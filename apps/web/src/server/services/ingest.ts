import "server-only";
import type { Db, Sql } from "../db";
import { HttpError } from "../http/responses";
import { assertContract } from "../http/validate";
import { audit } from "./audit";

/* Ingestion of authenticated agent payloads. Every write is idempotent (natural keys / dedupe keys) and the
 * transactional outbox row is committed in the SAME transaction as the event that caused it. */

type Json = Record<string, unknown>;

const ALLOWED_TRANSITIONS: Record<string, string[]> = {
  "": ["DISCOVERED"],
  DISCOVERED: ["EVALUATING", "DISCARDED"],
  EVALUATING: ["QUALIFIED", "DISCARDED", "NO_TRADE"],
  QUALIFIED: ["PUBLISHED", "DISCARDED", "NO_TRADE", "EXPIRED"],
  PUBLISHED: ["ACTIVE", "EXPIRED", "INVALIDATED"],
  ACTIVE: ["CLOSED", "INVALIDATED"],
};
const TERMINAL = new Set(["CLOSED", "DISCARDED", "NO_TRADE", "EXPIRED", "INVALIDATED"]);

async function enqueue(sql: Sql, intent: Json): Promise<boolean> {
  assertContract("NotificationIntent", intent);
  const route = intent.route as { type: string; user_id?: string };
  const rows = await sql.query(
    `INSERT INTO apex.notification_outbox(event_key, user_id, route_json, kind, priority, payload_json, source_event_at, is_demo)
     VALUES ($1, $2, $3::jsonb, $4, $5, $6::jsonb, $7, $8) ON CONFLICT (event_key) DO NOTHING RETURNING id`,
    [intent.event_key, route.type === "DM" ? route.user_id : null, JSON.stringify(route), intent.kind, intent.priority,
      JSON.stringify(intent), intent.source_event_at ?? null, Boolean(intent.is_demo)],
  );
  return rows.length > 0;
}

const INT32_MAX = 2_147_483_647;

/** `now` is the server receive time (wall clock live; the replay clock in DEMO). Skew is clamped to the int4 column range. */
export async function ingestHeartbeat(db: Db, agentId: string, hb: Json, now: Date = new Date()): Promise<{ skew_ms: number }> {
  assertContract("WorkerHeartbeat", hb);
  if (hb.agent_id !== agentId) throw new HttpError(403, "AGENT_MISMATCH", "heartbeat agent id does not match signer");
  const sent = new Date(String(hb.sent_at));
  const skew = Math.max(-INT32_MAX, Math.min(INT32_MAX, now.getTime() - sent.getTime()));
  await db.tx(async (sql) => {
    await sql.query("INSERT INTO apex.agent_heartbeats(agent_id, sent_at, received_at, clock_skew_ms, payload_json) VALUES ($1,$2,$3,$4,$5::jsonb)", [
      agentId, sent, now, skew, JSON.stringify(hb)]);
    await sql.query("UPDATE apex.agent_devices SET worker_version = $2, secret_version_ack = GREATEST(secret_version_ack, $3) WHERE id = $1", [
      agentId, String(hb.worker_version), Number(hb.secret_version_ack ?? 0)]);
  });
  return { skew_ms: skew };
}

