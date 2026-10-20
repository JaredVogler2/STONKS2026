import "server-only";
import type { Db } from "../db";
import { HttpError } from "../http/responses";
import { audit } from "./audit";

/* Signals Explorer: server-side filtering/sorting/pagination over immutable signal revisions, the full detail view,
 * and the owner's review actions. Sort columns are whitelisted (never interpolated from input). */

type Json = Record<string, unknown>;
const iso = (d: unknown) => (d ? new Date(d as string).toISOString().replace(/\.\d{3}Z$/, "Z") : null);
const fix = (r: Json) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, v instanceof Date ? iso(v) : v]));

export interface ExplorerFilter {
  mode?: "ALL" | "LIVE" | "DEMO";
  state?: string;
  tier?: string;
  strategy?: string;
  confidence?: string;
  sector?: string;
  minQuality?: number;
  maxHorizon?: number;
  catalyst?: "with" | "without";
  tradability?: string;
  window?: "1d" | "5d" | "20d" | "60d" | "all";
  q?: string;
  sort?: keyof typeof SORTS;
  dir?: "asc" | "desc";
  page?: number;
  pageSize?: number;
}

const SORTS = {
  asof: "s.asof_at",
  quality: "s.quality_score",
  rr: "(s.immutable_snapshot->'levels'->>'reward_to_risk')::numeric",
  probability: "s.confidence_probability",
  symbol: "s.symbol",
  horizon: "s.horizon_max_sessions",
} as const;

const STATES = new Set(["PUBLISHED", "ACTIVE", "CLOSED", "EXPIRED", "INVALIDATED", "DISCARDED", "NO_TRADE", "QUALIFIED"]);
const WINDOW_DAYS: Record<string, number> = { "1d": 1, "5d": 7, "20d": 28, "60d": 84 };

export interface ExplorerRow {
  id: string; symbol: string; company_name: string | null; state: string; outcome: string | null; tier: string; strategy_id: string;
  quality_score: number; confidence_status: string; confidence_probability: number | null; entry_min: string; entry_max: string; stop_level: string;
  target_primary: string; reward_to_risk: string | null; horizon_max_sessions: number; entry_expires_at: string; asof_at: string;
  published_at: string | null; feed: string; latency_class: string; sector: string | null; tradability_status: string | null;
  catalysts: number; is_demo: boolean; release_mode: string; revision: number;
}

export function parseFilter(sp: URLSearchParams | Record<string, string | string[] | undefined>): ExplorerFilter {
  const get = (k: string): string | undefined => {
    const v = sp instanceof URLSearchParams ? sp.get(k) ?? undefined : sp[k];
    return Array.isArray(v) ? v[0] : v || undefined;
  };
  const num = (k: string) => {
    const v = get(k);
    const n = v === undefined ? NaN : Number(v);
    return Number.isFinite(n) ? n : undefined;
  };
  const sort = get("sort");
  return {
    mode: (["ALL", "LIVE", "DEMO"].includes(get("mode") ?? "") ? get("mode") : undefined) as ExplorerFilter["mode"],
    state: get("state"), tier: get("tier"), strategy: get("strategy"), confidence: get("confidence"), sector: get("sector"),
    minQuality: num("min_quality"), maxHorizon: num("max_horizon"),
    catalyst: (["with", "without"].includes(get("catalyst") ?? "") ? get("catalyst") : undefined) as ExplorerFilter["catalyst"],
    tradability: get("tradability"),
    window: (["1d", "5d", "20d", "60d", "all"].includes(get("window") ?? "") ? get("window") : undefined) as ExplorerFilter["window"],
    q: get("q"), sort: sort && sort in SORTS ? (sort as ExplorerFilter["sort"]) : undefined, dir: get("dir") === "asc" ? "asc" : get("dir") === "desc" ? "desc" : undefined,
    page: num("page"), pageSize: num("page_size"),
  };
}

