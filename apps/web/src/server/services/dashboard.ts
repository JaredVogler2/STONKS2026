import "server-only";
import type { Db } from "../db";

/* Page-level read models for the owner dashboard. These only aggregate; they never expose another member's fills. */

type Json = Record<string, unknown>;
const iso = (d: unknown) => (d ? new Date(d as string).toISOString().replace(/\.\d{3}Z$/, "Z") : null);
const fix = (r: Json) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, v instanceof Date ? iso(v) : v]));

export interface CandidateRow {
  id: string;
  symbol: string;
  strategy_id: string;
  quality_score: number | null;
  gate_status: string;
  reason_codes: string[];
  asof_at: string;
  scan_id: string;
  signal_id: string | null;
  signal_state: string | null;
  tier: string | null;
  confidence_status: string | null;
  confidence_probability: number | null;
  reward_to_risk: string | null;
  horizon_max_sessions: number | null;
  sector: string | null;
  quote_feed: string | null;
  quote_received_at: string | null;
  quote_price: string | null;
}

/** Best candidates across the most recent `scans` scan runs (latest evaluation per symbol), joined to the published signal (if any) and the cached quote. */
export async function recentCandidates(db: Db, scans = 5, limit = 15): Promise<CandidateRow[]> {
  const rows = await db.query<Json>(
    `WITH recent AS (SELECT id FROM apex.scan_runs ORDER BY asof_at DESC LIMIT $1),
     latest AS (
       SELECT DISTINCT ON (c.symbol) c.* FROM apex.signal_candidates c JOIN recent r ON r.id = c.scan_run_id
       ORDER BY c.symbol, c.asof_at DESC, c.quality_score DESC NULLS LAST)
     SELECT c.id, c.symbol, c.strategy_id, c.quality_score::float8 AS quality_score, c.gate_status, c.reason_codes, c.asof_at, c.scan_run_id AS scan_id,
            s.id AS signal_id, h.state AS signal_state, s.immutable_snapshot->>'tier' AS tier, s.confidence_status,
            s.confidence_probability::float8 AS confidence_probability, s.immutable_snapshot->'levels'->>'reward_to_risk' AS reward_to_risk,
            s.horizon_max_sessions, COALESCE(s.immutable_snapshot->'risk'->>'sector', c.explanation_json->>'sector') AS sector,
            q.feed AS quote_feed, q.ts_received AS quote_received_at, q.price::text AS quote_price
     FROM latest c
     LEFT JOIN apex.signals s ON s.immutable_snapshot->>'candidate_id' = c.id AND s.revision = 1
     LEFT JOIN apex.signal_heads h ON h.signal_id = s.id
     LEFT JOIN apex.latest_quotes q ON q.symbol = c.symbol
     ORDER BY (c.gate_status = 'PASS') DESC, c.quality_score DESC NULLS LAST, c.asof_at DESC, c.symbol
     LIMIT $2`, [scans, limit]);
  return rows.map((r) => fix(r) as unknown as CandidateRow);
}

export interface SectorRow {
  sector: string;
  etf: string;
  ret_20: number | null;
  rank_20: number | null;
  rank_change_20: number | null;
  breadth_above_sma50: number | null;
  members: number;
  open_signals: number;
}

/** Sector strength as computed by the Pi at the latest scan (sector ETF returns + member breadth), plus APEX's own
 * open-signal crowding per sector. */
export async function sectorBoard(db: Db): Promise<{ asof_at: string | null; rows: SectorRow[] }> {
  const scan = await db.query<{ asof_at: Date; metrics_json: Json }>("SELECT asof_at, metrics_json FROM apex.scan_runs ORDER BY asof_at DESC LIMIT 1");
  const sectors = (scan[0]?.metrics_json?.sectors as Json[] | undefined) ?? [];
  const crowd = await db.query<{ sector: string; n: number }>(
    `SELECT s.immutable_snapshot->'risk'->>'sector' AS sector, count(*)::int AS n FROM apex.signal_heads h
     JOIN apex.signals s ON s.id = h.signal_id AND s.revision = h.current_revision WHERE h.state IN ('PUBLISHED','ACTIVE') GROUP BY 1`);
  const crowdBy = new Map(crowd.map((c) => [c.sector, c.n]));
  return {
    asof_at: iso(scan[0]?.asof_at),
    rows: sectors.map((s) => ({ ...(s as unknown as SectorRow), open_signals: crowdBy.get(String(s.sector)) ?? 0 }))
      .sort((a, b) => (b.ret_20 ?? -9) - (a.ret_20 ?? -9)),
  };
}

