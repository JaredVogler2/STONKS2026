import "server-only";
import type { Db, Sql } from "../db";
import { HttpError } from "../http/responses";
import {
  apply, avgCost, dec, fmt, LedgerError, parseFill, replay, type Correction, type EventType, type Fill, type LedgerEvent,
  type LedgerState,
} from "../domain/ledger";

export interface PositionEventInput {
  client_idempotency_key: string;
  event_type: EventType;
  position_id?: string;
  symbol?: string;
  signal_id?: string;
  fill?: { price: string; quantity: string; fees: string; fill_at: string };
  notes?: string;
  correction?: { target_event_id: string; void: boolean; replacement?: { price: string; quantity: string; fees: string; fill_at: string }; reason?: string };
  source: "discord" | "dashboard" | "replay" | "starter_import";
  discord_interaction_id?: string;
  account_label?: string;
}

export interface RecordResult {
  status: "persisted" | "duplicate";
  event_id: string;
  position: PositionSummaryDto;
  warnings: string[];
}

export interface PositionSummaryDto {
  position_id: string;
  symbol: string;
  signal_id: string | null;
  state: string;
  total_quantity: string;
  avg_cost: string;
  realized_pnl: string;
  fees_total: string;
  cost_method: "FIFO";
  opened_at: string;
  closed_at?: string;
  off_plan: boolean;
  off_plan_reasons: string[];
  lots: { lot_id: string; fill_at: string; fill_price: string; quantity_initial: string; quantity_remaining: string; fees: string }[];
  event_count: number;
}

interface EventRow {
  id: string;
  seq: number;
  event_type: EventType;
  price: string | null;
  quantity: string | null;
  fees: string | null;
  event_at: Date | null;
  correction_json: { target_event_id: string; void: boolean; replacement?: { price: string; quantity: string; fees: string; fill_at: string } } | null;
}

const PUBLISHED = new Set(["PUBLISHED", "ACTIVE", "CLOSED", "EXPIRED", "INVALIDATED"]);
const iso = (d: Date) => d.toISOString().replace(/\.000Z$/, "Z");

function toLedger(rows: EventRow[]): LedgerEvent[] {
  return rows.map((r) => {
    const fill: Fill | null = r.price && r.quantity && r.event_at
      ? { price: dec(r.price), quantity: dec(r.quantity), fees: dec(r.fees ?? "0"), fillAt: new Date(r.event_at) }
      : null;
    let correction: Correction | null = null;
    if (r.correction_json) {
      const c = r.correction_json;
      correction = {
        targetEventId: c.target_event_id,
        void: c.void,
        replacement: c.replacement
          ? { price: dec(c.replacement.price), quantity: dec(c.replacement.quantity), fees: dec(c.replacement.fees), fillAt: new Date(c.replacement.fill_at) }
          : null,
      };
    }
    return { eventId: r.id, seq: Number(r.seq), eventType: r.event_type, fill, correction };
  });
}

interface SignalLevels {
  entry_min: string;
  entry_max: string;
  entry_expires_at: string;
  published_at: string | null;
  state: string;
  symbol: string;
}

async function signalFor(sql: Sql, signalId: string | null): Promise<SignalLevels | null> {
  if (!signalId) return null;
  const rows = await sql.query<{ snap: { levels: { entry_min: string; entry_max: string }; horizon: { entry_expires_at: string }; published_at?: string; symbol: string }; state: string }>(
    `SELECT s.immutable_snapshot AS snap, h.state FROM apex.signal_heads h
     JOIN apex.signals s ON s.id = h.signal_id AND s.revision = h.current_revision WHERE h.signal_id = $1`,
    [signalId],
  );
  const r = rows[0];
  if (!r) return null;
  return { entry_min: r.snap.levels.entry_min, entry_max: r.snap.levels.entry_max, entry_expires_at: r.snap.horizon.entry_expires_at,
    published_at: r.snap.published_at ?? null, state: r.state, symbol: r.snap.symbol };
}

