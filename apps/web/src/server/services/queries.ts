import "server-only";
import type { Db } from "../db";

/* Read models for the owner dashboard. Personal fills never appear here except through the owner's OWN member-scoped
 * session (positions page); other members are visible only as aggregate counts. */

type Json = Record<string, unknown>;
const iso = (d: Date | string | null | undefined) => (d ? new Date(d).toISOString().replace(/\.\d{3}Z$/, "Z") : null);

export interface SignalRow {
  id: string;
  symbol: string;
  state: string;
  outcome: string | null;
  tier: string;
  strategy_id: string;
  quality_score: number;
  confidence_status: string;
  confidence_probability: number | null;
  entry_min: string;
  entry_max: string;
  stop_level: string;
  target_primary: string;
  reward_to_risk: string;
  horizon_max_sessions: number;
  entry_expires_at: string;
  asof_at: string;
  published_at: string | null;
  feed: string;
  latency_class: string;
  sector: string;
  is_demo: boolean;
  release_mode: string;
}

export interface SignalFilter {
  state?: string;
  tier?: string;
  strategy?: string;
  confidence?: string;
  sector?: string;
  minQuality?: number;
  q?: string;
  limit?: number;
  offset?: number;
}

export async function listSignals(db: Db, f: SignalFilter = {}): Promise<{ rows: SignalRow[]; total: number }> {
  const where: string[] = [];
  const params: unknown[] = [];
  const add = (sql: string, v: unknown) => {
    params.push(v);
    where.push(sql.replace("?", `$${params.length}`));
  };
  if (f.state) add("h.state = ?", f.state);
  if (f.tier) add("s.immutable_snapshot->>'tier' = ?", f.tier);
  if (f.strategy) add("s.strategy_id = ?", f.strategy);
  if (f.confidence) add("s.confidence_status = ?", f.confidence);
  if (f.sector) add("s.immutable_snapshot->'risk'->>'sector' = ?", f.sector);
  if (f.minQuality !== undefined) add("s.quality_score >= ?", f.minQuality);
  if (f.q) add("s.symbol LIKE ?", `${f.q.toUpperCase().replace(/[^A-Z0-9.]/g, "")}%`);
  const w = where.length ? `WHERE ${where.join(" AND ")}` : "";
  const total = await db.query<{ n: number }>(
    `SELECT count(*)::int AS n FROM apex.signal_heads h JOIN apex.signals s ON s.id = h.signal_id AND s.revision = h.current_revision ${w}`, params);
  params.push(Math.min(200, f.limit ?? 50), f.offset ?? 0);
  const rows = await db.query<Json>(
    `SELECT s.id, s.symbol, h.state, h.outcome, s.immutable_snapshot->>'tier' AS tier, s.strategy_id, s.quality_score::float8 AS quality_score,
            s.confidence_status, s.confidence_probability::float8 AS confidence_probability, s.entry_min::text, s.entry_max::text, s.stop_level::text,
            s.target_primary::text, s.immutable_snapshot->'levels'->>'reward_to_risk' AS reward_to_risk, s.horizon_max_sessions, s.entry_expires_at,
            s.asof_at, h.published_at, s.feed, s.latency_class, s.immutable_snapshot->'risk'->>'sector' AS sector, s.is_demo, s.release_mode
     FROM apex.signal_heads h JOIN apex.signals s ON s.id = h.signal_id AND s.revision = h.current_revision ${w}
     ORDER BY s.asof_at DESC, s.id LIMIT $${params.length - 1} OFFSET $${params.length}`, params);
  return {
    total: total[0]?.n ?? 0,
    rows: rows.map((r) => ({ ...r, entry_expires_at: iso(r.entry_expires_at as Date)!, asof_at: iso(r.asof_at as Date)!,
      published_at: iso(r.published_at as Date), entry_min: fmtNum(r.entry_min), entry_max: fmtNum(r.entry_max), stop_level: fmtNum(r.stop_level),
      target_primary: fmtNum(r.target_primary) })) as SignalRow[],
  };
}

export function fmtNum(v: unknown): string {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(2) : String(v);
}

