import Decimal from "decimal.js";

/**
 * TypeScript twin of apps/pi-worker/src/apex_worker/portfolio/ledger.py.
 * Both are pinned by packages/quant/vectors/ledger.json; keep them identical in behaviour.
 */
const D = Decimal.clone({ precision: 34, rounding: Decimal.ROUND_HALF_EVEN, toExpNeg: -40, toExpPos: 40 });
type Dec = InstanceType<typeof D>;

export const LEDGER_VERSION = "ledger-fifo-1";
export const MAX_PRICE = new D("1000000");
export const MAX_QUANTITY = new D("100000000");
export const MAX_FEES = new D("100000");
export const FUTURE_SKEW_MS = 2 * 60 * 1000;
const EARLIEST_FILL = Date.UTC(2000, 0, 1);

export type EventType = "WATCH" | "UNWATCH" | "OPEN" | "ADD" | "PARTIAL_EXIT" | "CLOSE" | "CORRECT" | "CANCEL_CORRECTION";
export type PositionState = "WATCHING" | "OPEN" | "PARTIALLY_CLOSED" | "CLOSED";
const FILL_EVENTS = new Set<EventType>(["OPEN", "ADD", "PARTIAL_EXIT", "CLOSE"]);
const BUY_EVENTS = new Set<EventType>(["OPEN", "ADD"]);

export class LedgerError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export interface Fill {
  price: Dec;
  quantity: Dec;
  fees: Dec;
  fillAt: Date;
}

export interface Correction {
  targetEventId: string;
  void: boolean;
  replacement?: Fill | null;
}

export interface LedgerEvent {
  eventId: string;
  seq: number;
  eventType: EventType;
  fill?: Fill | null;
  correction?: Correction | null;
}

export interface Lot {
  lotId: string;
  fillAt: Date;
  price: Dec;
  quantityInitial: Dec;
  quantityRemaining: Dec;
  fees: Dec;
  seq: number;
}

export interface LedgerState {
  state: PositionState;
  lots: Lot[];
  totalQuantity: Dec;
  costRemaining: Dec;
  realizedPnl: Dec;
  feesTotal: Dec;
  openedAt: Date | null;
  closedAt: Date | null;
  hadSells: boolean;
}

export function dec(v: string | number | Dec): Dec {
  return new D(v);
}

export function fmt(v: Dec): string {
  if (v.isZero()) return "0";
  return v.toFixed();
}

export function avgCost(s: LedgerState): Dec {
  if (s.totalQuantity.isZero()) return new D(0);
  return s.costRemaining.div(s.totalQuantity).toDecimalPlaces(6, Decimal.ROUND_HALF_EVEN);
}

export function unrealizedPnl(s: LedgerState, price: Dec): Dec {
  return s.totalQuantity.mul(price).sub(s.costRemaining).toDecimalPlaces(6, Decimal.ROUND_HALF_EVEN);
}

function lotCost(lot: Lot, qty: Dec): Dec {
  return qty.mul(lot.price).add(lot.fees.mul(qty).div(lot.quantityInitial));
}

function places(v: Dec): number {
  return v.decimalPlaces();
}

export function parseDecimal(raw: unknown, field: string, maxDp: number, max: Dec, allowZero = false): Dec {
  if (typeof raw !== "string" || raw.trim() === "") throw new LedgerError("INVALID_NUMBER", `${field} must be an exact decimal string`);
  let v: Dec;
  try {
    v = new D(raw.trim());
  } catch {
    throw new LedgerError("INVALID_NUMBER", `${field} is not a number`);
  }
  if (!v.isFinite()) throw new LedgerError("INVALID_NUMBER", `${field} must be finite`);
  if (v.isNeg() || (v.isZero() && !allowZero)) {
    throw new LedgerError("NON_POSITIVE", `${field} must be ${allowZero ? "non-negative" : "positive"}`);
  }
  if (v.gt(max)) throw new LedgerError("TOO_LARGE", `${field} exceeds the safety limit ${fmt(max)}`);
  if (places(v) > maxDp) throw new LedgerError("TOO_PRECISE", `${field} allows at most ${maxDp} decimal places`);
  return v;
}