export function offPlanReasons(st: LedgerState, sig: SignalLevels | null): string[] {
  if (!sig || !st.lots.length) return [];
  const first = [...st.lots].sort((a, b) => a.fillAt.getTime() - b.fillAt.getTime() || a.seq - b.seq)[0]!;
  const out: string[] = [];
  if (first.price.lt(dec(sig.entry_min)) || first.price.gt(dec(sig.entry_max))) out.push("ENTRY_OUTSIDE_SIGNAL_RANGE");
  if (first.fillAt.getTime() > new Date(sig.entry_expires_at).getTime()) out.push("ENTRY_AFTER_SIGNAL_EXPIRY");
  if (sig.published_at && first.fillAt.getTime() < new Date(sig.published_at).getTime()) out.push("ENTRY_BEFORE_SIGNAL_PUBLISHED");
  return out;
}

function summary(pos: { id: string; symbol: string; signal_id: string | null; created_at: Date }, st: LedgerState, off: string[], count: number): PositionSummaryDto {
  const out: PositionSummaryDto = {
    position_id: pos.id, symbol: pos.symbol, signal_id: pos.signal_id, state: st.state, total_quantity: fmt(st.totalQuantity),
    avg_cost: fmt(avgCost(st)), realized_pnl: fmt(st.realizedPnl), fees_total: fmt(st.feesTotal), cost_method: "FIFO",
    opened_at: iso(st.openedAt ?? new Date(pos.created_at)), off_plan: off.length > 0, off_plan_reasons: off,
    lots: st.lots.map((l) => ({ lot_id: l.lotId, fill_at: iso(l.fillAt), fill_price: fmt(l.price), quantity_initial: fmt(l.quantityInitial),
      quantity_remaining: fmt(l.quantityRemaining), fees: fmt(l.fees) })),
    event_count: count,
  };
  if (st.state === "CLOSED" && st.closedAt) out.closed_at = iso(st.closedAt);
  return out;
}

async function loadPosition(sql: Sql, positionId: string, lock: boolean) {
  const rows = await sql.query<{ id: string; user_id: string; account_id: string; symbol: string; signal_id: string | null; created_at: Date; is_demo: boolean }>(
    `SELECT id, user_id, account_id, symbol, signal_id, created_at, is_demo FROM apex.positions WHERE id = $1${lock ? " FOR UPDATE" : ""}`,
    [positionId],
  );
  return rows[0] ?? null;
}

async function events(sql: Sql, positionId: string): Promise<EventRow[]> {
  return sql.query<EventRow & Record<string, unknown>>(
    "SELECT id, seq, event_type, price, quantity, fees, event_at, correction_json FROM apex.position_events WHERE position_id = $1 ORDER BY seq",
    [positionId],
  );
}

async function rebuildProjection(sql: Sql, pos: { id: string; user_id: string; account_id: string; symbol: string; signal_id: string | null }, st: LedgerState,
  off: string[], count: number, source: string): Promise<void> {
  await sql.query("DELETE FROM apex.position_lots WHERE position_id = $1", [pos.id]);
  for (const l of st.lots) {
    await sql.query(
      `INSERT INTO apex.position_lots(id, user_id, position_id, account_id, signal_id, symbol, fill_at, fill_price, quantity_initial,
         quantity_remaining, fees, source, revision) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)`,
      [l.lotId, pos.user_id, pos.id, pos.account_id, pos.signal_id, pos.symbol, l.fillAt, fmt(l.price), fmt(l.quantityInitial),
        fmt(l.quantityRemaining), fmt(l.fees), source, count],
    );
  }
  await sql.query(
    `UPDATE apex.positions SET state=$2, total_quantity=$3, avg_cost=$4, realized_pnl=$5, fees_total=$6, opened_at=$7, closed_at=$8,
       off_plan=$9, off_plan_reasons=$10, event_count=$11 WHERE id=$1`,
    [pos.id, st.state, fmt(st.totalQuantity), fmt(avgCost(st)), fmt(st.realizedPnl), fmt(st.feesTotal), st.openedAt,
      st.state === "CLOSED" ? st.closedAt : null, off.length > 0, off, count],
  );
}