export interface TimelineItem { at: string; kind: string; title: string; detail: string | null; tone: "pos" | "neg" | "warn" | "info" | "neutral"; href: string | null }

/** Recent operational events in one stream: signal lifecycle, source outages, model changes, earnings vetoes. */
export async function eventTimeline(db: Db, limit = 16): Promise<TimelineItem[]> {
  const sig = await db.query<Json>(
    `SELECT e.event_type, e.event_at, e.cause, e.to_state, h.symbol, e.signal_id FROM apex.signal_events e
     JOIN apex.signal_heads h ON h.signal_id = e.signal_id
     WHERE e.event_type NOT IN ('CREATED','QUALIFIED') ORDER BY e.event_at DESC, e.recorded_at DESC LIMIT $1`, [limit]);
  const sec = await db.query<Json>("SELECT kind, route, detail, at FROM apex.security_events ORDER BY at DESC LIMIT 5");
  // model events come from the registry's own timestamps (replay-clock aware), not audit insert times
  const models = await db.query<Json>(
    `SELECT 'promoted' AS action, id AS entity_id, promoted_at AS at FROM apex.model_versions WHERE promoted_at IS NOT NULL
     UNION ALL SELECT 'registered', id, created_at FROM apex.model_versions ORDER BY 3 DESC LIMIT 5`);
  const outages = await db.query<Json>(
    `SELECT received_at, payload_json FROM apex.agent_heartbeats WHERE payload_json->'components'->'market_feed'->>'status' IN ('DOWN','DEGRADED','STALE')
     ORDER BY received_at DESC LIMIT 5`);
  const toneOf = (t: string): TimelineItem["tone"] =>
    t === "INVALIDATED" || t === "STOP_HIT" || t === "INTRADAY_STOP_TOUCH" ? "neg" : t === "TARGET_REACHED" || t === "INTRADAY_TARGET_TOUCH" ? "pos"
      : t === "PUBLISHED" || t === "ACTIVATED" ? "info" : t === "STALE_DATA" || t === "TIME_WARNING" ? "warn" : "neutral";
  const items: TimelineItem[] = [
    ...sig.map((e) => ({
      at: iso(e.event_at)!, kind: String(e.event_type), tone: toneOf(String(e.event_type)),
      title: `${e.symbol} · ${String(e.event_type).toLowerCase().replace(/_/g, " ")}`, detail: (e.cause as string) ?? null, href: `/signals/${e.signal_id}`,
    })),
    ...sec.map((s) => ({ at: iso(s.at)!, kind: "SECURITY", tone: "warn" as const, title: `Security: ${String(s.kind).replace(/_/g, " ")}`,
      detail: (s.route as string) ?? null, href: "/operations#security" })),
    ...models.map((m) => ({ at: iso(m.at)!, kind: "MODEL", tone: "info" as const, title: `Model ${String(m.action)}`,
      detail: String(m.entity_id ?? ""), href: "/models" })),
    ...outages.map((o) => ({ at: iso(o.received_at)!, kind: "SOURCE_OUTAGE", tone: "neg" as const, title: "Market feed degraded",
      detail: String(((o.payload_json as Json)?.components as Json | undefined)?.market_feed ? ((((o.payload_json as Json).components as Json).market_feed as Json).detail ?? "") : ""),
      href: "/operations" })),
  ];
  return items.sort((a, b) => b.at.localeCompare(a.at)).slice(0, limit);
}