export async function ingestObservations(db: Db, body: { observations?: Json[]; bars?: Json[] }): Promise<{ quotes: number; bars: number }> {
  const obs = (body.observations ?? []).slice(0, 500);
  const bars = (body.bars ?? []).slice(0, 5000);
  let q = 0;
  let b = 0;
  await db.tx(async (sql) => {
    for (const o of obs) {
      assertContract("MarketObservation", o);
      const prov = o.provenance as Json;
      await sql.query(
        `INSERT INTO apex.latest_quotes(symbol, observation_json, price, feed, latency_class, ts_exchange, ts_received, validation_level, is_demo)
         VALUES ($1,$2::jsonb,$3,$4,$5,$6,$7,$8,$9)
         ON CONFLICT (symbol) DO UPDATE SET observation_json = EXCLUDED.observation_json, price = EXCLUDED.price, feed = EXCLUDED.feed,
           latency_class = EXCLUDED.latency_class, ts_exchange = EXCLUDED.ts_exchange, ts_received = EXCLUDED.ts_received,
           validation_level = EXCLUDED.validation_level, is_demo = EXCLUDED.is_demo
         WHERE apex.latest_quotes.ts_received <= EXCLUDED.ts_received`,
        [o.symbol, JSON.stringify(o), o.price, prov.feed, prov.latency_class, prov.ts_exchange ?? null, prov.ts_received, o.validation_level,
          Boolean(prov.is_demo)],
      );
      q++;
    }
    for (const bar of bars) {
      assertContract("MarketBar", bar);
      const prov = bar.provenance as Json;
      if (bar.timeframe !== "1Day") continue; // intraday bars stay on the Pi (500 MB budget)
      await sql.query(
        `INSERT INTO apex.daily_bars(symbol, session_date, adjustment, feed, provider, open, high, low, close, volume, vwap, is_provisional, asof_at, is_demo)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
         ON CONFLICT (symbol, session_date, adjustment, feed) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
           close=EXCLUDED.close, volume=EXCLUDED.volume, vwap=EXCLUDED.vwap, is_provisional=EXCLUDED.is_provisional, asof_at=EXCLUDED.asof_at,
           ingested_at=now()`,
        [bar.symbol, bar.session_date, bar.adjustment, prov.feed, prov.provider, bar.open, bar.high, bar.low, bar.close, bar.volume,
          bar.vwap ?? null, bar.is_provisional, bar.asof_at ?? prov.ts_received, Boolean(prov.is_demo)],
      );
      b++;
    }
  });
  return { quotes: q, bars: b };
}

export async function ingestScanRun(db: Db, agentId: string, body: Json): Promise<{ candidates: number }> {
  const run = body.scan as Json;
  const cands = ((body.candidates as Json[]) ?? []).slice(0, 300);
  if (!run || typeof run.id !== "string") throw new HttpError(422, "CONTRACT_VIOLATION", "scan.id required");
  await db.tx(async (sql) => {
    await sql.query(
      `INSERT INTO apex.scan_runs(id, agent_id, started_at, finished_at, asof_at, universe_size, eligible_count, scanned_count, candidate_count,
         published_count, no_trade_reason, metrics_json, is_demo)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13) ON CONFLICT (id) DO NOTHING`,
      [run.id, agentId, run.started_at, run.finished_at ?? null, run.asof_at, run.universe_size ?? 0, run.eligible_count ?? 0, run.scanned_count ?? 0,
        cands.length, run.published_count ?? 0, run.no_trade_reason ?? null, JSON.stringify(run.metrics ?? {}), Boolean(run.is_demo)],
    );
    for (const c of cands) {
      assertContract("SignalCandidate", c);
      await sql.query(
        `INSERT INTO apex.signal_candidates(id, scan_run_id, symbol, created_at, asof_at, strategy_id, strategy_version, quality_score, gate_status,
           reason_codes, explanation_json, is_demo)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12) ON CONFLICT (id) DO NOTHING`,
        [c.id, run.id, c.symbol, c.created_at, c.asof_at, c.strategy_id, c.strategy_version, c.quality_score, c.gate_status, c.reason_codes,
          JSON.stringify({ evidence: c.evidence, group_scores: c.group_scores, regime: c.regime, feature_snapshot_hash: c.feature_snapshot_hash }),
          c.is_demo],
      );
    }
    if (body.regime) {
      const r = body.regime as Json;
      await sql.query(
        `INSERT INTO apex.market_regimes(asof_at, regime, state_probabilities, inputs_json, version, is_demo)
         VALUES ($1,$2,$3::jsonb,$4::jsonb,$5,$6) ON CONFLICT (asof_at, version, is_demo) DO NOTHING`,
        [run.asof_at, r.label ?? "unknown", JSON.stringify(r.probabilities ?? {}), JSON.stringify(r.inputs ?? {}), r.version ?? "regime-rules-1",
          Boolean(run.is_demo)],
      );
    }
    // raw feature values behind the best candidates' evidence (bounded; NULL = insufficient history, never zero-filled)
    for (const f of ((body.feature_snapshots as Json[] | undefined) ?? []).slice(0, 25)) {
      assertContract("FeatureSnapshot", f);
      if (Boolean(f.is_demo) !== Boolean(run.is_demo)) throw new HttpError(422, "MODE_MISMATCH", "feature snapshot mode must match the scan");
      await sql.query(
        `INSERT INTO apex.feature_snapshots(symbol, asof_at, session_date, feature_version, features_json, sources_json, integrity_hash, is_demo)
         VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8) ON CONFLICT (symbol, asof_at, feature_version) DO NOTHING`,
        [f.symbol, f.asof_at, f.session_date ?? null, f.feature_version, JSON.stringify(f.features), JSON.stringify(f.sources), f.integrity_hash,
          Boolean(f.is_demo)],
      );
    }
  });
  return { candidates: cands.length };
}