export async function exploreSignals(db: Db, f: ExplorerFilter, now: Date): Promise<{ rows: ExplorerRow[]; total: number; page: number; pageSize: number }> {
  const where: string[] = [];
  const params: unknown[] = [];
  const add = (sql: string, ...v: unknown[]) => {
    let s = sql;
    for (const x of v) {
      params.push(x);
      s = s.replace("?", `$${params.length}`);
    }
    where.push(s);
  };
  if (f.mode === "LIVE") add("NOT s.is_demo");
  if (f.mode === "DEMO") add("s.is_demo");
  if (f.state) {
    const states = f.state.split(",").map((x) => x.trim().toUpperCase()).filter((x) => STATES.has(x));
    if (states.length) add("h.state = ANY(?)", states);
  }
  if (f.tier) add("s.immutable_snapshot->>'tier' = ?", f.tier);
  if (f.strategy) add("s.strategy_id = ?", f.strategy);
  if (f.confidence) add("s.confidence_status = ?", f.confidence);
  if (f.sector) add("s.immutable_snapshot->'risk'->>'sector' = ?", f.sector);
  if (f.minQuality !== undefined) add("s.quality_score >= ?", f.minQuality);
  if (f.maxHorizon !== undefined) add("s.horizon_max_sessions <= ?", f.maxHorizon);
  if (f.catalyst === "with") add("jsonb_array_length(COALESCE(s.immutable_snapshot->'thesis'->'catalysts','[]'::jsonb)) > 0");
  if (f.catalyst === "without") add("jsonb_array_length(COALESCE(s.immutable_snapshot->'thesis'->'catalysts','[]'::jsonb)) = 0");
  if (f.tradability) add("s.immutable_snapshot->'risk'->>'tradability_status' = ?", f.tradability);
  if (f.window && f.window !== "all") add("s.asof_at >= ?", new Date(now.getTime() - WINDOW_DAYS[f.window]! * 86400_000));
  add("s.asof_at <= ?", now); // never show signals "from the future" of the replay clock
  if (f.q) add("s.symbol LIKE ?", `${f.q.toUpperCase().replace(/[^A-Z0-9.]/g, "").slice(0, 10)}%`);
  const w = where.length ? `WHERE ${where.join(" AND ")}` : "";
  const from = `FROM apex.signal_heads h JOIN apex.signals s ON s.id = h.signal_id AND s.revision = h.current_revision ${w}`;
  const total = (await db.query<{ n: number }>(`SELECT count(*)::int AS n ${from}`, params))[0]?.n ?? 0;
  const pageSize = Math.min(100, Math.max(10, Math.floor(f.pageSize ?? 25)));
  const page = Math.max(1, Math.floor(f.page ?? 1));
  const order = `${SORTS[f.sort ?? "asof"]} ${f.dir === "asc" ? "ASC" : "DESC"} NULLS LAST, s.id`;
  const rows = await db.query<Json>(
    `SELECT s.id, s.symbol, s.immutable_snapshot->>'company_name' AS company_name, h.state, h.outcome, s.immutable_snapshot->>'tier' AS tier,
            s.strategy_id, s.quality_score::float8 AS quality_score, s.confidence_status, s.confidence_probability::float8 AS confidence_probability,
            to_char(s.entry_min, 'FM999999990.00') AS entry_min, to_char(s.entry_max, 'FM999999990.00') AS entry_max,
            to_char(s.stop_level, 'FM999999990.00') AS stop_level, to_char(s.target_primary, 'FM999999990.00') AS target_primary,
            s.immutable_snapshot->'levels'->>'reward_to_risk' AS reward_to_risk, s.horizon_max_sessions, s.entry_expires_at, s.asof_at, h.published_at,
            s.feed, s.latency_class, s.immutable_snapshot->'risk'->>'sector' AS sector, s.immutable_snapshot->'risk'->>'tradability_status' AS tradability_status,
            jsonb_array_length(COALESCE(s.immutable_snapshot->'thesis'->'catalysts','[]'::jsonb))::int AS catalysts, s.is_demo, s.release_mode, s.revision
     ${from} ORDER BY ${order} LIMIT ${pageSize} OFFSET ${(page - 1) * pageSize}`, params);
  return { rows: rows.map((r) => fix(r) as unknown as ExplorerRow), total, page, pageSize };
}

