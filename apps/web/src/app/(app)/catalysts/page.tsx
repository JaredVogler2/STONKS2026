import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { Badge, Card, EmptyState, PageHeader, Table, Td, Th, type Tone } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import { ageSeconds, fmtAge, fmtEt, fmtEtDate, fmtPct, humanize } from "@/lib/format";
import { pageOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { CATALYST_GROUPS, catalystFeed, catalystSources, type CatalystRow } from "@/server/services/dashboard";

export const dynamic = "force-dynamic";
export const metadata = { title: "Catalyst Intelligence" };

const GROUP_LABEL: Record<string, string> = { "": "All", filings: "SEC filings", insider: "Insider (Form 4)", congress: "Congress disclosures",
  earnings: "Earnings", macro: "Macro", news: "News" };
const DIR: Record<string, { tone: Tone; label: string }> = {
  potentially_positive: { tone: "pos", label: "potentially positive" }, potentially_negative: { tone: "neg", label: "potentially negative" },
  mixed: { tone: "warn", label: "mixed" }, unknown: { tone: "neutral", label: "direction unknown" },
};
const SOURCE_LABEL: Record<string, string> = { sec_edgar: "SEC EDGAR", senate_efd: "Senate eFD", house_clerk: "House Clerk", news_rss: "News RSS", fred: "FRED",
  manual: "Manual", replay: "Replay" };

function delayDays(c: CatalystRow): number | null {
  if (!c.event_at) return null;
  return Math.round((new Date(c.available_at).getTime() - new Date(c.event_at).getTime()) / 86400_000);
}

function Row({ c, now }: { c: CatalystRow; now: Date }) {
  const lag = delayDays(c);
  const d = DIR[c.direction] ?? DIR.unknown!;
  return (
    <li className="grid gap-1 border-b border-line/70 py-3 last:border-0 sm:grid-cols-[150px_minmax(0,1fr)_120px] sm:gap-4">
      <div className="text-[12px] text-muted">
        <div className="text-fg">{fmtEt(c.available_at)}</div>
        <div className="text-[11px] text-faint">public / knowable</div>
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          {c.symbol ? <Link href={`/signals?q=${c.symbol}&reset=1`} className="text-[13px] font-semibold text-info hover:underline">{c.symbol}</Link> : <Badge>market</Badge>}
          <Badge tone={c.catalyst_type === "insider_open_market_purchase" ? "pos" : "neutral"}>{humanize(c.catalyst_type)}</Badge>
          <Badge tone={d.tone}>{d.label}</Badge>
          {c.actionable_age === "lagged_disclosure" ? <Badge tone="warn" title="Disclosed long after the trade; not a timely signal">delayed {lag ?? "?"}d</Badge> : null}
          {c.actionable_age === "scheduled_future" ? <Badge tone="info">scheduled</Badge> : null}
          {c.regulatory_flags.map((f) => <Badge key={f} tone="warn">{humanize(f)}</Badge>)}
        </div>
        <div className="mt-1 text-[13px]">
          {c.canonical_url && !c.is_demo ? (
            <a href={c.canonical_url} target="_blank" rel="noreferrer noopener" className="inline-flex items-start gap-1 hover:underline">
              {c.headline} <ExternalLink aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
            </a>
          ) : c.headline}
        </div>
        <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-faint">
          <span>{SOURCE_LABEL[c.source] ?? c.source} · grade {c.source_grade.split("_")[0]}</span>
          {c.event_at ? <span>event {fmtEtDate(c.event_at)}</span> : null}
          {c.published_at ? <span>published {fmtEt(c.published_at)}</span> : null}
          <span>{new Date(c.first_seen_at) > now ? "backfilled by APEX after the fact" : `first seen by APEX ${fmtAge(ageSeconds(now, c.first_seen_at))} ago`}</span>
          {c.is_demo && c.canonical_url ? <span>fictional DEMO link (not opened)</span> : null}
        </div>
      </div>
      <div className="num text-[12px] sm:text-right">
        <div className={cn(c.reaction_1d === null ? "text-faint" : c.reaction_1d > 0 ? "text-pos" : "text-neg")}>1d {c.reaction_1d === null ? "n/a" : fmtPct(c.reaction_1d, 1, true)}</div>
        <div className={cn(c.reaction_5d === null ? "text-faint" : c.reaction_5d > 0 ? "text-pos" : "text-neg")}>5d {c.reaction_5d === null ? "n/a" : fmtPct(c.reaction_5d, 1, true)}</div>
        {c.impact_score !== null ? <div className="text-faint">impact {c.impact_score.toFixed(2)}</div> : null}
      </div>
    </li>
  );
}

export default async function CatalystsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  await pageOwner();
  const sp = await searchParams;
  const group = typeof sp.group === "string" && sp.group in CATALYST_GROUPS ? sp.group : "";
  const symbol = typeof sp.symbol === "string" ? sp.symbol : undefined;
  const ctx = await appContext();
  const now = await ctx.now();
  const [items, sources] = await Promise.all([catalystFeed(ctx.db, { group: group || undefined, symbol, limit: 100 }, now), catalystSources(ctx.db, now)]);
  const clusters = Object.values(items.reduce<Record<string, { id: string; n: number; head: string; types: Set<string> }>>((acc, c) => {
    if (!c.cluster_id) return acc;
    const e = (acc[c.cluster_id] ??= { id: c.cluster_id, n: 0, head: c.headline, types: new Set() });
    e.n++;
    e.types.add(c.catalyst_type);
    return acc;
  }, {})).filter((c) => c.n > 1).sort((a, b) => b.n - a.n).slice(0, 8);
  const allSources = ["sec_edgar", "senate_efd", "house_clerk", "news_rss", "fred"];

  return (
    <>
      <PageHeader title="Catalyst Intelligence"
        subtitle="Filings, insider trades, congressional disclosures, earnings and macro releases, ordered by when they became public. Event, publication and first-seen times are kept separate so nothing is used before it was knowable." />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <Card title="Timeline" definition="Reaction = log change from the last close before availability to 1 and 5 sessions after, from stored bars (n/a when APEX holds no bars for the symbol)."
          interval={`${items.length} items`}
          actions={
            <nav aria-label="Catalyst type" className="flex flex-wrap gap-1">
              {Object.entries(GROUP_LABEL).map(([k, v]) => (
                <Link key={k} href={k ? `/catalysts?group=${k}` : "/catalysts"} aria-current={group === k ? "page" : undefined}
                  className={cn("rounded-md border px-2 py-1 text-[11px]", group === k ? "border-info/50 bg-info/15 text-fg" : "border-line text-muted hover:text-fg")}>{v}</Link>
              ))}
            </nav>
          }>
          {items.length ? <ul>{items.map((c) => <Row key={c.id} c={c} now={now} />)}</ul>
            : <EmptyState title="No catalysts in this view" detail="Sources are polled by the Pi; check the source status panel." />}
        </Card>
        <div className="space-y-4">
          <Card title="Source status" definition="Last time APEX saw an item from each source. Sources without recent items may simply be quiet, or not configured.">
            <Table caption="Catalyst sources" compact>
              <thead><tr><Th>Source</Th><Th align="right">30d</Th><Th>Last seen</Th></tr></thead>
              <tbody>
                {allSources.map((s) => {
                  const r = sources.find((x) => x.source === s);
                  return (
                    <tr key={s}>
                      <Td>{SOURCE_LABEL[s]}</Td><Td align="right">{r?.n_30d ?? 0}</Td>
                      <Td className="text-[12px] text-muted">{r?.last_seen ? `${fmtAge(ageSeconds(now, r.last_seen))} ago` : <Badge>no feed</Badge>}</Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
            <p className="mt-2 text-[11px] text-faint">
              No real-time news feed is used: RSS headlines are polled with delay and only linked, never republished. Congressional reports are filed up to 45 days after the trade.
            </p>
          </Card>
          <Card title="Topic clusters" definition="Items the pipeline grouped as the same underlying story (counted once).">
            {clusters.length ? (
              <ul className="space-y-2 text-[13px]">
                {clusters.map((c) => (
                  <li key={c.id}><Badge>{c.n} items</Badge> <span className="text-muted">{[...c.types].map(humanize).join(", ")}</span><div className="truncate text-[12px]">{c.head}</div></li>
                ))}
              </ul>
            ) : <p className="text-[13px] text-muted">No multi-item clusters in this view.</p>}
          </Card>
        </div>
      </div>
    </>
  );
}