export interface SignalPublish {
  signal: Json;
  tier: "HIGH_CONVICTION" | "RESEARCH_SETUP";
  events: Json[];
  intent?: Json;
}

function checkSignalRow(sig: Json, mode: string): void {
  const isDemo = Boolean(sig.is_demo);
  if (isDemo !== (sig.release_mode === "DEMO")) throw new HttpError(422, "MODE_MISMATCH", "is_demo must match release_mode");
  if (mode === "DEMO" ? !isDemo : isDemo) {
    // a live deployment never accepts demo signals, and a DEMO deployment never accepts live ones
    throw new HttpError(422, "MODE_MISMATCH", `server is in ${mode} mode; signal release_mode ${String(sig.release_mode)} rejected`);
  }
  const conf = sig.confidence as Json;
  if (conf.status === "UNVALIDATED" && conf.p_target_first !== undefined) {
    throw new HttpError(422, "UNVALIDATED_WITH_PROBABILITY", "unvalidated signals cannot carry a probability");
  }
}

export async function ingestSignal(db: Db, agentId: string, body: SignalPublish, mode: string): Promise<{ created: boolean }> {
  const sig = assertContract<Json>("TradeSignal", body.signal);
  checkSignalRow(sig, mode);
  const lv = sig.levels as Json;
  const hz = sig.horizon as Json;
  const conf = sig.confidence as Json;
  const quote = sig.quote as Json;
  const prov = quote.provenance as Json;
  return db.tx(async (sql) => {
    const existing = await sql.query<{ immutable_hash: string }>("SELECT immutable_hash FROM apex.signals WHERE id=$1 AND revision=$2", [sig.id, sig.revision]);
    if (existing[0]) {
      if (existing[0].immutable_hash !== sig.immutable_hash) {
        throw new HttpError(409, "IMMUTABLE_REVISION", "a different body for this signal revision already exists; publish a new revision");
      }
      return { created: false };
    }
    await sql.query(
      `INSERT INTO apex.signals(id, revision, symbol, side, state_at_revision, entry_min, entry_max, stop_level, target_primary, target_secondary,
         horizon_min_sessions, horizon_max_sessions, entry_expires_at, created_at, asof_at, published_at, feed, latency_class, strategy_id,
         model_version, policy_version, quality_score, confidence_status, confidence_probability, confidence_interval, confidence_label,
         release_mode, immutable_snapshot, immutable_hash, is_demo)
       VALUES ($1,$2,$3,'LONG',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24::jsonb,$25,$26,$27::jsonb,$28,$29)`,
      [sig.id, sig.revision, sig.symbol, sig.state, lv.entry_min, lv.entry_max, lv.stop_level, lv.target_primary, lv.target_secondary ?? null,
        hz.min_sessions, hz.max_sessions, hz.entry_expires_at, sig.created_at, sig.asof_at, sig.published_at ?? null, prov.feed, prov.latency_class,
        sig.strategy_id, sig.model_version, sig.policy_version, sig.quality_score, conf.status, conf.p_target_first ?? null,
        conf.interval_80 ? JSON.stringify(conf.interval_80) : null, conf.label, sig.release_mode, JSON.stringify({ ...sig, tier: body.tier }),
        sig.immutable_hash, sig.is_demo],
    );
    await sql.query(
      `INSERT INTO apex.signal_heads(signal_id, symbol, current_revision, state, is_demo, published_at) VALUES ($1,$2,$3,'DISCOVERED',$4,NULL)
       ON CONFLICT (signal_id) DO UPDATE SET current_revision = EXCLUDED.current_revision, updated_at = now()`,
      [sig.id, sig.symbol, sig.revision, sig.is_demo],
    );
    for (const ev of body.events) await applySignalEvent(sql, ev);
    if (body.intent) await enqueue(sql, body.intent);
    await audit(sql, { actorType: "agent", actorId: agentId, entity: "signals", entityId: String(sig.id), action: "signal.published",
      metadata: { tier: body.tier, symbol: sig.symbol, confidence: conf.status } });
    return { created: true };
  });
}