export async function latestScans(db: Db, limit = 20): Promise<Json[]> {
  const rows = await db.query<Json>(
    `SELECT id, asof_at, universe_size, eligible_count, scanned_count, candidate_count, published_count, no_trade_reason, metrics_json->'vetoes' AS vetoes
     FROM apex.scan_runs ORDER BY asof_at DESC LIMIT $1`, [limit]);
  return rows.map(fix);
}

export async function ownerPositionCounts(db: Db, userId: string): Promise<{ open: number; watching: number }> {
  const rows = await db.asMember(userId, (sql) => sql.query<{ open: number; watching: number }>(
    `SELECT count(*) FILTER (WHERE state IN ('OPEN','PARTIALLY_CLOSED'))::int AS open, count(*) FILTER (WHERE state = 'WATCHING')::int AS watching
     FROM apex.positions`));
  return rows[0] ?? { open: 0, watching: 0 };
}

export interface AssessmentRow { position_id: string; asof_at: string; assessment: Json }

/** The owner's latest assessment per position (member-scoped session: RLS returns only their own rows). */
export async function latestAssessments(db: Db, userId: string): Promise<Map<string, AssessmentRow>> {
  const rows = await db.asMember(userId, (sql) => sql.query<{ position_id: string; asof_at: Date; assessment_json: Json }>(
    `SELECT DISTINCT ON (position_id) position_id, asof_at, assessment_json FROM apex.position_snapshots ORDER BY position_id, asof_at DESC`));
  return new Map(rows.map((r) => [r.position_id, { position_id: r.position_id, asof_at: iso(r.asof_at)!, assessment: r.assessment_json }]));
}

export async function assessmentHistory(db: Db, userId: string, positionId: string, limit = 12): Promise<AssessmentRow[]> {
  const rows = await db.asMember(userId, (sql) => sql.query<{ position_id: string; asof_at: Date; assessment_json: Json }>(
    "SELECT position_id, asof_at, assessment_json FROM apex.position_snapshots WHERE position_id = $1 ORDER BY asof_at DESC LIMIT $2", [positionId, limit]));
  return rows.map((r) => ({ position_id: r.position_id, asof_at: iso(r.asof_at)!, assessment: r.assessment_json }));
}

/** Pearson correlation of daily log returns over the last `window` common sessions, from APEX's stored bars. */
export async function returnCorrelations(db: Db, symbols: string[], window = 60): Promise<{ symbols: string[]; matrix: (number | null)[][]; sessions: number }> {
  const syms = [...new Set(symbols)].slice(0, 8);
  if (syms.length < 2) return { symbols: syms, matrix: [], sessions: 0 };
  const rows = await db.query<{ symbol: string; session_date: Date | string; close: string }>(
    `SELECT symbol, session_date, close FROM apex.daily_bars WHERE symbol = ANY($1) AND adjustment = 'split'
     AND session_date >= (SELECT max(session_date) FROM apex.daily_bars) - ($2::int * 2) ORDER BY session_date`, [syms, window]);
  const byDate = new Map<string, Map<string, number>>();
  for (const r of rows) {
    const d = typeof r.session_date === "string" ? r.session_date.slice(0, 10) : new Date(r.session_date).toISOString().slice(0, 10);
    if (!byDate.has(d)) byDate.set(d, new Map());
    byDate.get(d)!.set(r.symbol, Number(r.close));
  }
  const dates = [...byDate.keys()].sort().filter((d) => syms.every((s) => byDate.get(d)!.has(s))).slice(-(window + 1));
  const rets = syms.map((s) => dates.slice(1).map((d, i) => Math.log(byDate.get(d)!.get(s)! / byDate.get(dates[i]!)!.get(s)!)));
  const corr = (a: number[], b: number[]) => {
    const n = a.length;
    if (n < 20) return null;
    const ma = a.reduce((x, y) => x + y, 0) / n;
    const mb = b.reduce((x, y) => x + y, 0) / n;
    let num = 0, da = 0, dbb = 0;
    for (let i = 0; i < n; i++) {
      num += (a[i]! - ma) * (b[i]! - mb);
      da += (a[i]! - ma) ** 2;
      dbb += (b[i]! - mb) ** 2;
    }
    return da && dbb ? num / Math.sqrt(da * dbb) : null;
  };
  return { symbols: syms, sessions: Math.max(0, dates.length - 1), matrix: rets.map((a) => rets.map((b) => corr(a, b))) };
}

