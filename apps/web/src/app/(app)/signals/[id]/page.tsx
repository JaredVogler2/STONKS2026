import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ActionButton } from "@/components/client/action-button";
import { CandleChart, type ChartEvent } from "@/components/client/candle-chart";
import { SignalReviewForm } from "@/components/client/signal-actions";
import { ReliabilityDiagram, type ReliabilityBin } from "@/components/charts/reliability";
import {
  AgeBadge, Badge, Card, ConfidenceBadge, DemoTag, EmptyState, KeyValue, Notice, PageHeader, QualityMeter, SignalStateBadge, Table, Td, Th,
} from "@/components/ui/primitives";
import { ageSeconds, fmtEt, fmtNum, fmtPct, humanize, strategyLabel } from "@/lib/format";
import { pageOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { etParts } from "@/server/market/calendar";
import { signalFull } from "@/server/services/explorer";
import { barsFor } from "@/server/services/queries";

export const dynamic = "force-dynamic";

type Json = Record<string, unknown>;
interface Evidence { code: string; group: string; text: string; value: number | null; unit?: string; asof_at: string }

const EVENT_TONE: Record<string, ChartEvent["tone"]> = {
  PUBLISHED: "info", ACTIVATED: "info", TARGET_REACHED: "pos", INTRADAY_TARGET_TOUCH: "pos", STOP_HIT: "neg", INTRADAY_STOP_TOUCH: "neg",
  INVALIDATED: "neg", EXPIRED: "neutral", CLOSED: "neutral", TIME_WARNING: "warn", STALE_DATA: "warn", ADMIN_REVIEW: "warn",
};
const TERMINAL = new Set(["CLOSED", "DISCARDED", "NO_TRADE", "EXPIRED", "INVALIDATED"]);

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  return { title: `Signal ${(await params).id.slice(0, 12)}` };
}