async function applySignalEvent(sql: Sql, ev: Json): Promise<boolean> {
  assertContract("SignalEvent", ev);
  const head = await sql.query<{ state: string }>("SELECT state FROM apex.signal_heads WHERE signal_id = $1 FOR UPDATE", [ev.signal_id]);
  if (!head[0]) throw new HttpError(404, "UNKNOWN_SIGNAL", "signal not found");
  const dup = await sql.query("SELECT 1 FROM apex.signal_events WHERE dedupe_key = $1", [ev.dedupe_key]);
  if (dup.length) return false;
  const prior = await sql.query<{ n: number }>("SELECT count(*)::int AS n FROM apex.signal_events WHERE signal_id = $1", [ev.signal_id]);
  const cur = prior[0]!.n === 0 ? "" : head[0].state; // a fresh head only accepts the creation event
  const from = (ev.from_state as string | null) ?? "";
  const to = String(ev.to_state);
  const annotation = from !== "" && from === to;
  if (annotation) {
    if (TERMINAL.has(cur)) throw new HttpError(409, "TERMINAL_SIGNAL", "terminal signals are immutable");
    if (cur !== from) throw new HttpError(409, "STATE_CONFLICT", `signal is ${cur || "(new)"}, annotation expects ${from}`);
  } else {
    if (from !== cur) throw new HttpError(409, "STATE_CONFLICT", `signal is ${cur || "(new)"}, event expects ${from || "(new)"}`);
    if (!(ALLOWED_TRANSITIONS[from] ?? []).includes(to)) throw new HttpError(422, "ILLEGAL_TRANSITION", `${from || "(new)"} -> ${to}`);
  }
  await sql.query(
    `INSERT INTO apex.signal_events(signal_id, event_type, event_at, from_state, to_state, cause, evidence_asof_at, payload_json, dedupe_key)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9)`,
    [ev.signal_id, ev.event_type, ev.event_at, ev.from_state ?? null, to, ev.cause, ev.evidence_asof_at ?? null, JSON.stringify(ev.payload ?? {}),
      ev.dedupe_key],
  );
  if (!annotation) {
    const outcome = (ev.payload as Json | undefined)?.outcome ?? null;
    await sql.query(
      `UPDATE apex.signal_heads SET state = $2, outcome = COALESCE($3, outcome),
         published_at = CASE WHEN $2 = 'PUBLISHED' THEN $4::timestamptz ELSE published_at END, updated_at = now() WHERE signal_id = $1`,
      [ev.signal_id, to, outcome, ev.event_at],
    );
  }
  return true;
}