export async function memberCounts(db: Db): Promise<Json> {
  const r = await db.query<Json>(
    `SELECT (SELECT count(*)::int FROM apex.app_users WHERE role = 'member') AS members,
            (SELECT count(DISTINCT user_id)::int FROM apex.positions) AS members_with_positions,
            (SELECT count(*)::int FROM apex.positions WHERE state IN ('OPEN','PARTIALLY_CLOSED')) AS open_positions,
            (SELECT count(*)::int FROM apex.positions WHERE state = 'WATCHING') AS watching,
            (SELECT count(*)::int FROM apex.notification_outbox WHERE route_json->>'type' = 'DM' AND state = 'deferred') AS held_private,
            (SELECT count(*)::int FROM apex.notification_outbox WHERE route_json->>'type' = 'DM' AND state = 'sent') AS dm_sent`);
  return r[0] ?? {};
}

export const CATALYST_GROUPS: Record<string, string[]> = {
  filings: ["merger_acquisition", "buyback", "dilution_offering", "shelf_registration", "management_change", "contract_award", "regulatory_decision",
    "regulatory_action", "ownership_13d", "ownership_13g", "periodic_report", "current_report_other"],
  insider: ["insider_open_market_purchase", "insider_sale", "insider_award_or_exercise"],
  congress: ["political_disclosure"],
  earnings: ["earnings_release", "earnings_scheduled", "guidance_raise", "guidance_cut"],
  macro: ["macro_release", "sector_shock", "government_procurement"],
  news: ["news_other"],
};

export interface CatalystRow {
  id: string; symbol: string | null; catalyst_type: string; source: string; headline: string; canonical_url: string | null;
  event_at: string | null; published_at: string | null; first_seen_at: string; available_at: string; source_grade: string; direction: string;
  impact_score: number | null; actionable_age: string | null; cluster_id: string | null; reason_codes: string[]; regulatory_flags: string[];
  legal_classification: string; is_demo: boolean; parsed: Json; reaction_1d: number | null; reaction_5d: number | null;
}

/** Catalysts by availability time (never by a backdated event time) with the subsequent stored-bar reaction where bars exist. */
export async function catalystFeed(db: Db, opts: { group?: string; symbol?: string; source?: string; limit?: number }, now: Date): Promise<CatalystRow[]> {
  const where = ["c.available_at <= $1"];
  const params: unknown[] = [now];
  if (opts.group && CATALYST_GROUPS[opts.group]) {
    params.push(CATALYST_GROUPS[opts.group]);
    where.push(`c.catalyst_type = ANY($${params.length})`);
  }
  if (opts.symbol) {
    params.push(opts.symbol.toUpperCase().replace(/[^A-Z0-9.]/g, "").slice(0, 10));
    where.push(`c.symbol = $${params.length}`);
  }
  if (opts.source) {
    params.push(opts.source);
    where.push(`c.source = $${params.length}`);
  }
  params.push(Math.min(200, opts.limit ?? 80));
  const rows = await db.query<Json>(
    `SELECT c.id, c.symbol, c.catalyst_type, c.source, c.headline, c.canonical_url, c.event_at, c.published_at, c.first_seen_at, c.available_at, c.source_grade,
            c.direction, c.impact_score::float8 AS impact_score, c.actionable_age, c.cluster_id, c.reason_codes, c.regulatory_flags, c.legal_classification, c.is_demo,
            c.parsed_json AS parsed,
            (SELECT close FROM apex.daily_bars b WHERE b.symbol = c.symbol AND b.adjustment='split' AND b.session_date < (c.available_at AT TIME ZONE 'America/New_York')::date
               ORDER BY session_date DESC LIMIT 1) AS base_close,
            (SELECT array_agg(close ORDER BY session_date) FROM (SELECT close, session_date FROM apex.daily_bars b WHERE b.symbol = c.symbol AND b.adjustment='split'
               AND b.session_date >= (c.available_at AT TIME ZONE 'America/New_York')::date AND b.session_date <= ($1::timestamptz AT TIME ZONE 'America/New_York')::date
               ORDER BY session_date LIMIT 5) x) AS after_closes
     FROM apex.catalysts c WHERE ${where.join(" AND ")} ORDER BY c.available_at DESC LIMIT $${params.length}`, params);
  return rows.map((r) => {
    const base = r.base_close === null || r.base_close === undefined ? null : Number(r.base_close);
    const after = ((r.after_closes as unknown[] | null) ?? []).map(Number);
    const react = (n: number) => (base && after.length >= n ? Math.log(after[n - 1]! / base) : null);
    const rest = { ...r };
    delete rest.base_close;
    delete rest.after_closes;
    return { ...(fix(rest) as unknown as CatalystRow), reaction_1d: react(1), reaction_5d: react(5) };
  });
}

