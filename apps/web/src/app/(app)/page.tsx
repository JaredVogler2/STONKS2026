import Link from "next/link";
import { AutoRefresh } from "@/components/client/auto-refresh";
import { RegimeRibbon, SectorHeatmap } from "@/components/ui/market";
import {
  AgeBadge, Badge, Card, ConfidenceBadge, EmptyState, Notice, PageHeader, QualityMeter, SignalStateBadge, Stat, StatusPill, Table, Td, Th,
} from "@/components/ui/primitives";
import { ageSeconds, fmtAge, fmtEt, humanize, strategyLabel } from "@/lib/format";
import { pageOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { eventTimeline, ownerPositionCounts, recentCandidates, sectorBoard } from "@/server/services/dashboard";
import { computeHealth } from "@/server/services/health";
import { overview } from "@/server/services/queries";
import { getSettings } from "@/server/services/settings";

export const dynamic = "force-dynamic";
export const metadata = { title: "Command Center" };

type Json = Record<string, unknown>;

export default async function CommandCenter() {
  const owner = await pageOwner();
  const ctx = await appContext();
  const now = await ctx.now();
  const [ov, health, cands, sectors, timeline, mine, settings] = await Promise.all([
    overview(ctx.db), computeHealth(ctx.db, now), recentCandidates(ctx.db, 5, 15), sectorBoard(ctx.db), eventTimeline(ctx.db, 16),
    ownerPositionCounts(ctx.db, owner.userId), getSettings(ctx.db),
  ]);
  const scan = ov.scan as Json | null;
  const feed = (ov.feed as Json | null) ?? {};
  const regime = ov.regime as Json | null;
  const positions = ov.positions as Json;
  const comp = (k: string) => health.components.find((c) => c.key === k);
  const model = comp("model");
  const training = comp("training_schedule");
  const feedComp = comp("market_feed");
  const scanAge = ageSeconds(now, (scan?.finished_at ?? scan?.asof_at) as string | undefined);
  const eligible = Number(scan?.eligible_count ?? 0);
  const scanned = Number(scan?.scanned_count ?? 0);
  const coverage = eligible ? Math.round((scanned / eligible) * 100) : null;
  const subs = feed.subscriptions as number | undefined;
  const cap = (feed.subscription_cap ?? feed.plan_limit) as number | undefined;
  const latestScanCands = cands.filter((c) => c.scan_id === scan?.id);
  const champions = (ov.champions as Json[]).filter((c) => Boolean(c.is_demo) === ctx.isDemo);

  return (
    <>
      <AutoRefresh seconds={60} />
      <PageHeader title="Command Center"
        subtitle="What the scanner sees right now, what it published, and whether every input is fresh enough to trust. Quality ranks setups; only a calibrated model can attach a probability." />

      {health.overall !== "OK" ? (
        <Notice tone={health.overall === "DOWN" ? "neg" : "warn"} title={`System health: ${humanize(health.overall)}`}
          action={<Link href="/operations" className="text-xs font-medium underline">Open Operations</Link>}>
          {health.components.filter((c) => ["DOWN", "STALE", "DEGRADED"].includes(c.status)).map((c) => `${c.label}: ${c.detail ?? c.status}`).join(" · ")}
        </Notice>
      ) : null}

      <section aria-label="Key figures" className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-8">
        <Stat label="Scanned" value={scan ? String(scanned) : "n/a"} sub={coverage === null ? "no scan yet" : `${scanned}/${eligible} eligible · ${coverage}% coverage`}
          help="Symbols evaluated by the last end-of-day scan over symbols passing the universe filters" />
        <Stat label="Live slots" value={subs !== undefined ? `${subs} / ${cap ?? "?"}` : "n/a"} tone={subs !== undefined && cap && subs >= cap ? "warn" : "neutral"}
          sub={feed.feed ? `${String(feed.feed).toUpperCase()} feed (plan limit ${feed.plan_limit ?? "?"})` : "reported by the Pi heartbeat"}
          help="Streaming symbol slots in use on the Pi versus the configured cap (Alpaca Basic IEX allows 30)" />
        <Stat label="Candidates" value={scan ? String(scan.candidate_count ?? 0) : "n/a"} sub="latest scan, after hard vetoes" />
        <Stat label="Published" value={String(ov.published_today ?? 0)} sub={scan?.no_trade_reason ? "no-trade day" : "signals from the latest scan"} />
        <Stat label="My positions" value={<span className="sensitive">{mine.open}</span>} sub={`${mine.watching} watching · ${positions.members_with_positions ?? 0} members tracking`}
          help="Your own self-reported positions. Other members appear only as an aggregate count." />
        <Stat label="Feed age" value={fmtAge(feedComp?.age_seconds ?? null)} tone={feedComp?.status === "OK" ? "neutral" : "warn"}
          sub={ctx.isDemo ? "replay fixture" : feedComp?.detail ?? ""} help="Age of the newest cached quote" />
        <Stat label="Last scan" value={scanAge === null ? "never" : `${fmtAge(scanAge)} ago`} sub={scan ? fmtEt(scan.asof_at as string) : "start the Pi worker"}
          tone={scanAge !== null && scanAge > 4 * 86400 ? "warn" : "neutral"} />
        <Stat label="Calibration" value={model?.status === "OK" ? "Calibrated" : model?.status === "DEGRADED" ? (ctx.isDemo ? "DEMO control" : "Experimental") : model?.status === "STALE" ? "Expired" : "None"}
          tone={model?.status === "OK" ? "pos" : model?.status === "DEGRADED" ? (ctx.isDemo ? "demo" : "warn") : "neutral"}
          sub={training?.age_seconds != null ? `trained ${fmtAge(training.age_seconds)} ago` : "no training run yet"}
          help="Whether a validated, unexpired model can attach calibrated probabilities. Without one every signal is UNVALIDATED." />
      </section>

      {scan?.no_trade_reason ? (
        <Notice tone="neutral" title="No trade from the latest scan">
          {String(scan.no_trade_reason)}. Standing aside is a valid output: no setup cleared the quality, liquidity, earnings and concentration gates.
        </Notice>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Best candidates"
          definition="Top setups from the last 5 scans, gate-passing first, ranked by Quality (0–100 ranking, not a probability). Probability appears only with a calibrated champion."
          interval={scan ? `latest scan ${fmtEt(scan.asof_at as string)}` : undefined}
          actions={<Link href="/signals" className="text-xs font-medium text-info hover:underline">All signals →</Link>}>
          {cands.length ? (
            <Table caption="Best candidates">
              <thead>
                <tr><Th>Symbol</Th><Th>Setup</Th><Th align="right">Quality</Th><Th>Probability</Th><Th align="right">R/R</Th><Th align="right">Horizon</Th>
                  <Th>Sector</Th><Th>Decision</Th><Th>Quote</Th></tr>
              </thead>
              <tbody>
                {cands.map((c) => {
                  const qAge = ageSeconds(now, c.quote_received_at);
                  return (
                    <tr key={c.id} className="hover:bg-surface-2/60">
                      <Td className="font-medium">
                        {c.signal_id ? <Link href={`/signals/${c.signal_id}`} className="text-info hover:underline">{c.symbol}</Link> : c.symbol}
                        {c.scan_id !== scan?.id ? <span className="ml-1 text-[11px] text-faint">({fmtAge(ageSeconds(now, c.asof_at))} ago)</span> : null}
                      </Td>
                      <Td className="max-w-[180px] truncate text-muted" ><span title={strategyLabel(c.strategy_id)}>{strategyLabel(c.strategy_id)}</span></Td>
                      <Td align="right"><QualityMeter value={c.quality_score ?? 0} /></Td>
                      <Td>{c.signal_id ? <ConfidenceBadge status={c.confidence_status ?? "UNVALIDATED"} probability={c.confidence_probability} /> : <Badge>not published</Badge>}</Td>
                      <Td align="right">{c.reward_to_risk ?? "—"}</Td>
                      <Td align="right">{c.horizon_max_sessions ? `≤${c.horizon_max_sessions}s` : "—"}</Td>
                      <Td className="text-muted">{c.sector ? humanize(c.sector) : "—"}</Td>
                      <Td>
                        {c.signal_id ? <SignalStateBadge state={c.signal_state ?? "PUBLISHED"} />
                          : <Badge tone={c.gate_status === "PASS" ? "info" : "neutral"} title={c.reason_codes.join(", ")}>{c.gate_status.toLowerCase()}</Badge>}
                        {c.tier === "RESEARCH_SETUP" ? <span className="ml-1 text-[11px] text-faint">research</span> : null}
                      </Td>
                      <Td>
                        {c.quote_feed ? (
                          <span className="text-[11px] text-muted">{c.quote_feed.toUpperCase()} · {fmtAge(qAge)}</span>
                        ) : <span className="text-[11px] text-faint">no quote</span>}
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          ) : (
            <EmptyState title="No candidates in the last 5 scans" detail={scan ? `Most common veto: ${String(scan.no_trade_reason ?? "n/a")}` : "Pair and start the Pi worker to begin scanning."}
              action={scan ? "Review universe filters" : "Pair the Pi"} href={scan ? "/settings#market" : "/settings#pairing"} />
          )}
          {cands.length && !latestScanCands.length ? (
            <p className="mt-2 text-[11px] text-faint">The latest scan produced no candidates; rows above come from earlier scans (age shown).</p>
          ) : null}
        </Card>

        <Card title="Market regime" definition="Rule-based state estimate from benchmark trend, volatility and breadth. Descriptive context, not a forecast."
          interval={regime ? fmtEt(regime.asof_at as string) : undefined}>
          {regime ? (
            <RegimeRibbon current={String(regime.regime)} probabilities={(regime.state_probabilities as Record<string, number>) ?? {}}
              history={(ov.regime_history as { asof_at: string; regime: string }[]) ?? []} />
          ) : <EmptyState title="No regime estimate yet" detail="Computed by the Pi at each end-of-day scan." />}
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Sector strength and crowding"
          definition="Sector ETF 20-session log return, percentile rank among sectors (and 20-session change), share of member stocks above their 50-session average, and APEX's open signals per sector."
          interval={sectors.asof_at ? `as of ${fmtEt(sectors.asof_at)}` : undefined}>
          {sectors.rows.length ? (
            <SectorHeatmap rows={sectors.rows} maxPerSector={settings["risk.policy"].value.max_open_per_sector} />
          ) : <EmptyState title="Sector context not reported yet" detail="The Pi reports sector strength with each end-of-day scan." />}
          <p className="mt-2 text-[11px] text-faint">
            “Crowded” = open signals at the per-sector cap ({settings["risk.policy"].value.max_open_per_sector}); further setups in that sector are held to WATCH.
          </p>
        </Card>

        <Card title="Recent events" definition="Signal lifecycle changes, source outages, model changes and security rejects, newest first." interval="last 16">
          {timeline.length ? (
            <ol className="space-y-2.5">
              {timeline.map((t, i) => (
                <li key={`${t.at}-${i}`} className="grid grid-cols-[8px_minmax(0,1fr)] gap-2.5">
                  <span aria-hidden="true" className="mt-1.5 size-2 rounded-full" style={{
                    background: t.tone === "pos" ? "var(--pos)" : t.tone === "neg" ? "var(--neg)" : t.tone === "warn" ? "var(--warn)" : t.tone === "info" ? "var(--info)" : "var(--faint)" }} />
                  <div className="min-w-0">
                    <div className="truncate text-[13px]">
                      {t.href ? <Link href={t.href} className="hover:underline">{t.title}</Link> : t.title}
                    </div>
                    <div className="truncate text-[11px] text-faint">{fmtEt(t.at)}{t.detail ? ` · ${t.detail}` : ""}</div>
                  </div>
                </li>
              ))}
            </ol>
          ) : <EmptyState title="Nothing has happened yet" />}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Service health" definition="Each input is checked separately; one green light is never enough." interval={`checked ${fmtEt(health.generated_at)}`}
          actions={<Link href="/operations" className="text-xs font-medium text-info hover:underline">Details →</Link>}>
          <ul className="grid gap-2 sm:grid-cols-2">
            {health.components.map((c) => (
              <li key={c.key} className="flex min-w-0 items-start justify-between gap-2 rounded-lg border border-line px-3 py-2">
                <div className="min-w-0">
                  <div className="truncate text-[13px]">{c.label}</div>
                  <div className="truncate text-[11px] text-faint" title={c.detail}>{c.detail}</div>
                </div>
                <StatusPill status={c.status} />
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Champion models" definition="Validated models allowed to attach probabilities. Promotion requires the frozen gates; expiry forces UNVALIDATED."
          actions={<Link href="/models" className="text-xs font-medium text-info hover:underline">Models & Automation →</Link>}>
          {champions.length ? (
            <ul className="space-y-2">
              {champions.map((c) => (
                <li key={String(c.id)} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line px-3 py-2">
                  <div className="min-w-0">
                    <div className="truncate text-[13px] font-medium">{strategyLabel(String(c.strategy_id))}</div>
                    <div className="num truncate text-[11px] text-faint">{String(c.id)}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    {c.is_demo ? <Badge tone="demo">DEMO control</Badge> : null}
                    <AgeBadge seconds={ageSeconds(now, c.promoted_at as string)} prefix="promoted" warnAfter={8 * 86400} staleAfter={21 * 86400} />
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="No champion model" detail="Every published signal is labelled UNVALIDATED and carries no probability until a model passes the frozen promotion gates." />
          )}
        </Card>
      </div>
    </>
  );
}
