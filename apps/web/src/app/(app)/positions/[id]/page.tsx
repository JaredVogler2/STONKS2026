import Link from "next/link";
import { notFound } from "next/navigation";
import { CandleChart } from "@/components/client/candle-chart";
import { PositionEventForm } from "@/components/client/position-forms";
import { AssessmentBadge, MAX_PRICE_AGE_S, PRICE_OK } from "@/components/ui/positions";
import { Badge, Card, EmptyState, KeyValue, Notice, PageHeader, Table, Td, Th } from "@/components/ui/primitives";
import { fmtEt, fmtNum, fmtPct, fmtSigned } from "@/lib/format";
import { pageOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { HttpError } from "@/server/http/responses";
import { etParts } from "@/server/market/calendar";
import { assessmentHistory } from "@/server/services/dashboard";
import { getPosition } from "@/server/services/positions";
import { barsFor } from "@/server/services/queries";

export const dynamic = "force-dynamic";
export const metadata = { title: "Position" };

type Json = Record<string, unknown>;

export default async function PositionPage({ params }: { params: Promise<{ id: string }> }) {
  const owner = await pageOwner();
  const { id } = await params;
  const ctx = await appContext();
  const now = await ctx.now();
  let d: Awaited<ReturnType<typeof getPosition>>;
  try {
    d = await getPosition(ctx.db, owner.userId, id); // member-scoped: another member's id is simply not found
  } catch (e) {
    if (e instanceof HttpError && e.status === 404) notFound();
    throw e;
  }
  const p = d.summary;
  const history = await assessmentHistory(ctx.db, owner.userId, id, 12);
  const latest = (history[0]?.assessment ?? {}) as Json;
  const fresh = PRICE_OK.has(String(latest.price_status)) && Number(latest.quote_age_seconds ?? Infinity) <= MAX_PRICE_AGE_S;
  const plan = (latest.original_scenario ?? {}) as Json;
  const revised = (latest.revised_scenario ?? {}) as Json;
  const bars = await barsFor(ctx.db, p.symbol, 160);
  const isOpen = p.state === "OPEN" || p.state === "PARTIALLY_CLOSED";
  const fills = d.events.filter((e) => ["OPEN", "ADD", "PARTIAL_EXIT", "CLOSE"].includes(String(e.event_type)));
  const voided = new Set(d.events.filter((e) => e.event_type === "CORRECT").map((e) => String((e.correction_json as Json | null)?.target_event_id ?? "")));
  const modes = (isOpen ? ["ADD", "PARTIAL_EXIT", "CLOSE", "CORRECT"] : p.state === "WATCHING" ? ["OPEN", "UNWATCH"] : ["CORRECT"]) as ("ADD" | "PARTIAL_EXIT" | "CLOSE" | "CORRECT" | "OPEN" | "UNWATCH")[];

  return (
    <>
      <div className="text-[12px] text-faint"><Link href="/positions" className="hover:text-fg">My Positions</Link> / {p.symbol}</div>
      <PageHeader title={`${p.symbol} · self-reported position`}
        subtitle={<>Opened {fmtEt(p.opened_at)}{p.signal_id ? <> from signal <Link className="text-info hover:underline" href={`/signals/${p.signal_id}`}>{p.signal_id.slice(0, 16)}…</Link></> : " (no APEX signal)"}. Cost method FIFO.</>} />
      <div className="flex flex-wrap items-center gap-2">
        <Badge>{p.state.toLowerCase().replace(/_/g, " ")}</Badge>
        <AssessmentBadge status={isOpen || p.state === "WATCHING" ? (latest.status as string | undefined) : "CLOSED"} />
        {p.off_plan ? <Badge tone="warn" title={p.off_plan_reasons.join(", ")}>off plan: {p.off_plan_reasons.join(", ").toLowerCase().replace(/_/g, " ")}</Badge> : null}
        {latest.compliance_gated ? <Badge title="Personalized group guidance is off; wording is informational">informational wording</Badge> : null}
      </div>
      {isOpen && !fresh ? (
        <Notice tone="warn" title="Price is stale">The last assessed price is older than an hour or unverified, so unrealized P&L is not shown. It refreshes with the next Pi assessment.</Notice>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Price and plan" definition="Stored daily bars; plan levels come from the signal this position follows (if any).">
          <CandleChart title={`${p.symbol} daily`} bars={bars} cutoff={etParts(now).date}
            events={fills.map((e) => ({ date: etParts(new Date(String(e.event_at))).date, label: `${String(e.event_type)} ${String(e.quantity)} @ ${String(e.price)}`,
              tone: e.event_type === "OPEN" || e.event_type === "ADD" ? "info" : "warn" }))}
            levels={plan.stop_level ? { stop: Number(plan.stop_level), target: Number(plan.target_primary), entryMin: Number(plan.entry_min), entryMax: Number(plan.entry_max) } : undefined} />
        </Card>
        <Card title="Position" definition="From your entries. Unrealized uses the latest fresh assessed price.">
          <KeyValue items={[
            ["Quantity", <span key="q" className="sensitive">{fmtNum(p.total_quantity, 0)}</span>],
            ["Average cost", <span key="a" className="sensitive">{Number(p.avg_cost) ? fmtNum(p.avg_cost) : "—"}</span>],
            ["Realized P&L", <span key="r" className="sensitive">{fmtSigned(p.realized_pnl)}</span>],
            ["Fees", <span key="f" className="sensitive">{fmtNum(p.fees_total)}</span>],
            ["Last price", isOpen ? (fresh ? `${fmtNum(latest.price)} (${String((latest.quote_provenance as Json | undefined)?.feed ?? "?")})` : "stale") : "—"],
            ["Unrealized", <span key="u" className="sensitive">{isOpen && fresh ? `${fmtSigned(latest.unrealized_pnl)} (${fmtPct(latest.unrealized_pct, 1, true)})` : "—"}</span>],
            ["To stop / target", isOpen && fresh ? `${fmtPct(latest.distance_to_stop_pct)} / ${fmtPct(latest.distance_to_target_pct)}` : "—"],
            ["Remaining reward/risk", latest.marginal_reward_to_risk !== undefined ? fmtNum(latest.marginal_reward_to_risk) : "—"],
            ["Sessions held / remaining", latest.sessions_held !== undefined ? `${String(latest.sessions_held)} / ${String(latest.sessions_remaining ?? "?")}` : "—"],
            ["Regime now", String(revised.regime ?? "—").replace(/_/g, " ")],
          ]} />
          {((latest.reasons as string[]) ?? []).length ? (
            <ul className="mt-3 list-disc space-y-1 pl-4 text-[12px] text-muted">{(latest.reasons as string[]).map((r) => <li key={r}>{r}</li>)}</ul>
          ) : null}
        </Card>
      </div>

      <Card title="Record an update" definition="Each submit is idempotent. Corrections never delete history; they add an event that voids or replaces an earlier fill.">
        <PositionEventForm modes={modes} positionId={p.position_id} symbol={p.symbol} remainingQty={p.total_quantity} nowIso={now.toISOString()}
          events={fills.filter((e) => !voided.has(String(e.id))).map((e) => ({ id: String(e.id),
            label: `#${String(e.seq)} ${String(e.event_type)} ${String(e.quantity)} @ ${String(e.price)} (${fmtEt(e.event_at as string)})` }))} />
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Open lots (FIFO)" definition="Sells consume the oldest lots first.">
          {p.lots.length ? (
            <Table caption="Lots">
              <thead><tr><Th>Bought</Th><Th align="right">Price</Th><Th align="right">Initial</Th><Th align="right">Remaining</Th><Th align="right">Fees</Th></tr></thead>
              <tbody>{p.lots.map((l) => (
                <tr key={l.lot_id}><Td className="text-muted">{fmtEt(l.fill_at)}</Td><Td align="right"><span className="sensitive">{fmtNum(l.fill_price)}</span></Td>
                  <Td align="right"><span className="sensitive">{fmtNum(l.quantity_initial, 0)}</span></Td><Td align="right"><span className="sensitive">{fmtNum(l.quantity_remaining, 0)}</span></Td>
                  <Td align="right"><span className="sensitive">{fmtNum(l.fees)}</span></Td></tr>))}
              </tbody>
            </Table>
          ) : <EmptyState title="No open lots" />}
        </Card>
        <Card title="Event history (immutable)" definition="Everything you reported, in order. Voided fills stay visible.">
          <Table caption="Position events">
            <thead><tr><Th>#</Th><Th>Event</Th><Th align="right">Qty @ price</Th><Th>When</Th><Th>Source</Th></tr></thead>
            <tbody>{d.events.map((e) => (
              <tr key={String(e.id)} className={voided.has(String(e.id)) ? "opacity-50" : undefined}>
                <Td>{String(e.seq)}</Td>
                <Td>{String(e.event_type).toLowerCase().replace(/_/g, " ")}{voided.has(String(e.id)) ? <Badge className="ml-1" tone="warn">corrected</Badge> : null}</Td>
                <Td align="right"><span className="sensitive">{e.quantity ? `${fmtNum(e.quantity, 0)} @ ${fmtNum(e.price)}` : "—"}</span></Td>
                <Td className="text-muted">{e.event_at ? fmtEt(e.event_at as string) : fmtEt(e.recorded_at as string)}</Td>
                <Td className="text-muted">{String(e.source)}</Td>
              </tr>))}
            </tbody>
          </Table>
        </Card>
      </div>

      <Card title="Assessment history" definition="The Pi's periodic checks against the original plan (newest first).">
        {history.length ? (
          <Table caption="Assessments">
            <thead><tr><Th>As of</Th><Th>Assessment</Th><Th align="right">Price</Th><Th>Price status</Th><Th align="right">Unrealized</Th><Th>Flags</Th></tr></thead>
            <tbody>{history.map((h) => {
              const a = h.assessment as Json;
              return (
                <tr key={h.asof_at}><Td className="text-muted">{fmtEt(h.asof_at)}</Td><Td><AssessmentBadge status={a.status as string} /></Td>
                  <Td align="right">{fmtNum(a.price)}</Td><Td className="text-[12px] text-muted">{String(a.price_status ?? "").toLowerCase().replace(/_/g, " ")}</Td>
                  <Td align="right"><span className="sensitive">{fmtSigned(a.unrealized_pnl)}</span></Td>
                  <Td className="text-[12px] text-muted">{((a.risk_flags as string[]) ?? []).join(", ").toLowerCase().replace(/_/g, " ") || "—"}</Td></tr>
              );
            })}</tbody>
          </Table>
        ) : <EmptyState title="Not assessed yet" detail="The Pi assesses open positions after each quote poll and at the close." />}
      </Card>
    </>
  );
}