export async function catalystSources(db: Db, now: Date): Promise<{ source: string; last_seen: string | null; n_30d: number }[]> {
  const rows = await db.query<{ source: string; last_seen: Date | null; n: number }>(
    `SELECT source, max(first_seen_at) AS last_seen, count(*) FILTER (WHERE available_at > $1::timestamptz - interval '30 days')::int AS n
     FROM apex.catalysts WHERE available_at <= $1 GROUP BY source ORDER BY source`, [now]);
  return rows.map((r) => ({ source: r.source, last_seen: iso(r.last_seen), n_30d: r.n }));
}

/** Next occurrence (ET wall-clock) of a weekly/weekday schedule, for display only. GitHub may delay or skip runs. */
export function nextRunEt(now: Date, days: number[], hhmm: string): string {
  const fmt = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  const p = Object.fromEntries(fmt.formatToParts(now).map((x) => [x.type, x.value]));
  const today = `${p.year}-${p.month}-${p.day}`;
  const nowHm = `${p.hour === "24" ? "00" : p.hour}:${p.minute}`;
  for (let d = 0; d < 9; d++) {
    const date = new Date(`${today}T12:00:00Z`);
    date.setUTCDate(date.getUTCDate() + d);
    if (!days.includes(date.getUTCDay())) continue;
    if (d === 0 && nowHm >= hhmm) continue;
    return `${date.toISOString().slice(0, 10)} ${hhmm} ET`;
  }
  return "unknown";
}

export async function modelsPanel(db: Db, isDemo: boolean, now: Date): Promise<Json> {
  const versions = await db.query<Json>(
    `SELECT id, strategy_id, status, data_source, created_at, registered_at, promoted_at, promoted_by, validation_expires_at, rollback_parent, is_demo,
            artifact_size_bytes, artifact_sha256, signer_key_version, code_sha, feature_schema_version, promotion_report,
            manifest_json->'evaluation' AS evaluation, manifest_json->'training_window' AS training_window
     FROM apex.model_versions WHERE is_demo = $1 ORDER BY strategy_id, registered_at DESC`, [isDemo]);
  const activations = await db.query<Json>(
    `SELECT DISTINCT ON (a.agent_id) a.agent_id, a.model_id, a.status, a.detail, a.reported_at, d.device_name FROM apex.model_activations a
     LEFT JOIN apex.agent_devices d ON d.id = a.agent_id ORDER BY a.agent_id, a.reported_at DESC`);
  const recentActivations = await db.query<Json>("SELECT agent_id, model_id, status, detail, reported_at FROM apex.model_activations ORDER BY reported_at DESC LIMIT 10");
  const runs = await db.query<Json>(
    `SELECT id, workflow, trigger, github_run_id, status, outcome, started_at, finished_at, minutes_used, candidate_model_id, report_json->'decisions' AS decisions, is_demo
     FROM apex.training_runs ORDER BY started_at DESC LIMIT 25`);
  const month = await db.query<{ used: string | null; runs: number }>(
    "SELECT sum(minutes_used)::text AS used, count(*)::int AS runs FROM apex.training_runs WHERE started_at >= date_trunc('month', $1::timestamptz)", [now]);
  const lastBy = (wf: string, ok = false) => runs.find((r) => r.workflow === wf && (!ok || r.status === "succeeded")) ?? null;
  return {
    versions: versions.map(fix), activations: activations.map(fix), recent_activations: recentActivations.map(fix), runs: runs.map(fix),
    last_train: lastBy("train_weekly") ? fix(lastBy("train_weekly")!) : null,
    last_train_ok: lastBy("train_weekly", true) ? fix(lastBy("train_weekly", true)!) : null,
    last_local: lastBy("local_replay") ? fix(lastBy("local_replay")!) : null,
    last_drift: lastBy("drift") ? fix(lastBy("drift")!) : null,
    minutes_month: Number(month[0]?.used ?? 0), runs_month: month[0]?.runs ?? 0,
    next_train: nextRunEt(now, [1], "19:17"), next_drift: nextRunEt(now, [1, 2, 3, 4, 5], "20:23"),
  };
}