export default async function SignalDetail({ params }: { params: Promise<{ id: string }> }) {
  await pageOwner();
  const { id } = await params;
  const ctx = await appContext();
  const now = await ctx.now();
  const d = await signalFull(ctx.db, id);
  if (!d) notFound();
  const s = d.snapshot;
  const levels = s.levels as Json;
  const horizon = s.horizon as Json;
  const risk = s.risk as Json;
  const thesis = s.thesis as Json;
  const confidence = s.confidence as Json;
  const quote = s.quote as Json | undefined;
  const reasons = (thesis.reasons as Evidence[]) ?? [];
  const counter = (thesis.counter_evidence as Evidence[]) ?? [];
  const groups = ((d.candidate?.explanation_json as Json | undefined)?.group_scores as Record<string, number | null>) ?? {};
  const byGroup = reasons.reduce<Record<string, number>>((acc, r) => ({ ...acc, [r.group]: (acc[r.group] ?? 0) + 1 }), {});
  const correlated = Object.entries(byGroup).filter(([, n]) => n > 1);
  const bars = await barsFor(ctx.db, String(s.symbol), 200);
  const cutoff = etParts(now).date;
  const events: ChartEvent[] = d.events
    .filter((e) => e.event_type !== "CREATED" && e.event_type !== "QUALIFIED")
    .map((e) => ({ date: etParts(new Date(String(e.event_at))).date, label: `${humanize(String(e.event_type))}: ${String(e.cause ?? "")}`, tone: EVENT_TONE[String(e.event_type)] ?? "neutral" }));
  const closed = d.events.find((e) => e.to_state === "CLOSED" && e.from_state !== e.to_state);
  const closePayload = (closed?.payload_json as Json | undefined) ?? {};
  const evaluation = (d.model?.evaluation_json as Json | undefined) ?? null;
  const bins = ((evaluation?.holdout as Json | undefined)?.reliability as ReliabilityBin[] | undefined) ?? [];
  const quoteAgeNow = ageSeconds(now, d.quote?.ts_received as string | undefined);
  const terminal = TERMINAL.has(d.state);

  return (
    <>
      <div className="text-[12px] text-faint"><Link href="/signals" className="hover:text-fg">Signals</Link> / {String(s.symbol)}</div>
      <PageHeader title={`${String(s.symbol)} · ${strategyLabel(String(s.strategy_id))}`}
        subtitle={<>{String(s.company_name ?? "")} · {String(thesis.summary ?? "")}</>}
        actions={
          <>
            <ActionButton url="/api/v1/admin/positions" label="Watch in my positions" variant="secondary"
              body={{ client_idempotency_key: `dash-watch-${id}`, event_type: "WATCH", signal_id: id, symbol: s.symbol }}
              successMessage="Added to your watch list" duplicateMessage="Already on your watch list" />
          </>
        } />
      <div className="flex flex-wrap items-center gap-2">
        <SignalStateBadge state={d.state} outcome={d.outcome} />
        <Badge tone={s.tier === "HIGH_CONVICTION" ? "pos" : "neutral"}>{String(s.tier ?? "").replace(/_/g, " ").toLowerCase()}</Badge>
        <ConfidenceBadge status={String(confidence.status)} probability={(confidence.probability as number | undefined) ?? null} />
        <QualityMeter value={Number(s.quality_score)} />
        {s.is_demo ? <DemoTag /> : null}
        {d.review.suppressed ? <Badge tone="warn">alerts suppressed by owner</Badge> : null}
        <span className="text-[12px] text-faint">revision {d.revisions.length} · published {fmtEt(s.published_at as string)}</span>
      </div>
      {confidence.status === "UNVALIDATED" ? (
        <Notice tone="neutral" title="Unvalidated research setup">
          {String(confidence.label ?? "")}. No calibrated model backs this setup, so APEX states no probability of success. Treat it as a hypothesis to study.
        </Notice>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Price, plan and events"
          definition="Stored daily bars up to the current clock. Shaded band = entry zone during its validity; dashed lines = stop and primary target over the holding horizon; triangles = lifecycle events."
          interval={`bars through ${bars.at(-1)?.date ?? "n/a"} (${bars[0]?.feed ?? "no"} feed)`}>
          <CandleChart title={`${String(s.symbol)} daily`} bars={bars} cutoff={cutoff} events={events}
            levels={{ entryMin: Number(levels.entry_min), entryMax: Number(levels.entry_max), stop: Number(levels.stop_level), target: Number(levels.target_primary),
              asofDate: etParts(new Date(String(s.asof_at))).date, entryExpiresDate: etParts(new Date(String(horizon.entry_expires_at))).date,
              horizonSessions: Number(horizon.max_sessions) }} />
        </Card>
        <Card title="The plan as published" definition="Immutable levels from the original record. A stop is a reference level, not a guaranteed exit price.">
          <KeyValue items={[
            ["Entry zone", `${levels.entry_min} – ${levels.entry_max}`],
            ["Stop", <span key="s" className="text-neg">{String(levels.stop_level)}</span>],
            ["Primary target", <span key="t" className="text-pos">{String(levels.target_primary)}</span>],
            ...(levels.target_secondary ? [["Secondary target", String(levels.target_secondary)] as [string, string]] : []),
            ["Reward / risk", String(levels.reward_to_risk ?? "n/a")],
            ["Entry valid until", fmtEt(horizon.entry_expires_at as string)],
            ["Holding horizon", `${horizon.min_sessions}–${horizon.max_sessions} sessions (${String(horizon.basis ?? "").toLowerCase()})`],
            ["Max slippage", `${levels.max_slippage_bps ?? "n/a"} bps`],
            ["Earnings", humanize(String(risk.earnings_status ?? "unknown"))],
            ["Robinhood check", humanize(String(risk.tradability_status ?? "unknown"))],
            ["Sector", humanize(String(risk.sector ?? "unknown"))],
          ]} />
          <ul className="mt-3 list-disc space-y-1 pl-4 text-[12px] text-muted">
            {((levels.level_basis as string[]) ?? []).map((b) => <li key={b}>{b}</li>)}
            {risk.gap_risk_note ? <li>{String(risk.gap_risk_note)}</li> : null}
            {risk.notional_example ? <li>{String(risk.notional_example)}</li> : null}
          </ul>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Why it was selected" definition="Evidence at the decision time, strongest first. Evidence from one family (e.g. several momentum measures) is counted once.">
          <ol className="space-y-2">
            {reasons.map((r) => (
              <li key={r.code} className="text-[13px]">
                <div className="flex items-center gap-1.5"><Badge>{r.group}</Badge><span className="text-[11px] text-faint">{r.code}</span></div>
                <div className="mt-0.5">{r.text}</div>
              </li>
            ))}
          </ol>
          {correlated.length ? (
            <Notice tone="warn" title="Correlated evidence">
              {correlated.map(([g, n]) => `${n} items from the ${g} family`).join("; ")} measure related things; they are not independent confirmations.
            </Notice>
          ) : null}
          {Object.keys(groups).length ? (
            <div className="mt-3">
              <div className="mb-1 text-[11px] text-faint">Quality factor scores (0–1, before penalties)</div>
              <ul className="space-y-1">
                {Object.entries(groups).sort((a, b) => (b[1] ?? -1) - (a[1] ?? -1)).map(([g, v]) => (
                  <li key={g} className="grid grid-cols-[80px_minmax(0,1fr)_40px] items-center gap-2 text-xs">
                    <span className="text-muted">{g}</span>
                    <span aria-hidden="true" className="h-1.5 overflow-hidden rounded-full bg-surface-3">
                      {v !== null ? <span className="block h-full rounded-full bg-info/70" style={{ width: `${Math.round(v * 100)}%` }} /> : null}
                    </span>
                    <span className="num text-right">{v === null ? "n/a" : v.toFixed(2)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </Card>
        <Card title="Counter-evidence and risks" definition="What argues against the setup, including quality penalties.">
          {counter.length ? (
            <ul className="space-y-2">
              {counter.map((r) => (
                <li key={r.code} className="text-[13px]">
                  <div className="flex items-center gap-1.5"><Badge tone="warn">{r.group}</Badge><span className="text-[11px] text-faint">{r.code}</span></div>
                  <div className="mt-0.5">{r.text}</div>
                </li>
              ))}
            </ul>
          ) : <EmptyState title="No counter-evidence recorded" detail="Absence of recorded counter-evidence is not evidence of safety." />}
          {(thesis.regime as Json | undefined) ? (
            <p className="mt-3 text-[12px] text-muted">Regime at decision: {humanize(String((thesis.regime as Json).label))} (rule-based estimate).</p>
          ) : null}
        </Card>
        <Card title="Confidence and calibration" definition="Probability of the target before the stop within the horizon, only from a calibrated, unexpired model. Bins below are the model's untouched holdout.">
          <KeyValue items={[
            ["Status", String(confidence.status)],
            ["Statement", String(confidence.label ?? "")],
            ["Model", String(s.model_version)],
            ["Event definition", `${humanize(String((s.event_definition as Json).type))}, same-bar ${String((s.event_definition as Json).same_bar_rule).toLowerCase().replace(/_/g, " ")}`],
            ...(confidence.interval ? [["Interval", JSON.stringify(confidence.interval)] as [string, string]] : []),
          ]} />
          {bins.length ? (
            <div className="mt-3"><ReliabilityDiagram bins={bins} title="Holdout reliability" /></div>
          ) : (
            <p className="mt-3 text-[12px] text-muted">No calibration bins: this signal was not scored by a validated model.</p>
          )}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Freshness" definition="Quote used at the decision time versus the latest cached quote now.">
          <KeyValue items={[
            ["Decision quote", quote ? `${String(quote.price)} (${String((quote.provenance as Json)?.feed ?? "?")}, ${String((quote.provenance as Json)?.latency_class ?? "?")})` : "n/a"],
            ["Age at decision", quote ? `${String(quote.age_seconds)}s` : "n/a"],
            ["Latest quote", d.quote ? `${fmtNum(d.quote.price)} (${String(d.quote.feed)})` : "none cached"],
            ["Latest quote age", d.quote ? <AgeBadge key="a" seconds={quoteAgeNow} prefix="received" /> : "n/a"],
            ["Feature snapshot", d.features ? `as of ${fmtEt(d.features.asof_at)}` : "not uploaded"],
          ]} />
        </Card>
        <Card title="Result" definition="Theoretical outcome of the published plan (first touch on daily bars, conservative same-bar rule). Members' own fills are tracked separately.">
          {d.state === "CLOSED" ? (
            <KeyValue items={[
              ["Outcome", humanize(String(d.outcome ?? closePayload.outcome ?? "n/a"))],
              ["Modeled R multiple", closePayload.r_multiple_modeled !== undefined ? fmtNum(closePayload.r_multiple_modeled) : "n/a"],
              ["Modeled return", closePayload.return_modeled !== undefined ? fmtPct(closePayload.return_modeled, 2, true) : "n/a"],
              ["Closed", fmtEt(closed?.event_at as string)],
            ]} />
          ) : TERMINAL.has(d.state) ? (
            <p className="text-[13px] text-muted">Ended as {humanize(d.state)} without a filled outcome. No-fills and expiries count in the published track record.</p>
          ) : <p className="text-[13px] text-muted">Still open. The result is recorded when the target, stop or time exit is reached.</p>}
        </Card>
      </div>

      <Card title="Lifecycle" definition="Original record and every later event, oldest first. Revisions never rewrite earlier ones.">
        <Table caption="Signal events">
          <thead><tr><Th>When</Th><Th>Event</Th><Th>From → to</Th><Th>Cause</Th></tr></thead>
          <tbody>
            {d.events.map((e, i) => (
              <tr key={i}>
                <Td className="whitespace-nowrap text-muted">{fmtEt(e.event_at as string)}</Td>
                <Td><Badge tone={EVENT_TONE[String(e.event_type)] === "neutral" ? "neutral" : (EVENT_TONE[String(e.event_type)] ?? "neutral")}>{humanize(String(e.event_type))}</Badge></Td>
                <Td className="whitespace-nowrap text-[12px] text-muted">{String(e.from_state ?? "—").toLowerCase()} → {String(e.to_state).toLowerCase()}</Td>
                <Td className="text-[13px]">{String(e.cause ?? "")}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
        {d.revisions.length > 1 ? (
          <div className="mt-4">
            <h3 className="mb-1 text-[12px] font-semibold text-muted">Revisions</h3>
            <Table caption="Signal revisions">
              <thead><tr><Th>Rev</Th><Th align="right">Quality</Th><Th>Confidence</Th><Th align="right">Entry</Th><Th align="right">Stop</Th><Th align="right">Target</Th><Th>As of</Th></tr></thead>
              <tbody>
                {d.revisions.map((r) => (
                  <tr key={String(r.revision)}>
                    <Td>r{String(r.revision)}</Td><Td align="right">{fmtNum(r.quality_score, 1)}</Td><Td>{String(r.confidence_status)}</Td>
                    <Td align="right">{fmtNum(r.entry_min)}–{fmtNum(r.entry_max)}</Td><Td align="right">{fmtNum(r.stop_level)}</Td>
                    <Td align="right">{fmtNum(r.target_primary)}</Td><Td className="text-muted">{fmtEt(r.asof_at as string)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>
        ) : null}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Raw feature values" definition="Exact inputs behind the evidence, as computed by the Pi. Blank = insufficient history (never zero-filled)."
          interval={d.features ? `integrity ${d.features.integrity_hash.slice(0, 12)}…` : undefined}>
          {d.features ? (
            <details>
              <summary className="cursor-pointer text-[13px] text-info">Show {Object.keys(d.features.features).length} features</summary>
              <dl className="num mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-[12px] sm:grid-cols-2">
                {Object.entries(d.features.features).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-2 border-b border-line/50 py-0.5">
                    <dt className="truncate text-muted">{k}</dt><dd>{v === null ? "" : Math.abs(v) >= 1000 ? v.toFixed(0) : v.toFixed(4)}</dd>
                  </div>
                ))}
              </dl>
            </details>
          ) : <EmptyState title="No feature snapshot uploaded for this signal" detail="Older Pi versions did not upload snapshots." />}
        </Card>
        <Card title="Catalysts around the decision" definition="Filings and news for this symbol from 45 days before to 30 days after the decision, by the time APEX could have known them.">
          {d.catalysts.length ? (
            <ul className="space-y-2">
              {d.catalysts.map((c) => (
                <li key={String(c.id)} className="text-[13px]">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge tone={c.direction === "potentially_positive" ? "pos" : c.direction === "potentially_negative" ? "neg" : "neutral"}>{humanize(String(c.catalyst_type))}</Badge>
                    <span className="text-[11px] text-faint">{fmtEt(c.available_at as string)} · {String(c.source)} · grade {String(c.source_grade)}</span>
                    {new Date(String(c.available_at)) > new Date(String(s.asof_at)) ? <Badge>after decision</Badge> : null}
                  </div>
                  <div className="mt-0.5">
                    {c.canonical_url ? (
                      <a href={String(c.canonical_url)} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 hover:underline">
                        {String(c.headline)} <ExternalLink aria-hidden="true" className="size-3" />
                      </a>
                    ) : String(c.headline)}
                  </div>
                </li>
              ))}
            </ul>
          ) : <EmptyState title="No catalysts recorded for this symbol in that window" />}
        </Card>
      </div>

      <Card title="Owner review" definition="Recorded in the signal's history and the audit log.">
        {d.review.notes.length ? (
          <ul className="mb-3 space-y-1 text-[13px]">
            {d.review.notes.map((n, i) => <li key={i}><span className="text-faint">{fmtEt(n.event_at as string)}</span> · {String(n.cause)}</li>)}
          </ul>
        ) : null}
        <SignalReviewForm signalId={id} terminal={terminal} suppressed={d.review.suppressed} />
      </Card>
    </>
  );
}