export async function ingestSignalEvents(db: Db, body: { events: Json[]; intents?: Json[] }): Promise<{ applied: number }> {
  const intents = new Map((body.intents ?? []).map((i) => [String(i.event_key).replace(/^SIGEVT:/, ""), i]));
  return db.tx(async (sql) => {
    let applied = 0;
    for (const ev of body.events.slice(0, 50)) {
      if (await applySignalEvent(sql, ev)) {
        applied++;
        const intent = intents.get(String(ev.dedupe_key));
        if (intent) await enqueue(sql, intent);
      }
    }
    return { applied };
  });
}

export async function ingestCatalysts(db: Db, items: Json[]): Promise<{ inserted: number }> {
  let inserted = 0;
  await db.tx(async (sql) => {
    for (const c of items.slice(0, 500)) {
      assertContract("Catalyst", c);
      const r = await sql.query(
        `INSERT INTO apex.catalysts(id, symbol, cik, catalyst_type, source, event_at, published_at, first_seen_at, available_at, canonical_url, headline,
           source_grade, direction, entity_confidence, extraction_confidence, impact_score, actionable_age, cluster_id, revision_of, reason_codes,
           regulatory_flags, parsed_json, source_hash, legal_classification, parser_version, is_demo)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22::jsonb,$23,$24,$25,$26)
         ON CONFLICT DO NOTHING RETURNING id`,
        [c.id, c.symbol ?? null, c.cik ?? null, c.catalyst_type, c.source, c.event_at ?? null, c.published_at ?? null, c.first_seen_at, c.available_at,
          c.canonical_url ?? null, c.headline, c.source_grade, c.direction, c.entity_confidence, c.extraction_confidence, c.impact_score ?? null,
          c.actionable_age ?? null, c.cluster_id ?? null, c.revision_of ?? null, c.reason_codes, c.regulatory_flags ?? [],
          JSON.stringify(c.parsed ?? {}), c.source_hash, c.legal_classification, c.parser_version, c.is_demo],
      );
      inserted += r.length;
    }
  });
  return { inserted };
}

export async function ingestAssessments(db: Db, items: { user_id: string; assessment: Json; assessment_key: string; intent?: Json }[]): Promise<number> {
  let n = 0;
  for (const it of items.slice(0, 200)) {
    const a = assertContract<Json>("PositionAssessment", it.assessment);
    await db.asMember(it.user_id, async (sql) => {
      const pos = await sql.query<{ id: string }>("SELECT id FROM apex.positions WHERE id = $1", [a.position_id]);
      if (!pos[0]) return; // not this member's position: silently ignored, never cross-written
      const r = await sql.query(
        `INSERT INTO apex.position_snapshots(user_id, position_id, symbol, asof_at, assessment_key, avg_cost, total_quantity, unrealized_pnl,
           source_age_seconds, assessment_json) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb) ON CONFLICT (position_id, assessment_key) DO NOTHING RETURNING id`,
        [it.user_id, a.position_id, a.symbol, a.asof_at, it.assessment_key, a.avg_cost ?? "0", a.total_quantity ?? "0", a.unrealized_pnl ?? null,
          a.quote_age_seconds ?? null, JSON.stringify(a)],
      );
      if (r.length && it.intent) {
        const route = it.intent.route as Json;
        if (route.type !== "DM" || route.user_id !== it.user_id) throw new HttpError(422, "ROUTE_MISMATCH", "position updates are private DMs to the owner");
        await enqueue(sql, it.intent);
      }
      n += r.length;
    });
  }
  return n;
}

export async function enqueueIntent(db: Db, intent: Json): Promise<boolean> {
  const route = intent.route as Json;
  if (route.type === "DM") throw new HttpError(422, "USE_ASSESSMENT_ROUTE", "DM intents are only accepted with their assessment");
  return db.tx((sql) => enqueue(sql, intent));
}

