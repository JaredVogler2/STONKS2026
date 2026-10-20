import { Download } from "lucide-react";
import Link from "next/link";
import { ActionButton } from "@/components/client/action-button";
import { PositionEventForm } from "@/components/client/position-forms";
import { PrintButton } from "@/components/client/prefs";
import { AssessmentBadge, MAX_PRICE_AGE_S, PRICE_OK } from "@/components/ui/positions";
import { Badge, Card, EmptyState, KeyValue, Notice, PageHeader, Stat, Table, Td, Th } from "@/components/ui/primitives";
import { buttonClass } from "@/lib/button";
import { fmtEt, fmtNum, fmtPct, fmtSigned } from "@/lib/format";
import { pageOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { latestAssessments, memberCounts, returnCorrelations } from "@/server/services/dashboard";
import { listPositions } from "@/server/services/positions";
import { getSettings } from "@/server/services/settings";

export const dynamic = "force-dynamic";
export const metadata = { title: "My Positions" };

type Json = Record<string, unknown>;

export default async function PositionsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const owner = await pageOwner();
  const sp = await searchParams;
  const showClosed = sp.closed === "1";
  const ctx = await appContext();
  const now = await ctx.now();
  const [positions, assessments, members, settings] = await Promise.all([
    listPositions(ctx.db, owner.userId, showClosed), latestAssessments(ctx.db, owner.userId), memberCounts(ctx.db), getSettings(ctx.db),
  ]);
  const budget = settings["owner.risk_budget"].value;
  const open = positions.filter((p) => p.state === "OPEN" || p.state === "PARTIALLY_CLOSED");
  const rows = positions.map((p) => {
    const a = assessments.get(p.position_id);
    const as = (a?.assessment ?? {}) as Json;
    const fresh = PRICE_OK.has(String(as.price_status)) && Number(as.quote_age_seconds ?? Infinity) <= MAX_PRICE_AGE_S;
    const isOpen = p.state === "OPEN" || p.state === "PARTIALLY_CLOSED";
    const stop = Number(((as.original_scenario as Json | undefined)?.stop_level) ?? NaN);
    return { p, as, fresh, isOpen, assessedAt: a?.asof_at,
      exposure: isOpen && fresh ? Number(p.total_quantity) * Number(as.price) : 0,
      unrealized: isOpen && fresh ? Number(as.unrealized_pnl ?? 0) : 0,
      risk: isOpen && fresh && Number.isFinite(stop) ? Math.max(0, Number(p.total_quantity) * (Number(as.price) - stop)) : 0 };
  });
  const sum = (f: (r: (typeof rows)[number]) => number) => rows.reduce((acc, r) => acc + f(r), 0);
  const exposure = sum((r) => r.exposure);
  const unrealized = sum((r) => r.unrealized);
  const openRisk = sum((r) => r.risk);
  const staleCount = rows.filter((r) => r.isOpen && !r.fresh).length;
  const realized = positions.reduce((s, p) => s + Number(p.realized_pnl), 0);
  const corr = budget.enabled ? await returnCorrelations(ctx.db, open.map((p) => p.symbol)) : null;

  return (
    <>
      <PageHeader title="My Positions"
        subtitle={<>Your own <strong>self-reported</strong> positions (FIFO lots). Entered here or with the Discord bot; never verified against a brokerage. Other members&apos; positions are private to them and appear only as counts.</>}
        actions={
          <>
            <Link href={showClosed ? "/positions" : "/positions?closed=1"} className={buttonClass("secondary")}>{showClosed ? "Hide closed" : "Show closed"}</Link>
            <a href="/api/v1/admin/positions/export" className={buttonClass("secondary")} download><Download aria-hidden="true" className="size-4" /> Trade report CSV</a>
            <PrintButton />
          </>
        } />
      <section aria-label="Totals" className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Open positions" value={String(open.length)} sub={`${positions.filter((p) => p.state === "WATCHING").length} watching`} />
        <Stat label="Exposure" value={<span className="sensitive">{fmtNum(exposure)}</span>} sub="fresh prices only" />
        <Stat label="Unrealized" value={<span className="sensitive">{fmtSigned(unrealized)}</span>} tone={unrealized > 0 ? "pos" : unrealized < 0 ? "neg" : "neutral"}
          sub={staleCount ? `${staleCount} position(s) excluded: stale price` : "from assessed prices"} />
        <Stat label="Realized" value={<span className="sensitive">{fmtSigned(realized)}</span>} tone={realized > 0 ? "pos" : realized < 0 ? "neg" : "neutral"}
          sub={showClosed ? "incl. closed positions" : "open/watching only"} />
        <Stat label="Open risk to stops" value={<span className="sensitive">{fmtNum(openRisk)}</span>}
          sub={budget.enabled ? `${fmtPct(openRisk / budget.hypothetical_account_usd)} of hypothetical ${fmtNum(budget.hypothetical_account_usd, 0)}` : "set a hypothetical budget in Settings"} />
      </section>

      <Card title="Positions" definition="Unrealized P&L uses the latest assessed price and is hidden when that price is older than an hour. Distances are to the original plan's stop and target."
        interval={`as of ${fmtEt(now)}`}>
        {rows.length ? (
          <Table caption="My positions">
            <thead><tr><Th>Symbol</Th><Th>State</Th><Th align="right">Qty</Th><Th align="right">Avg cost</Th><Th align="right">Price</Th><Th align="right">Unrealized</Th>
              <Th align="right">To stop</Th><Th align="right">To target</Th><Th align="right">Sessions</Th><Th>Assessment</Th></tr></thead>
            <tbody>
              {rows.map(({ p, as, fresh, isOpen }) => (
                <tr key={p.position_id} className="hover:bg-surface-2/60">
                  <Td className="font-medium">
                    <Link href={`/positions/${p.position_id}`} className="text-info underline decoration-info/40 underline-offset-2 hover:decoration-info">{p.symbol}</Link>
                    {p.off_plan ? <Badge tone="warn" className="ml-1" title={p.off_plan_reasons.join(", ")}>off plan</Badge> : null}
                  </Td>
                  <Td><Badge>{p.state.toLowerCase().replace(/_/g, " ")}</Badge></Td>
                  <Td align="right"><span className="sensitive">{fmtNum(p.total_quantity, 0)}</span></Td>
                  <Td align="right"><span className="sensitive">{Number(p.avg_cost) ? fmtNum(p.avg_cost) : "—"}</span></Td>
                  <Td align="right">{isOpen ? (fresh ? fmtNum(as.price) : <Badge tone="warn">stale</Badge>) : "—"}</Td>
                  <Td align="right" className={Number(as.unrealized_pnl) > 0 ? "text-pos" : Number(as.unrealized_pnl) < 0 ? "text-neg" : ""}>
                    <span className="sensitive">{isOpen && fresh ? `${fmtSigned(as.unrealized_pnl)} (${fmtPct(as.unrealized_pct, 1, true)})` : "—"}</span>
                  </Td>
                  <Td align="right">{isOpen && fresh && as.distance_to_stop_pct !== undefined ? fmtPct(as.distance_to_stop_pct) : "—"}</Td>
                  <Td align="right">{isOpen && fresh && as.distance_to_target_pct !== undefined ? fmtPct(as.distance_to_target_pct) : "—"}</Td>
                  <Td align="right">{as.sessions_held !== undefined ? `${String(as.sessions_held)} / ${String(Number(as.sessions_held) + Number(as.sessions_remaining ?? 0))}` : "—"}</Td>
                  <Td><AssessmentBadge status={isOpen || p.state === "WATCHING" ? (as.status as string | undefined) : "CLOSED"} /></Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <EmptyState title={showClosed ? "No positions recorded" : "No open positions"} detail="Record a buy below, use “Watch in my positions” on a signal, or use /apex_buy in Discord." />
        )}
      </Card>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Record a position" definition="Starts a new self-reported position. Use a position's page to add, sell part, close or correct.">
          <PositionEventForm modes={["OPEN", "WATCH"]} nowIso={now.toISOString()} onDoneHref="/positions" />
        </Card>
        <Card title="Members (aggregate only)" definition="Counts across all members. Individual members' fills are private and not viewable here; there is no impersonation.">
          <KeyValue items={[
            ["Discord members (excl. you)", String(members.members ?? 0)], ["People tracking positions (incl. you)", String(members.members_with_positions ?? 0)],
            ["Open positions (all)", String(members.open_positions ?? 0)], ["Watching (all)", String(members.watching ?? 0)],
            ["Private updates sent", String(members.dm_sent ?? 0)], ["Held privately (DMs off)", String(members.held_private ?? 0)],
          ]} />
        </Card>
      </div>

      <Card title="Cross-position risk" definition="Opt-in. Correlation of daily log returns over the last 60 common sessions from stored bars; high correlation means positions behave like one bet.">
        {!budget.enabled ? (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-[13px] text-muted">Off. Enable a hypothetical risk budget to see open risk as a percentage and correlations between open positions.</p>
            <ActionButton url="/api/v1/admin/settings" method="PUT" label="Enable with $25,000 hypothetical account"
              body={{ key: "owner.risk_budget", value: { enabled: true, hypothetical_account_usd: budget.hypothetical_account_usd } }}
              successMessage="Enabled" />
          </div>
        ) : corr && corr.matrix.length ? (
          <div className="overflow-x-auto">
            <table className="num text-[12px]">
              <caption className="sr-only">Return correlation matrix</caption>
              <thead><tr><th />{corr.symbols.map((s) => <th key={s} scope="col" className="px-2 py-1 text-faint">{s}</th>)}</tr></thead>
              <tbody>
                {corr.symbols.map((s, i) => (
                  <tr key={s}>
                    <th scope="row" className="px-2 py-1 text-left text-faint">{s}</th>
                    {corr.matrix[i]!.map((v, j) => (
                      <td key={j} className="px-2 py-1 text-center" style={{ background: v === null || i === j ? undefined
                        : `color-mix(in oklab, ${v > 0 ? "var(--warn)" : "var(--info)"} ${Math.round(Math.abs(v) * 45)}%, transparent)` }}>
                        {i === j ? "—" : v === null ? "n/a" : v.toFixed(2)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-[11px] text-faint">{corr.sessions} common sessions. Correlation is unstable in short windows and rises in sell-offs.</p>
          </div>
        ) : <Notice tone="neutral" title="Need at least two open positions with stored bars">Correlations appear once two or more open positions have enough history.</Notice>}
      </Card>
    </>
  );
}