export function parseFill(raw: { price: unknown; quantity: unknown; fees?: unknown; fill_at: unknown }, now: Date): Fill {
  const at = typeof raw.fill_at === "string" ? new Date(raw.fill_at) : null;
  if (!at || Number.isNaN(at.getTime())) throw new LedgerError("INVALID_TIME", "fill time is invalid");
  if (at.getTime() > now.getTime() + FUTURE_SKEW_MS) throw new LedgerError("FUTURE_FILL", "fill time cannot be in the future");
  if (at.getTime() < EARLIEST_FILL) throw new LedgerError("INVALID_TIME", "fill time is implausibly old");
  return {
    price: parseDecimal(raw.price, "price", 4, MAX_PRICE),
    quantity: parseDecimal(raw.quantity, "quantity", 6, MAX_QUANTITY),
    fees: parseDecimal(raw.fees ?? "0", "fees", 4, MAX_FEES, true),
    fillAt: at,
  };
}

function validateStructure(events: LedgerEvent[]): void {
  const seen = new Map<string, LedgerEvent>();
  for (let i = 1; i < events.length; i++) {
    if (events[i]!.seq <= events[i - 1]!.seq) throw new LedgerError("BAD_SEQUENCE", "events must have strictly increasing sequence numbers");
  }
  if (events.length && !["WATCH", "OPEN"].includes(events[0]!.eventType)) {
    throw new LedgerError("NO_POSITION", "a position starts with OPEN or WATCH");
  }
  for (const e of events) {
    if (seen.has(e.eventId)) throw new LedgerError("DUPLICATE_EVENT", "duplicate event id");
    if (FILL_EVENTS.has(e.eventType) && !e.fill) throw new LedgerError("MISSING_FILL", `${e.eventType} requires a fill`);
    if (e.eventType === "CORRECT" || e.eventType === "CANCEL_CORRECTION") {
      if (!e.correction) throw new LedgerError("MISSING_CORRECTION", "correction details are required");
      const target = seen.get(e.correction.targetEventId);
      if (!target) throw new LedgerError("UNKNOWN_TARGET", "correction target is not an earlier event of this position");
      if (e.eventType === "CORRECT") {
        if (!FILL_EVENTS.has(target.eventType)) {
          throw new LedgerError("BAD_TARGET", "only fills (OPEN/ADD/PARTIAL_EXIT/CLOSE) can be corrected");
        }
        if (!e.correction.void && !e.correction.replacement) {
          throw new LedgerError("MISSING_REPLACEMENT", "a non-void correction needs a replacement fill");
        }
      } else if (target.eventType !== "CORRECT") {
        throw new LedgerError("BAD_TARGET", "CANCEL_CORRECTION must target a CORRECT event");
      }
    }
    seen.set(e.eventId, e);
  }
}