export async function signalDetail(db: Db, id: string): Promise<{ snapshot: Json; state: string; outcome: string | null; events: Json[]; revisions: number } | null> {
  if (!/^sig_[0-9a-f]{24}$/.test(id)) return null;
  const rows = await db.query<{ immutable_snapshot: Json; state: string; outcome: string | null; current_revision: number }>(
    `SELECT s.immutable_snapshot, h.state, h.outcome, h.current_revision FROM apex.signal_heads h
     JOIN apex.signals s ON s.id = h.signal_id AND s.revision = h.current_revision WHERE h.signal_id = $1`, [id]);
  if (!rows[0]) return null;
  const events = await db.query<Json>(
    "SELECT event_type, event_at, from_state, to_state, cause, evidence_asof_at, payload_json, recorded_at FROM apex.signal_events WHERE signal_id=$1 ORDER BY event_at, recorded_at",
    [id]);
  return { snapshot: rows[0].immutable_snapshot, state: rows[0].state, outcome: rows[0].outcome, revisions: rows[0].current_revision,
    events: events.map((e) => ({ ...e, event_at: iso(e.event_at as Date), evidence_asof_at: iso(e.evidence_asof_at as Date), recorded_at: iso(e.recorded_at as Date) })) };
}

export async function barsFor(db: Db, symbol: string, limit = 160): Promise<{ date: string; open: number; high: number; low: number; close: number; volume: number; feed: string }[]> {
  const rows = await db.query<{ session_date: Date | string; open: string; high: string; low: string; close: string; volume: string | number; feed: string }>(
    `SELECT session_date, open, high, low, close, volume, feed FROM apex.daily_bars WHERE symbol=$1 AND adjustment='split'
     ORDER BY session_date DESC LIMIT $2`, [symbol, limit]);
  return rows.reverse().map((r) => ({ date: typeof r.session_date === "string" ? r.session_date.slice(0, 10) : new Date(r.session_date).toISOString().slice(0, 10),
    open: Number(r.open), high: Number(r.high), low: Number(r.low), close: Number(r.close), volume: Number(r.volume), feed: r.feed }));
}

export async function overview(db: Db): Promise<Json> {
  const scan = await db.query<Json>(
    `SELECT id, asof_at, finished_at, universe_size, eligible_count, scanned_count, candidate_count, published_count, no_trade_reason, metrics_json, is_demo
     FROM apex.scan_runs ORDER BY asof_at DESC LIMIT 1`);
  const s = scan[0];
  const cands = s ? await db.query<Json>(
    `SELECT id, symbol, strategy_id, quality_score::float8 AS quality_score, gate_status, reason_codes, explanation_json FROM apex.signal_candidates
     WHERE scan_run_id = $1 ORDER BY quality_score DESC NULLS LAST, symbol LIMIT 12`, [s.id]) : [];
  const regime = await db.query<Json>("SELECT asof_at, regime, state_probabilities, version FROM apex.market_regimes ORDER BY asof_at DESC LIMIT 1");
  const regimeHistory = await db.query<Json>("SELECT asof_at, regime FROM apex.market_regimes ORDER BY asof_at DESC LIMIT 40");
  const counts = await db.query<Json>(
    `SELECT count(*) FILTER (WHERE state IN ('PUBLISHED','ACTIVE'))::int AS open_signals,
            count(*) FILTER (WHERE state = 'CLOSED')::int AS closed_signals,
            count(*) FILTER (WHERE state IN ('EXPIRED','INVALIDATED'))::int AS expired_or_invalidated,
            count(*)::int AS total_signals FROM apex.signal_heads`);
  const positions = await db.query<Json>(
    `SELECT count(*) FILTER (WHERE state IN ('OPEN','PARTIALLY_CLOSED'))::int AS open_positions,
            count(*) FILTER (WHERE state = 'WATCHING')::int AS watching, count(DISTINCT user_id)::int AS members_with_positions FROM apex.positions`);
  const hb = await db.query<{ payload_json: Json; received_at: Date }>("SELECT payload_json, received_at FROM apex.agent_heartbeats ORDER BY received_at DESC LIMIT 1");
  const feed = ((hb[0]?.payload_json?.components as Json | undefined)?.market_feed as Json | undefined)?.metrics as Json | undefined;
  const timeline = await db.query<Json>(
    `SELECT e.event_type, e.event_at, e.cause, e.to_state, h.symbol, e.signal_id FROM apex.signal_events e JOIN apex.signal_heads h ON h.signal_id = e.signal_id
     WHERE e.event_type NOT IN ('CREATED','QUALIFIED') ORDER BY e.event_at DESC, e.recorded_at DESC LIMIT 14`);
  const champion = await db.query<Json>("SELECT id, strategy_id, promoted_at, validation_expires_at, is_demo FROM apex.model_versions WHERE status='champion' ORDER BY promoted_at DESC LIMIT 3");
  const publishedToday = s ? Number(s.published_count ?? 0) : 0;
  return {
    scan: s ? { ...s, asof_at: iso(s.asof_at as Date), finished_at: iso(s.finished_at as Date) } : null,
    candidates: cands,
    regime: regime[0] ? { ...regime[0], asof_at: iso(regime[0].asof_at as Date) } : null,
    regime_history: regimeHistory.map((r) => ({ ...r, asof_at: iso(r.asof_at as Date) })).reverse(),
    counts: counts[0] ?? {}, positions: positions[0] ?? {}, published_today: publishedToday,
    feed: feed ?? null, heartbeat_at: iso(hb[0]?.received_at),
    timeline: timeline.map((t) => ({ ...t, event_at: iso(t.event_at as Date) })),
    champions: champion.map((c) => ({ ...c, promoted_at: iso(c.promoted_at as Date), validation_expires_at: iso(c.validation_expires_at as Date) })),
  };
}