export async function deliveryOverview(db: Db, opts: { kind?: string; state?: string; limit?: number }): Promise<Json> {
  const where = ["route_json->>'type' = 'CHANNEL'"];
  const params: unknown[] = [];
  if (opts.kind) { params.push(opts.kind); where.push(`kind = $${params.length}`); }
  if (opts.state) { params.push(opts.state); where.push(`state = $${params.length}`); }
  params.push(Math.min(100, opts.limit ?? 40));
  const notices = await db.query<Json>(
    `SELECT id, event_key, kind, priority, state, created_at, source_event_at, sent_at, route_json->>'channel_key' AS channel, rendered_json, attempts, last_error, is_demo
     FROM apex.notification_outbox WHERE ${where.join(" AND ")} ORDER BY coalesce(source_event_at, created_at) DESC, created_at DESC LIMIT $${params.length}`, params);
  const byState = await db.query<Json>(
    `SELECT route_json->>'type' AS route, state, count(*)::int AS n FROM apex.notification_outbox GROUP BY 1, 2 ORDER BY 1, 2`);
  const byKind = await db.query<Json>(
    `SELECT kind, count(*)::int AS n, count(*) FILTER (WHERE state = 'sent')::int AS sent FROM apex.notification_outbox WHERE route_json->>'type' = 'CHANNEL'
     GROUP BY 1 ORDER BY 2 DESC`);
  const attempts = await db.query<Json>(
    `SELECT status, count(*)::int AS n, max(http_status) FILTER (WHERE status <> 'delivered') AS last_http FROM apex.delivery_attempts GROUP BY 1 ORDER BY 2 DESC`);
  return { notices: notices.map(fix), by_state: byState, by_kind: byKind, attempts };
}

export async function auditSearch(db: Db, q: string | undefined, limit = 80): Promise<{ audit: Json[]; history: Json[] }> {
  const term = q ? `%${q.replace(/[%_\\]/g, "").slice(0, 60)}%` : null;
  const audit = await db.query<Json>(
    `SELECT id, actor_type, actor_id, entity, entity_id, action, redacted_metadata_json, correlation_id, at FROM apex.audit_events
     ${term ? "WHERE action ILIKE $2 OR entity ILIKE $2 OR coalesce(entity_id,'') ILIKE $2" : ""} ORDER BY at DESC LIMIT $1`,
    term ? [limit, term] : [limit]);
  const history = await db.query<Json>(
    `SELECT key, version, redacted_change, actor_id, at FROM apex.settings_history ${term ? "WHERE key ILIKE $2" : ""} ORDER BY at DESC LIMIT $1`,
    term ? [30, term] : [30]);
  return { audit: audit.map(fix), history: history.map(fix) };
}