function ledgerToHttp(e: unknown): never {
  if (e instanceof LedgerError) throw new HttpError(422, e.code, e.message);
  throw e;
}

/** Record one member ledger event. Runs entirely as `apex_member` with RLS scoped to `userId`. */
export async function recordPositionEvent(db: Db, userId: string, ev: PositionEventInput, now: Date, isDemo: boolean): Promise<RecordResult> {
  const attempt = () =>
    db.asMember(userId, async (sql) => {
      const dup = await sql.query<{ id: string; position_id: string }>(
        "SELECT id, position_id FROM apex.position_events WHERE user_id = $1 AND client_idempotency_key = $2",
        [userId, ev.client_idempotency_key],
      );
      if (dup[0]) {
        const pos = (await loadPosition(sql, dup[0].position_id, false))!;
        const rows = await events(sql, pos.id);
        const st = replay(toLedger(rows));
        return { status: "duplicate" as const, event_id: dup[0].id, position: summary(pos, st, offPlanReasons(st, await signalFor(sql, pos.signal_id)), rows.length), warnings: [] };
      }
      let positionId = ev.position_id;
      if (!positionId) {
        if (ev.event_type !== "OPEN" && ev.event_type !== "WATCH") throw new HttpError(422, "POSITION_REQUIRED", "this action needs one of your positions");
        let symbol = ev.symbol;
        if (ev.signal_id) {
          const sig = await signalFor(sql, ev.signal_id);
          if (!sig || !PUBLISHED.has(sig.state)) throw new HttpError(422, "UNKNOWN_SIGNAL", "that signal id is not a published APEX signal");
          if (symbol && symbol !== sig.symbol) throw new HttpError(422, "SYMBOL_MISMATCH", `signal is for ${sig.symbol}, not ${symbol}`);
          symbol = sig.symbol;
        }
        if (!symbol) throw new HttpError(422, "SYMBOL_REQUIRED", "a symbol is required");
        const label = ev.account_label ?? "self-reported";
        // DO NOTHING + re-select: members hold INSERT/SELECT on accounts, not UPDATE
        await sql.query("INSERT INTO apex.position_accounts(user_id, account_label) VALUES ($1, $2) ON CONFLICT (user_id, account_label) DO NOTHING",
          [userId, label]);
        const acct = await sql.query<{ id: string }>("SELECT id FROM apex.position_accounts WHERE user_id = $1 AND account_label = $2", [userId, label]);
        const created = await sql.query<{ id: string }>(
          "INSERT INTO apex.positions(user_id, account_id, symbol, signal_id, state, is_demo) VALUES ($1,$2,$3,$4,'WATCHING',$5) RETURNING id",
          [userId, acct[0]!.id, symbol, ev.signal_id ?? null, isDemo],
        );
        positionId = created[0]!.id;
      }
      const pos = await loadPosition(sql, positionId, true);
      if (!pos) throw new HttpError(404, "NOT_FOUND", "position not found"); // includes other members' ids (RLS)
      const rows = await events(sql, pos.id);
      const existing = toLedger(rows);
      let fill: Fill | null = null;
      let corr: Correction | null = null;
      try {
        if (ev.fill) fill = parseFill(ev.fill, now);
        if (ev.correction) {
          corr = { targetEventId: ev.correction.target_event_id, void: ev.correction.void,
            replacement: ev.correction.replacement ? parseFill(ev.correction.replacement, now) : null };
        }
        if (ev.event_type === "CLOSE" && fill && existing.length) {
          const cur = replay(existing);
          if (!fill.quantity.eq(cur.totalQuantity)) {
            throw new LedgerError("CLOSE_QUANTITY_MISMATCH", `close sells all ${fmt(cur.totalQuantity)} shares; use a partial exit for fewer`);
          }
        }
      } catch (e) {
        ledgerToHttp(e);
      }
      const seq = rows.length ? Number(rows[rows.length - 1]!.seq) + 1 : 1;
      const idRow = await sql.query<{ id: string }>("SELECT gen_random_uuid() AS id");
      const eventId = idRow[0]!.id;
      let st: LedgerState;
      try {
        st = apply(existing, { eventId, seq, eventType: ev.event_type, fill, correction: corr });
      } catch (e) {
        ledgerToHttp(e);
      }
      await sql.query(
        `INSERT INTO apex.position_events(id, user_id, position_id, seq, event_type, price, quantity, fees, event_at, correction_json, notes,
           source, client_idempotency_key, discord_interaction_id)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12,$13,$14)`,
        [eventId, userId, pos.id, seq, ev.event_type, fill ? fmt(fill.price) : null, fill ? fmt(fill.quantity) : null, fill ? fmt(fill.fees) : null,
          fill ? fill.fillAt : null, ev.correction ? JSON.stringify({ target_event_id: ev.correction.target_event_id, void: ev.correction.void,
            replacement: ev.correction.replacement, reason: ev.correction.reason }) : null, ev.notes?.slice(0, 280) ?? null, ev.source,
          ev.client_idempotency_key, ev.discord_interaction_id ?? null],
      );
      const sig = await signalFor(sql, pos.signal_id);
      const off = offPlanReasons(st!, sig);
      await rebuildProjection(sql, pos, st!, off, seq, ev.source);
      const warnings = off.length ? ["OFF_PLAN_ENTRY"] : [];
      if (sig && ["CLOSED", "EXPIRED", "INVALIDATED"].includes(sig.state) && (ev.event_type === "OPEN" || ev.event_type === "ADD")) {
        warnings.push(`SIGNAL_ALREADY_${sig.state}`);
      }
      return { status: "persisted" as const, event_id: eventId, position: summary(pos, st!, off, seq), warnings };
    });
  try {
    return await attempt();
  } catch (e) {
    // concurrent duplicate submission: the unique (user, idempotency key) or (position, seq) constraint fired
    if ((e as { code?: string })?.code === "23505") return attempt();
    throw e;
  }
}