export async function sectorStrength(db: Db): Promise<{ sector: string; signals: number; avg_quality: number }[]> {
  const rows = await db.query<{ sector: string; signals: number; avg_quality: number }>(
    `SELECT COALESCE(s.immutable_snapshot->'risk'->>'sector','unknown') AS sector, count(*)::int AS signals, avg(s.quality_score)::float8 AS avg_quality
     FROM apex.signals s GROUP BY 1 ORDER BY 2 DESC LIMIT 12`);
  return rows;
}

export async function catalysts(db: Db, opts: { type?: string; symbol?: string; limit?: number } = {}): Promise<Json[]> {
  const params: unknown[] = [];
  const where: string[] = [];
  if (opts.type) {
    params.push(opts.type);
    where.push(`catalyst_type = $${params.length}`);
  }
  if (opts.symbol) {
    params.push(opts.symbol.toUpperCase());
    where.push(`symbol = $${params.length}`);
  }
  params.push(Math.min(200, opts.limit ?? 100));
  const rows = await db.query<Json>(
    `SELECT id, symbol, catalyst_type, source, event_at, published_at, first_seen_at, available_at, canonical_url, headline, source_grade, direction,
            entity_confidence::float8 AS entity_confidence, extraction_confidence::float8 AS extraction_confidence, impact_score::float8 AS impact_score,
            actionable_age, cluster_id, reason_codes, is_demo
     FROM apex.catalysts ${where.length ? `WHERE ${where.join(" AND ")}` : ""} ORDER BY available_at DESC LIMIT $${params.length}`, params);
  return rows.map((r) => ({ ...r, event_at: iso(r.event_at as Date), published_at: iso(r.published_at as Date), first_seen_at: iso(r.first_seen_at as Date),
    available_at: iso(r.available_at as Date) }));
}

export async function research(db: Db): Promise<{ backtests: Json[]; models: Json[]; training: Json[]; activations: Json[] }> {
  const backtests = await db.query<Json>(
    "SELECT id, strategy_id, data_source, status, started_at, completed_at, metrics_json, bias_warnings, is_demo FROM apex.backtest_runs ORDER BY strategy_id");
  const models = await db.query<Json>(
    `SELECT id, strategy_id, status, data_source, evaluation_json, validation_expires_at, created_at, promoted_at, promoted_by, rollback_parent, is_demo,
            artifact_size_bytes, artifact_sha256, signer_key_version, code_sha, feature_schema_version, promotion_report
     FROM apex.model_versions ORDER BY registered_at DESC LIMIT 30`);
  const training = await db.query<Json>(
    "SELECT id, workflow, trigger, github_run_id, status, outcome, started_at, finished_at, minutes_used, report_json, is_demo FROM apex.training_runs ORDER BY started_at DESC LIMIT 20");
  const activations = await db.query<Json>("SELECT agent_id, model_id, status, detail, reported_at FROM apex.model_activations ORDER BY reported_at DESC LIMIT 20");
  const fix = (r: Json) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, v instanceof Date ? iso(v) : v]));
  return { backtests: backtests.map(fix), models: models.map(fix), training: training.map(fix), activations: activations.map(fix) };
}

