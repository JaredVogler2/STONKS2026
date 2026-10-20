import { Download, GitCompare } from "lucide-react";
import { cookies } from "next/headers";
import Link from "next/link";
import { redirect } from "next/navigation";
import { buttonClass } from "@/lib/button";
import { SignalFilters } from "@/components/client/signal-filters";
import {
  Badge, Card, ConfidenceBadge, DemoTag, EmptyState, PageHeader, QualityMeter, SignalStateBadge, Table, Td, Th,
} from "@/components/ui/primitives";
import { fmtEt, humanize, strategyLabel } from "@/lib/format";
import { pageOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { exploreSignals, explorerOptions, parseFilter, type ExplorerFilter } from "@/server/services/explorer";

export const dynamic = "force-dynamic";
export const metadata = { title: "Signals Explorer" };

type SP = Record<string, string | string[] | undefined>;

function qs(sp: SP, patch: Record<string, string | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(sp)) if (typeof v === "string" && v && k !== "reset") p.set(k, v);
  for (const [k, v] of Object.entries(patch)) {
    if (v === undefined) p.delete(k);
    else p.set(k, v);
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

const COLUMNS: { key?: NonNullable<ExplorerFilter["sort"]>; label: string; align?: "right" }[] = [
  { key: "symbol", label: "Symbol" }, { label: "Setup" }, { key: "quality", label: "Quality", align: "right" }, { key: "probability", label: "Probability" },
  { label: "Entry zone", align: "right" }, { label: "Stop / target", align: "right" }, { key: "rr", label: "R/R", align: "right" },
  { key: "horizon", label: "Horizon", align: "right" }, { label: "Sector" }, { label: "Status" }, { key: "asof", label: "As of" }, { label: "" },
];

export default async function SignalsPage({ searchParams }: { searchParams: Promise<SP> }) {
  await pageOwner();
  const sp = await searchParams;
  const hasParams = Object.keys(sp).length > 0;
  if (!hasParams) {
    const saved = (await cookies()).get("apex_sig_filters")?.value;
    if (saved) {
      const decoded = decodeURIComponent(saved);
      if (/^[\w.,=&%+-]{1,400}$/.test(decoded)) redirect(`/signals?${decoded}`);
    }
  }
  const ctx = await appContext();
  const now = await ctx.now();
  const filter = parseFilter(sp);
  const [res, opts] = await Promise.all([exploreSignals(ctx.db, filter, now), explorerOptions(ctx.db)]);
  const pages = Math.max(1, Math.ceil(res.total / res.pageSize));
  const sort = filter.sort ?? "asof";
  const dir = filter.dir ?? "desc";

  return (
    <>
      <PageHeader title="Signals Explorer"
        subtitle="Every published signal as originally stated (immutable revisions), with its current lifecycle status. Filters and sorting run on the server against the stored record."
        actions={
          <>
            <Link href="/signals/compare" className={buttonClass("secondary")}><GitCompare aria-hidden="true" className="size-4" /> Compare</Link>
            <a href={`/api/v1/admin/signals/export${qs(sp, { page: undefined })}`} className={buttonClass("secondary")} download>
              <Download aria-hidden="true" className="size-4" /> CSV
            </a>
          </>
        } />
      <Card title="Filters" definition="Remembered on this device. Probability filter: only signals from a calibrated, unexpired model carry one.">
        <SignalFilters strategies={opts.strategies.map((s) => ({ value: s, label: strategyLabel(s) }))}
          sectors={opts.sectors.map((s) => ({ value: s, label: humanize(s) }))} />
      </Card>
      <Card title={`${res.total} signal${res.total === 1 ? "" : "s"}`}
        definition="Entry zone, stop and target are the levels published at the time. R/R is to the primary target from the zone midpoint. Horizon is the maximum holding window in trading sessions."
        interval={`page ${res.page} of ${pages}`}>
        {res.rows.length ? (
          <>
            <Table caption="Signals">
              <thead>
                <tr>
                  {COLUMNS.map((c, i) => (
                    <Th key={i} align={c.align}>
                      {c.key ? (
                        <Link href={`/signals${qs(sp, { sort: c.key, dir: sort === c.key && dir === "desc" ? "asc" : "desc", page: undefined })}`}
                          aria-label={`Sort by ${c.label}`} className="inline-flex items-center gap-1 hover:text-fg">
                          {c.label}
                          {sort === c.key ? <span aria-hidden="true">{dir === "desc" ? "▼" : "▲"}</span> : null}
                        </Link>
                      ) : c.label}
                    </Th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {res.rows.map((r) => (
                  <tr key={r.id} className="hover:bg-surface-2/60">
                    <Td className="font-medium">
                      <Link href={`/signals/${r.id}`} className="text-info hover:underline">{r.symbol}</Link>
                      {r.is_demo && !ctx.isDemo ? <span className="ml-1.5"><DemoTag /></span> : null}
                      {r.revision > 1 ? <span className="ml-1 text-[11px] text-faint">r{r.revision}</span> : null}
                    </Td>
                    <Td className="max-w-[170px] truncate text-muted"><span title={strategyLabel(r.strategy_id)}>{strategyLabel(r.strategy_id)}</span></Td>
                    <Td align="right"><QualityMeter value={r.quality_score} /></Td>
                    <Td><ConfidenceBadge status={r.confidence_status} probability={r.confidence_probability} /></Td>
                    <Td align="right">{r.entry_min}–{r.entry_max}</Td>
                    <Td align="right"><span className="text-neg">{r.stop_level}</span> / <span className="text-pos">{r.target_primary}</span></Td>
                    <Td align="right">{r.reward_to_risk ?? "—"}</Td>
                    <Td align="right">≤{r.horizon_max_sessions}s</Td>
                    <Td className="text-muted">{r.sector ? humanize(r.sector) : "—"}</Td>
                    <Td>
                      <SignalStateBadge state={r.state} outcome={r.outcome} />
                      {r.tier === "RESEARCH_SETUP" ? <span className="ml-1 text-[11px] text-faint">research</span> : null}
                      {r.catalysts ? <Badge className="ml-1" tone="info" title="Has linked news/filing catalysts">cat {r.catalysts}</Badge> : null}
                    </Td>
                    <Td className="whitespace-nowrap text-[12px] text-muted">{fmtEt(r.asof_at)}</Td>
                    <Td><Link href={`/signals/compare?a=${r.id}`} className="text-[11px] text-faint hover:text-fg" aria-label={`Compare ${r.symbol}`}>compare</Link></Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <nav aria-label="Pagination" className="mt-3 flex items-center justify-between text-[13px]">
              {res.page > 1 ? <Link className={buttonClass("secondary")} href={`/signals${qs(sp, { page: String(res.page - 1) })}`}>← Newer</Link> : <span />}
              <span className="text-faint">{(res.page - 1) * res.pageSize + 1}–{Math.min(res.total, res.page * res.pageSize)} of {res.total}</span>
              {res.page < pages ? <Link className={buttonClass("secondary")} href={`/signals${qs(sp, { page: String(res.page + 1) })}`}>Older →</Link> : <span />}
            </nav>
          </>
        ) : (
          <EmptyState title="No signals match these filters" detail="Loosen a filter, or wait for the next end-of-day scan. A day with no qualified setup is recorded as NO TRADE."
            action="Reset filters" href="/signals?reset=1" />
        )}
      </Card>
    </>
  );
}