export async function listPositions(db: Db, userId: string, includeClosed = false): Promise<PositionSummaryDto[]> {
  return db.asMember(userId, async (sql) => {
    const pos = await sql.query<{ id: string; symbol: string; signal_id: string | null; created_at: Date }>(
      `SELECT id, symbol, signal_id, created_at FROM apex.positions ${includeClosed ? "" : "WHERE state <> 'CLOSED'"} ORDER BY created_at DESC LIMIT 200`,
    );
    const out: PositionSummaryDto[] = [];
    for (const p of pos) {
      const rows = await events(sql, p.id);
      const st = replay(toLedger(rows));
      out.push(summary(p, st, offPlanReasons(st, await signalFor(sql, p.signal_id)), rows.length));
    }
    return out;
  });
}

export async function getPosition(db: Db, userId: string, positionId: string): Promise<{ summary: PositionSummaryDto; events: Record<string, unknown>[] }> {
  if (!/^[0-9a-f-]{36}$/.test(positionId)) throw new HttpError(404, "NOT_FOUND", "position not found");
  return db.asMember(userId, async (sql) => {
    const p = await loadPosition(sql, positionId, false);
    if (!p) throw new HttpError(404, "NOT_FOUND", "position not found");
    const rows = await events(sql, p.id);
    const st = replay(toLedger(rows));
    const evs = await sql.query(
      "SELECT id, seq, event_type, price, quantity, fees, event_at, correction_json, notes, source, recorded_at FROM apex.position_events WHERE position_id = $1 ORDER BY seq",
      [p.id],
    );
    return { summary: summary(p, st, offPlanReasons(st, await signalFor(sql, p.signal_id)), rows.length), events: evs };
  });
}

export async function deleteMemberData(db: Db, userId: string): Promise<number> {
  return db.asMember(userId, async (sql) => {
    const r = await sql.query<{ n: number }>("WITH d AS (DELETE FROM apex.positions RETURNING 1) SELECT count(*)::int AS n FROM d");
    await sql.query("DELETE FROM apex.symbol_mutes");
    await sql.query("DELETE FROM apex.notification_subscriptions");
    return r[0]?.n ?? 0;
  });
}