export async function operations(db: Db): Promise<Json> {
  const fix = (r: Json) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, v instanceof Date ? iso(v) : v]));
  const devices = await db.query<Json>(
    "SELECT id, device_name, status, fingerprint, worker_version, secret_version_ack, created_at, approved_at, last_seen_at, last_clock_skew_ms, revoked_at FROM apex.agent_devices ORDER BY created_at");
  const heartbeats = await db.query<Json>("SELECT agent_id, received_at, clock_skew_ms, payload_json FROM apex.agent_heartbeats ORDER BY received_at DESC LIMIT 1");
  const outbox = await db.query<Json>(
    "SELECT state, kind, count(*)::int AS n FROM apex.notification_outbox GROUP BY state, kind ORDER BY state, kind");
  const dead = await db.query<Json>(
    "SELECT id, kind, event_key, attempts, last_error, created_at FROM apex.notification_outbox WHERE state='dead_letter' ORDER BY created_at DESC LIMIT 20");
  const security = await db.query<Json>("SELECT kind, agent_id, route, detail, at FROM apex.security_events ORDER BY at DESC LIMIT 30");
  const audit = await db.query<Json>(
    "SELECT actor_type, actor_id, entity, entity_id, action, redacted_metadata_json, at FROM apex.audit_events ORDER BY at DESC LIMIT 60");
  const quota = await db.query<Json>("SELECT provider, day, requests, limit_per_day FROM apex.quota_usage ORDER BY day DESC LIMIT 14");
  const jobs = await db.query<Json>("SELECT job_name, started_at, finished_at, success, last_error FROM apex.job_runs ORDER BY started_at DESC LIMIT 20");
  const size = await db.query<{ bytes: string }>("SELECT apex.database_size_bytes()::text AS bytes");
  const tables = await db.query<Json>(
    `SELECT relname AS table, n_live_tup::int AS rows FROM pg_stat_user_tables WHERE schemaname='apex' ORDER BY n_live_tup DESC LIMIT 12`);
  return {
    devices: devices.map(fix), heartbeat: heartbeats[0] ? fix(heartbeats[0]) : null, outbox, dead_letters: dead.map(fix),
    security_events: security.map(fix), audit: audit.map(fix), quota: quota.map(fix), jobs: jobs.map(fix),
    db_bytes: Number(size[0]?.bytes ?? 0), tables,
  };
}

export async function alertHistory(db: Db, limit = 60): Promise<Json[]> {
  const rows = await db.query<Json>(
    `SELECT id, kind, priority, state, created_at, sent_at, route_json->>'channel_key' AS channel, rendered_json, attempts, last_error, is_demo
     FROM apex.notification_outbox WHERE route_json->>'type' = 'CHANNEL' ORDER BY created_at DESC LIMIT $1`, [limit]);
  const dm = await db.query<Json>(
    `SELECT state, count(*)::int AS n FROM apex.notification_outbox WHERE route_json->>'type' = 'DM' GROUP BY state`);
  return [...rows.map((r) => ({ ...r, created_at: iso(r.created_at as Date), sent_at: iso(r.sent_at as Date) })), { _dm_summary: dm }];
}

export async function memberAggregate(db: Db): Promise<Json> {
  const r = await db.query<Json>(
    `SELECT count(*) FILTER (WHERE role='member')::int AS members, count(*) FILTER (WHERE role='member' AND disabled_at IS NULL)::int AS active,
            count(*) FILTER (WHERE dm_opt_in AND role='member')::int AS dm_opt_in FROM apex.app_users`);
  return r[0] ?? {};
}

export async function performance(db: Db): Promise<Json> {
  // Measured on ALL published signals (no-fills, expiries and invalidations included); theoretical entries only.
  const rows = await db.query<Json>(
    `SELECT h.state, h.outcome, count(*)::int AS n FROM apex.signal_heads h WHERE h.published_at IS NOT NULL GROUP BY 1,2 ORDER BY 1,2`);
  const closed = await db.query<{ r: number | null }>(
    `SELECT (e.payload_json->>'r_multiple_modeled')::float8 AS r FROM apex.signal_events e WHERE e.to_state = 'CLOSED' AND e.payload_json ? 'r_multiple_modeled'`);
  const rs = closed.map((c) => c.r).filter((x): x is number => x !== null);
  return {
    by_state: rows, closed_n: rs.length,
    mean_r_modeled: rs.length ? Number((rs.reduce((a, b) => a + b, 0) / rs.length).toFixed(3)) : null,
    target_first: rows.filter((r) => r.outcome === "TARGET").reduce((a, r) => a + Number(r.n), 0),
    stop_first: rows.filter((r) => r.outcome === "STOP").reduce((a, r) => a + Number(r.n), 0),
    note: "Small-sample, theoretical (not member fills). No win-rate claim is made without calibrated out-of-sample evidence.",
  };
}