export function replay(input: LedgerEvent[]): LedgerState {
  const events = [...input].sort((a, b) => a.seq - b.seq);
  validateStructure(events);
  const cancelTargets = events.filter((e) => e.eventType === "CANCEL_CORRECTION").map((e) => e.correction!.targetEventId);
  if (new Set(cancelTargets).size !== cancelTargets.length) throw new LedgerError("ALREADY_CANCELLED", "that correction was already cancelled");
  const cancelled = new Set(cancelTargets);
  const replaced = new Map<string, Fill | null>();
  for (const e of events) {
    if (e.eventType === "CORRECT" && e.correction && !cancelled.has(e.eventId)) {
      replaced.set(e.correction.targetEventId, e.correction.void ? null : (e.correction.replacement ?? null));
    }
  }
  const effective: { ev: LedgerEvent; fill: Fill }[] = [];
  for (const e of events) {
    if (!FILL_EVENTS.has(e.eventType)) continue;
    const fill = replaced.has(e.eventId) ? replaced.get(e.eventId)! : e.fill!;
    if (fill) effective.push({ ev: e, fill });
  }
  effective.sort((a, b) => a.fill.fillAt.getTime() - b.fill.fillAt.getTime() || a.ev.seq - b.ev.seq);

  const st: LedgerState = {
    state: "WATCHING", lots: [], totalQuantity: new D(0), costRemaining: new D(0), realizedPnl: new D(0),
    feesTotal: new D(0), openedAt: null, closedAt: null, hadSells: false,
  };
  for (const { ev, fill } of effective) {
    st.feesTotal = st.feesTotal.add(fill.fees);
    if (BUY_EVENTS.has(ev.eventType)) {
      const lot: Lot = { lotId: ev.eventId, fillAt: fill.fillAt, price: fill.price, quantityInitial: fill.quantity,
        quantityRemaining: fill.quantity, fees: fill.fees, seq: ev.seq };
      st.lots.push(lot);
      st.totalQuantity = st.totalQuantity.add(fill.quantity);
      st.costRemaining = st.costRemaining.add(lotCost(lot, fill.quantity));
      st.openedAt ??= fill.fillAt;
      continue;
    }
    const qty = ev.eventType === "CLOSE" ? st.totalQuantity : fill.quantity;
    if (qty.lte(0)) throw new LedgerError("NOTHING_TO_SELL", "no shares remain at that fill time");
    if (qty.gt(st.totalQuantity)) {
      throw new LedgerError("OVERSELL", `cannot sell ${fmt(qty)} shares; only ${fmt(st.totalQuantity)} remain at that fill time`);
    }
    st.hadSells = true;
    let remaining = qty;
    let cost = new D(0);
    for (const lot of st.lots) {
      if (remaining.isZero()) break;
      if (lot.quantityRemaining.isZero()) continue;
      const take = Decimal.min(lot.quantityRemaining, remaining) as Dec;
      cost = cost.add(lotCost(lot, take));
      lot.quantityRemaining = lot.quantityRemaining.sub(take);
      remaining = remaining.sub(take);
    }
    st.totalQuantity = st.totalQuantity.sub(qty);
    st.costRemaining = st.costRemaining.sub(cost);
    st.realizedPnl = st.realizedPnl.add(qty.mul(fill.price)).sub(fill.fees).sub(cost);
    if (st.totalQuantity.isZero()) {
      st.closedAt = fill.fillAt;
      st.costRemaining = new D(0);
    } else {
      st.closedAt = null;
    }
  }
  if (effective.length === 0 || st.lots.length === 0) {
    const unwatched = events.some((e) => e.eventType === "UNWATCH");
    const hadFills = events.some((e) => FILL_EVENTS.has(e.eventType));
    st.state = unwatched || hadFills ? "CLOSED" : "WATCHING";
  } else if (st.totalQuantity.gt(0)) {
    st.state = st.hadSells ? "PARTIALLY_CLOSED" : "OPEN";
  } else {
    st.state = "CLOSED";
  }
  st.realizedPnl = st.realizedPnl.toDecimalPlaces(6, Decimal.ROUND_HALF_EVEN);
  st.costRemaining = st.costRemaining.toDecimalPlaces(12, Decimal.ROUND_HALF_EVEN);
  return st;
}

export function apply(existing: LedgerEvent[], next: LedgerEvent): LedgerState {
  const current = existing.length ? replay(existing) : null;
  const state = current?.state ?? null;
  const et = next.eventType;
  if (et === "WATCH" && existing.length) throw new LedgerError("ALREADY_EXISTS", "position already exists; WATCH starts a new position only");
  if (et === "OPEN" && !(state === null || state === "WATCHING")) {
    throw new LedgerError("ALREADY_OPEN", "this position already has an entry; use ADD, or record a new position");
  }
  if ((et === "ADD" || et === "PARTIAL_EXIT" || et === "CLOSE") && !(state === "OPEN" || state === "PARTIALLY_CLOSED")) {
    throw new LedgerError("NOT_OPEN", "position is not open");
  }
  if (et === "UNWATCH" && state !== "WATCHING") throw new LedgerError("NOT_WATCHING", "only a watch-only position can be unwatched");
  if ((et === "CORRECT" || et === "CANCEL_CORRECTION") && !existing.length) throw new LedgerError("NO_POSITION", "nothing to correct");
  return replay([...existing, next]);
}