export async function explorerOptions(db: Db): Promise<{ strategies: string[]; sectors: string[] }> {
  const st = await db.query<{ v: string }>("SELECT DISTINCT strategy_id AS v FROM apex.signals ORDER BY 1");
  const se = await db.query<{ v: string }>("SELECT DISTINCT immutable_snapshot->'risk'->>'sector' AS v FROM apex.signals WHERE immutable_snapshot->'risk'->>'sector' IS NOT NULL ORDER BY 1");
  return { strategies: st.map((r) => r.v), sectors: se.map((r) => r.v) };
}

const CSV_COLUMNS: (keyof ExplorerRow)[] = ["id", "symbol", "state", "outcome", "tier", "strategy_id", "quality_score", "confidence_status",
  "confidence_probability", "entry_min", "entry_max", "stop_level", "target_primary", "reward_to_risk", "horizon_max_sessions", "asof_at", "published_at",
  "entry_expires_at", "feed", "latency_class", "sector", "tradability_status", "release_mode"];

function csvCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  let s = String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`; // spreadsheet formula-injection guard
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** CSV of the filtered signal list (public signal fields only; never any member's fills). */
export async function exportSignalsCsv(db: Db, f: ExplorerFilter, now: Date): Promise<string> {
  const lines = [CSV_COLUMNS.join(",")];
  for (let page = 1; page <= 20; page++) {
    const r = await exploreSignals(db, { ...f, page, pageSize: 100 }, now);
    for (const row of r.rows) lines.push(CSV_COLUMNS.map((c) => csvCell(row[c])).join(","));
    if (page * 100 >= r.total) break;
  }
  return `${lines.join("\n")}\n`;
}

export interface SignalFull {
  snapshot: Json;
  state: string;
  outcome: string | null;
  events: Json[];
  revisions: Json[];
  candidate: Json | null;
  features: { features: Record<string, number | null>; asof_at: string; integrity_hash: string } | null;
  quote: Json | null;
  model: Json | null;
  catalysts: Json[];
  review: { suppressed: boolean; notes: Json[] };
}

export async function signalFull(db: Db, id: string): Promise<SignalFull | null> {
  if (!/^sig_[0-9a-f]{24}$/.test(id)) return null;
  const head = await db.query<{ state: string; outcome: string | null; current_revision: number }>(
    "SELECT state, outcome, current_revision FROM apex.signal_heads WHERE signal_id=$1", [id]);
  if (!head[0]) return null;
  const revs = await db.query<Json>(
    `SELECT revision, state_at_revision, quality_score::float8 AS quality_score, confidence_status, confidence_probability::float8 AS confidence_probability,
            entry_min::text, entry_max::text, stop_level::text, target_primary::text, asof_at, immutable_hash, immutable_snapshot
     FROM apex.signals WHERE id=$1 ORDER BY revision`, [id]);
  const current = revs.find((r) => Number(r.revision) === head[0]!.current_revision) ?? revs[revs.length - 1]!;
  const snapshot = current.immutable_snapshot as Json;
  const events = await db.query<Json>(
    "SELECT event_type, event_at, from_state, to_state, cause, evidence_asof_at, payload_json, recorded_at FROM apex.signal_events WHERE signal_id=$1 ORDER BY event_at, recorded_at",
    [id]);
  const cand = snapshot.candidate_id
    ? await db.query<Json>("SELECT id, quality_score::float8 AS quality_score, gate_status, reason_codes, explanation_json, asof_at FROM apex.signal_candidates WHERE id=$1",
      [snapshot.candidate_id])
    : [];
  const feats = await db.query<{ features_json: Record<string, number | null>; asof_at: Date; integrity_hash: string }>(
    "SELECT features_json, asof_at, integrity_hash FROM apex.feature_snapshots WHERE symbol=$1 AND asof_at=$2 ORDER BY feature_version DESC LIMIT 1",
    [snapshot.symbol, snapshot.asof_at]);
  const quote = await db.query<Json>("SELECT price::text, feed, latency_class, ts_exchange, ts_received, validation_level FROM apex.latest_quotes WHERE symbol=$1",
    [snapshot.symbol]);
  const model = snapshot.model_version && snapshot.model_version !== "none"
    ? await db.query<Json>("SELECT id, status, evaluation_json, validation_expires_at, is_demo FROM apex.model_versions WHERE id=$1", [snapshot.model_version])
    : [];
  const cats = await db.query<Json>(
    `SELECT id, catalyst_type, source, headline, canonical_url, available_at, direction, impact_score::float8 AS impact_score, source_grade FROM apex.catalysts
     WHERE symbol=$1 AND available_at <= $2::timestamptz + interval '30 days' AND available_at >= $2::timestamptz - interval '45 days' ORDER BY available_at DESC LIMIT 12`,
    [snapshot.symbol, snapshot.asof_at]);
  const reviews = events.filter((e) => e.event_type === "ADMIN_REVIEW");
  return {
    snapshot, state: head[0].state, outcome: head[0].outcome,
    events: events.map(fix),
    revisions: revs.map((r) => {
      const copy = { ...r };
      delete copy.immutable_snapshot;
      return fix(copy);
    }),
    candidate: cand[0] ? fix(cand[0]) : null,
    features: feats[0] ? { features: feats[0].features_json, asof_at: iso(feats[0].asof_at)!, integrity_hash: feats[0].integrity_hash } : null,
    quote: quote[0] ? fix(quote[0]) : null,
    model: model[0] ? fix(model[0]) : null,
    catalysts: cats.map(fix),
    review: { suppressed: reviews.some((r) => (r.payload_json as Json)?.suppress_alerts === true), notes: reviews.map(fix) },
  };
}

const TERMINAL = new Set(["CLOSED", "DISCARDED", "NO_TRADE", "EXPIRED", "INVALIDATED"]);

/**
 * Owner review annotation: records an ADMIN_REVIEW event (no state change) and, if asked, suppresses any further Discord
 * notices for this signal (pending outbox rows are marked `suppressed`; new ones are suppressed when leased).
 * It can never raise confidence: there is no path from here to a calibrated probability.
 */
export async function reviewSignal(db: Db, ownerId: string, id: string, input: { note: string; suppress: boolean }, now: Date): Promise<{ suppressed_pending: number }> {
  if (!/^sig_[0-9a-f]{24}$/.test(id)) throw new HttpError(404, "NOT_FOUND", "signal not found");
  const note = input.note.replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, 280);
  if (note.length < 3) throw new HttpError(422, "NOTE_REQUIRED", "write a short reason (3+ characters) for the audit trail");
  return db.tx(async (sql) => {
    const head = await sql.query<{ state: string }>("SELECT state FROM apex.signal_heads WHERE signal_id=$1 FOR UPDATE", [id]);
    if (!head[0]) throw new HttpError(404, "NOT_FOUND", "signal not found");
    if (TERMINAL.has(head[0].state)) throw new HttpError(409, "TERMINAL_SIGNAL", "terminal signals are immutable; the record stays as published");
    const key = `${id}:ADMIN_REVIEW:${now.getTime().toString(16)}${Math.random().toString(16).slice(2, 10)}`;
    await sql.query(
      `INSERT INTO apex.signal_events(signal_id, event_type, event_at, from_state, to_state, cause, payload_json, dedupe_key)
       VALUES ($1,'ADMIN_REVIEW',$2,$3,$3,$4,$5::jsonb,$6)`,
      [id, now, head[0].state, `owner review: ${note}`, JSON.stringify({ suppress_alerts: input.suppress, by: "owner" }), key]);
    let n = 0;
    if (input.suppress) {
      const r = await sql.query("UPDATE apex.notification_outbox SET state='suppressed' WHERE state IN ('pending','deferred') AND event_key LIKE $1 RETURNING id",
        [`%${id}%`]);
      n = r.length;
    }
    await audit(sql, { actorType: "owner", actorId: ownerId, entity: "signals", entityId: id, action: input.suppress ? "signal.suppressed" : "signal.reviewed",
      metadata: { suppressed_pending: n } });
    return { suppressed_pending: n };
  });
}
