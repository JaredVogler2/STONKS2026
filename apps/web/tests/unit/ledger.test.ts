import { describe, expect, it } from "vitest";
import vectors from "../../../../packages/quant/vectors/ledger.json";
import { apply, avgCost, dec, fmt, LedgerError, replay, unrealizedPnl, type LedgerEvent, type LedgerState } from "@/server/domain/ledger";

type RawFill = { price: string; quantity: string; fees: string; fill_at: string };
type RawEvent = { id: string; type: string; fill?: RawFill; correction?: { target: string; void: boolean; replacement?: RawFill }; error?: string };
type Expect = Record<string, unknown> & { state: string; total_quantity: string; avg_cost: string; realized_pnl: string };

const toFill = (f: RawFill) => ({ price: dec(f.price), quantity: dec(f.quantity), fees: dec(f.fees), fillAt: new Date(f.fill_at) });

function toEvent(raw: RawEvent, seq: number): LedgerEvent {
  return {
    eventId: raw.id,
    seq,
    eventType: raw.type as LedgerEvent["eventType"],
    fill: raw.fill ? toFill(raw.fill) : null,
    correction: raw.correction
      ? { targetEventId: raw.correction.target, void: raw.correction.void, replacement: raw.correction.replacement ? toFill(raw.correction.replacement) : null }
      : null,
  };
}

const norm = (s: string) => fmt(dec(s));

function check(st: LedgerState, exp: Expect) {
  expect(st.state).toBe(exp.state);
  expect(fmt(st.totalQuantity)).toBe(norm(exp.total_quantity));
  expect(fmt(avgCost(st))).toBe(norm(exp.avg_cost));
  expect(fmt(st.realizedPnl)).toBe(norm(exp.realized_pnl));
  if (exp.fees_total) expect(fmt(st.feesTotal)).toBe(norm(exp.fees_total as string));
  if (exp.lots_remaining) expect(st.lots.map((l) => fmt(l.quantityRemaining))).toEqual((exp.lots_remaining as string[]).map(norm));
  if (exp.unrealized_at) {
    const u = exp.unrealized_at as { price: string; pnl: string };
    expect(fmt(unrealizedPnl(st, dec(u.price)))).toBe(norm(u.pnl));
  }
}

describe("shared ledger vectors (parity with Python)", () => {
  for (const c of vectors.cases as unknown as { name: string; events: RawEvent[]; expect: Expect; checkpoints?: Record<string, Expect> }[]) {
    it(c.name, () => {
      const kept: LedgerEvent[] = [];
      let seq = 0;
      for (const raw of c.events) {
        seq += 1;
        const ev = toEvent(raw, seq);
        if (raw.error) {
          let code = "";
          try {
            apply(kept, ev);
          } catch (e) {
            code = e instanceof LedgerError ? e.code : "UNEXPECTED";
          }
          expect(code).toBe(raw.error);
          continue;
        }
        const st = apply(kept, ev);
        kept.push(ev);
        if (c.checkpoints?.[raw.id]) check(st, c.checkpoints[raw.id]!);
      }
      check(replay(kept), c.expect);
    });
  }
});