/* ---------------------------------- outbox delivery ---------------------------------- */

export async function leaseOutbox(db: Db, agentId: string, limit: number): Promise<{ outbox_id: string; attempt: number; intent: Json; discord_user_id: string | null; dm_opt_in: boolean }[]> {
  return db.tx(async (sql) => {
    // owner-suppressed signals: their queued notices are never handed to the bot
    await sql.query(
      `UPDATE apex.notification_outbox o SET state = 'suppressed' WHERE o.state = 'pending' AND EXISTS (
         SELECT 1 FROM apex.signal_events e WHERE e.event_type = 'ADMIN_REVIEW' AND e.payload_json->>'suppress_alerts' = 'true'
           AND o.event_key LIKE '%' || e.signal_id || '%')`);
    // owner STOP switch (fail closed): only system alerts are handed out while it is on
    const ks = await sql.query<{ stopped: boolean }>("SELECT (value_json->>'stopped')::boolean AS stopped FROM apex.settings WHERE key = 'alerts.kill_switch'");
    const stopped = ks[0]?.stopped === true;
    const rows = await sql.query<{ id: string; attempts: number; payload_json: Json; user_id: string | null }>(
      `SELECT id, attempts, payload_json, user_id FROM apex.notification_outbox
       WHERE (state = 'pending' OR (state = 'leased' AND lease_until < now())) AND next_attempt_at <= now()
         AND (NOT $2::boolean OR kind = 'SYSTEM_ALERT')
       ORDER BY CASE priority WHEN 'URGENT' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'NORMAL' THEN 2 ELSE 3 END, created_at
       LIMIT $1 FOR UPDATE SKIP LOCKED`,
      [Math.min(50, Math.max(1, limit)), stopped],
    );
    const out = [];
    for (const r of rows) {
      await sql.query("UPDATE apex.notification_outbox SET state='leased', lease_until = now() + interval '2 minutes', leased_by=$2 WHERE id=$1", [r.id, agentId]);
      let discord: string | null = null;
      let optIn = false;
      if (r.user_id) {
        const u = await sql.query<{ discord_user_id: string | null; dm_opt_in: boolean; disabled_at: Date | null }>(
          "SELECT discord_user_id, dm_opt_in, disabled_at FROM apex.app_users WHERE id=$1", [r.user_id]);
        discord = u[0]?.disabled_at ? null : (u[0]?.discord_user_id ?? null);
        optIn = Boolean(u[0]?.dm_opt_in);
      }
      out.push({ outbox_id: r.id, attempt: r.attempts + 1, intent: r.payload_json, discord_user_id: discord, dm_opt_in: optIn });
    }
    return out;
  });
}

export interface AckInput {
  outbox_id: string;
  status: "delivered" | "failed" | "rate_limited" | "dm_blocked";
  http_status?: number;
  retry_after_seconds?: number;
  discord_message_id?: string;
  error_code?: string;
  /** the message as posted to a shared channel (ignored for DMs) */
  rendered?: Json | null;
}

export async function ackOutbox(db: Db, agentId: string, ack: AckInput, maxAttempts = 8): Promise<string> {
  return db.tx(async (sql) => {
    const rows = await sql.query<{ state: string; attempts: number; leased_by: string | null }>(
      "SELECT state, attempts, leased_by FROM apex.notification_outbox WHERE id = $1 FOR UPDATE", [ack.outbox_id]);
    const r = rows[0];
    if (!r) throw new HttpError(404, "NOT_FOUND", "outbox item not found");
    if (r.state === "sent") return "sent"; // duplicate ack: idempotent
    if (r.leased_by && r.leased_by !== agentId) throw new HttpError(409, "LEASE_CONFLICT", "item leased by another agent");
    const attempts = r.attempts + 1;
    await sql.query(
      `INSERT INTO apex.delivery_attempts(outbox_id, status, http_status, rate_limit_until, error_code)
       VALUES ($1,$2,$3, CASE WHEN $4::int IS NULL THEN NULL ELSE now() + make_interval(secs => $4::int) END, $5)`,
      [ack.outbox_id, ack.status, ack.http_status ?? null, ack.retry_after_seconds ?? null, ack.error_code?.slice(0, 48) ?? null],
    );
    if (ack.status === "delivered") {
      await sql.query("UPDATE apex.notification_outbox SET state='sent', attempts=$2, sent_at=now(), discord_message_id=$3, lease_until=NULL WHERE id=$1",
        [ack.outbox_id, attempts, ack.discord_message_id ?? null]);
      // keep what was actually posted to a SHARED channel for the dashboard history (never stored for DMs)
      if (ack.rendered && typeof ack.rendered === "object" && JSON.stringify(ack.rendered).length <= 12_000) {
        await sql.query("UPDATE apex.notification_outbox SET rendered_json = $2::jsonb WHERE id = $1 AND route_json->>'type' = 'CHANNEL'",
          [ack.outbox_id, JSON.stringify(ack.rendered)]);
      }
      return "sent";
    }
    if (ack.status === "dm_blocked") {
      // never re-routed to a public channel; shown privately next time the member interacts
      await sql.query("UPDATE apex.notification_outbox SET state='deferred', attempts=$2, last_error='DM_BLOCKED', lease_until=NULL WHERE id=$1",
        [ack.outbox_id, attempts]);
      return "deferred";
    }
    if (attempts >= maxAttempts) {
      await sql.query("UPDATE apex.notification_outbox SET state='dead_letter', attempts=$2, last_error=$3, lease_until=NULL WHERE id=$1",
        [ack.outbox_id, attempts, (ack.error_code ?? "failed").slice(0, 200)]);
      return "dead_letter";
    }
    const backoff = ack.retry_after_seconds ?? Math.min(900, 2 ** attempts);
    await sql.query(
      `UPDATE apex.notification_outbox SET state='pending', attempts=$2, next_attempt_at = now() + make_interval(secs => $3), last_error=$4,
         lease_until=NULL WHERE id=$1`,
      [ack.outbox_id, attempts, backoff, (ack.error_code ?? ack.status).slice(0, 200)],
    );
    return "pending";
  });
}

/** Ack by event key (DEMO seed replay and Pi restarts that lost the outbox id). */
export async function ackOutboxByEventKey(db: Db, agentId: string, eventKey: string, status: "delivered" | "dm_blocked",
  rendered: Json | null): Promise<string | null> {
  const rows = await db.query<{ id: string; route_json: Json }>("SELECT id, route_json FROM apex.notification_outbox WHERE event_key = $1", [eventKey]);
  const row = rows[0];
  if (!row) return null;
  await db.query("UPDATE apex.notification_outbox SET leased_by = $2 WHERE id = $1 AND leased_by IS NULL", [row.id, agentId]);
  const res = await ackOutbox(db, agentId, { outbox_id: row.id, status, discord_message_id: status === "delivered" ? "demo" : undefined });
  if (rendered && row.route_json.type === "CHANNEL") {
    await db.query("UPDATE apex.notification_outbox SET rendered_json = $2::jsonb WHERE id = $1", [row.id, JSON.stringify(rendered)]);
  }
  return res;
}

export async function releaseDeferredForMember(db: Db, userId: string): Promise<Json[]> {
  return db.asMember(userId, async (sql) => {
    const rows = await sql.query<{ id: string; payload_json: Json }>(
      "SELECT id, payload_json FROM apex.notification_outbox WHERE user_id = $1 AND state = 'deferred' ORDER BY created_at LIMIT 10", [userId]);
    return rows.map((r) => ({ outbox_id: r.id, intent: r.payload_json }));
  });
}
