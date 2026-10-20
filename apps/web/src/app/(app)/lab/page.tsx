import Link from "next/link";
import { EquityDrawdown, HBarChart, Histogram } from "@/components/charts/basic";
import { ReliabilityDiagram, type ReliabilityBin } from "@/components/charts/reliability";
import { StrategyToggles } from "@/components/client/strategy-toggles";
import { Badge, Card, confidenceTone, EmptyState, KeyValue, Notice, PageHeader, Table, Td, Th } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import { fmtEt, fmtNum, fmtPct, humanize, STRATEGY_LABELS, strategyLabel } from "@/lib/format";
import { pageOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { research } from "@/server/services/queries";
import { getSettings } from "@/server/services/settings";

export const dynamic = "force-dynamic";
export const metadata = { title: "Strategy & Model Lab" };

type Json = Record<string, unknown>;
const n = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);

const GLOSSARY: [string, string][] = [
  ["Quality (0–100)", "A ranking of how well a setup matches its strategy's pre-registered pattern, after penalties. It orders setups against each other. It is NOT a probability and a 90 does not mean a 90% chance."],
  ["Calibrated probability", "The chance that the target is reached before the stop within the horizon, from a model whose stated probabilities matched observed frequencies on data it never trained on. Only CALIBRATED signals show one."],
  ["UNVALIDATED", "The strategy has not shown out-of-sample evidence that clears the frozen gates. Setups are published only as labelled research, without any probability."],
  ["EXPERIMENTAL", "Some evidence, but the sample is too small or unstable to rely on. Shown for study, never as a high-conviction alert."],
  ["Brier skill", "Improvement of the model's probability error over always predicting the training base rate. 0 = no better than the base rate; negative = worse."],
  ["ECE", "Expected calibration error: the average gap between predicted and observed frequency across probability bins. Lower is better."],
  ["Walk-forward + holdout", "The model is trained only on the past and tested on the next period, repeatedly; a final untouched holdout is scored once. Overlapping labels are purged and embargoed so the future cannot leak in."],
  ["Shuffled-label control", "The same pipeline run with labels randomly permuted. It must find no skill; if it does, the pipeline is leaking information."],
  ["Red-team planted alpha", "A synthetic dataset with a known, planted edge. The pipeline must find it, which proves it can detect real skill when it exists."],
  ["R multiple", "Profit or loss in units of the initial risk (entry minus stop). +2R means twice the amount risked."],
  ["Expected shortfall (5%)", "Average return of the worst 5% of outcomes: the size of a bad case, not just its likelihood."],
];

export default async function LabPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  await pageOwner();
  const sp = await searchParams;
  const ctx = await appContext();
  const [r, settings] = await Promise.all([research(ctx.db), getSettings(ctx.db)]);
  const reports = r.backtests.map((b) => ({ ...b, report: (b.metrics_json ?? {}) as Json }) as Json & { report: Json });
  const real = reports.filter((b) => b.strategy_id !== "redteam_planted_alpha");
  const selectedId = typeof sp.s === "string" ? sp.s : (real[0]?.id as string | undefined) ?? (reports[0]?.id as string | undefined);
  const sel = reports.find((b) => b.id === selectedId) ?? reports[0];
  const rep = (sel?.report ?? {}) as Json;
  const hold = (rep.holdout ?? {}) as Json;
  const econ = (rep.economics_all ?? {}) as Json;
  const gates = (rep.gates ?? {}) as Json;
  const strat = (rep.strategy ?? null) as Json | null;
  const toggleRows = Object.keys(STRATEGY_LABELS).filter((k) => k !== "redteam_planted_alpha").map((id) => {
    const b = real.find((x) => x.strategy_id === id);
    const sr = (b?.report.strategy ?? {}) as Json;
    return { id, label: strategyLabel(id), defaultEnabled: sr.default_enabled === undefined ? true : Boolean(sr.default_enabled),
      status: (b?.report.confidence_status as string | undefined) ?? null };
  });

  return (
    <>
      <PageHeader title="Strategy & Model Lab"
        subtitle="Each strategy is a pre-registered hypothesis tested out of sample with conservative fills and costs. Failures are kept and shown; a model goes live only through the frozen promotion gates." />

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Evidence by strategy" definition="Untouched holdout metrics and economics net of 25 bps round-trip costs (synthetic DEMO data in this mode)."
          interval={`${reports.length} reports`}>
          {reports.length ? (
            <Table caption="Strategy evidence">
              <thead><tr><Th>Strategy</Th><Th>Status</Th><Th align="right">Holdout n</Th><Th align="right">Brier skill</Th><Th align="right">ECE</Th>
                <Th align="right">Mean net</Th><Th align="right">p10 (CI)</Th><Th align="right">Target-first</Th><Th>Data</Th></tr></thead>
              <tbody>
                {reports.map((b) => {
                  const rp = b.report;
                  const h = (rp.holdout ?? {}) as Json;
                  const e = (rp.economics_all ?? {}) as Json;
                  const ci = (e.mean_return_net_ci ?? {}) as Json;
                  return (
                    <tr key={String(b.id)} className={cn("hover:bg-surface-2/60", b.id === sel?.id && "bg-surface-2")}>
                      <Td><Link href={`/lab?s=${String(b.id)}`} className="text-info hover:underline" aria-current={b.id === sel?.id ? "true" : undefined}>{strategyLabel(String(b.strategy_id))}</Link></Td>
                      <Td><Badge tone={confidenceTone(rp.confidence_status as string)}>{String(rp.confidence_status ?? "n/a").toLowerCase()}</Badge></Td>
                      <Td align="right">{String(h.n ?? "—")}</Td>
                      <Td align="right" className={n(h.brier_skill) === null ? "" : (n(h.brier_skill) ?? 0) > 0 ? "text-pos" : "text-neg"}>{n(h.brier_skill) === null ? "—" : fmtNum(h.brier_skill, 3)}</Td>
                      <Td align="right">{n(h.ece) === null ? "—" : fmtNum(h.ece, 3)}</Td>
                      <Td align="right">{n(e.mean_return_net) === null ? "—" : fmtPct(e.mean_return_net, 2, true)}</Td>
                      <Td align="right" className={(n(ci.p10) ?? 0) > 0 ? "text-pos" : "text-muted"}>{n(ci.p10) === null ? "—" : fmtPct(ci.p10, 2, true)}</Td>
                      <Td align="right">{n(e.target_first_rate) === null ? "—" : fmtPct(e.target_first_rate, 0)}</Td>
                      <Td className="text-[12px] text-muted">{humanize(String(b.data_source))}</Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          ) : <EmptyState title="No research reports yet" detail="Reports arrive from the weekly training workflow (or `apex train` locally)." action="Models & Automation" href="/models" />}
        </Card>
        <Card title="Strategy switches" definition="Which hypotheses the Pi scans for. Versioned and audited." interval={`setting v${settings["strategies.enabled"].version}`}>
          <StrategyToggles rows={toggleRows} overrides={settings["strategies.enabled"].value} version={settings["strategies.enabled"].version} />
        </Card>
      </div>

      {sel ? (
        <>
          <Card title={strategyLabel(String(sel.strategy_id))} definition={strat ? String(strat.hypothesis) : "Hypothesis text not included in this report version."}
            interval={`report ${fmtEt(sel.completed_at as string)} · ${String(rep.policy_version ?? "")}`}>
            <div className="grid gap-4 lg:grid-cols-3">
              <div className="space-y-3">
                <div className="flex flex-wrap gap-1.5">
                  <Badge tone={confidenceTone(rep.confidence_status as string)}>{String(rep.confidence_status ?? "n/a")}</Badge>
                  {strat?.weak_prior ? <Badge tone="warn">weak prior</Badge> : null}
                  {strat ? <Badge>v{String(strat.version)}</Badge> : null}
                  {sel.is_demo ? <Badge tone="demo">synthetic data</Badge> : null}
                </div>
                <ul className="list-disc space-y-1 pl-4 text-[12px] text-muted">
                  {((rep.status_reasons as string[]) ?? []).map((x) => <li key={x}>{x}</li>)}
                </ul>
                {strat ? (
                  <div>
                    <div className="text-[11px] text-faint">Known failure modes</div>
                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[12px]">{((strat.failure_modes as string[]) ?? []).map((x) => <li key={x}>{x}</li>)}</ul>
                    <div className="mt-2 text-[11px] text-faint">Allowed regimes: {((strat.allowed_regimes as string[]) ?? []).map(humanize).join(", ")}</div>
                  </div>
                ) : null}
              </div>
              <div>
                <h3 className="mb-2 text-[12px] font-semibold text-muted">Promotion gates (frozen)</h3>
                <KeyValue items={[
                  ["Holdout entries", String(gates.holdout_entries ?? "—")],
                  ["Distinct dates", String(gates.distinct_dates ?? "—")],
                  ["Regimes covered", String(gates.regimes ?? "—")],
                  ["Brier skill", n(gates.brier_skill) === null ? "—" : fmtNum(gates.brier_skill, 3)],
                  ["ECE", n(gates.ece) === null ? "—" : fmtNum(gates.ece, 3)],
                  ["Shuffled-label skill", n(gates.shuffled_skill) === null ? "—" : fmtNum(gates.shuffled_skill, 3)],
                  ["Selected trades", String(gates.selected_trades ?? "—")],
                  ["Selected p10 net", n(gates.selected_mean_lower_p10) === null ? "—" : fmtPct(gates.selected_mean_lower_p10, 2, true)],
                ]} />
              </div>
              <div>
                <h3 className="mb-2 text-[12px] font-semibold text-muted">Holdout calibration</h3>
                {((hold.reliability as ReliabilityBin[]) ?? []).length ? <ReliabilityDiagram bins={hold.reliability as ReliabilityBin[]} title="Holdout reliability" />
                  : <p className="text-[12px] text-muted">No bins (too few holdout events).</p>}
              </div>
            </div>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Baselines vs strategy" definition="Mean net return per trade (25 bps) of simple baselines on the same dates; a strategy must beat them to matter.">
              <HBarChart title="Mean net return by approach" unit="mean net return per trade" format={(v) => fmtPct(v, 2, true)}
                data={[{ label: "This strategy (all)", value: n(econ.mean_return_net) },
                  ...Object.entries((rep.baselines ?? {}) as Record<string, Json>).map(([k, v]) => ({ label: humanize(k), value: n(v.mean_return_net), note: `n=${String(v.n)}` }))]} />
            </Card>
            <Card title="Performance by market regime" definition="Holdout mean net return per trade by the regime at decision time.">
              <HBarChart title="Return by regime" unit="mean net return per trade (holdout, 25 bps)" format={(v) => fmtPct(v, 2, true)}
                data={Object.entries((rep.regime_breakdown ?? {}) as Record<string, Json>).map(([k, v]) => ({ label: `${humanize(k)} (n=${String(v.n)})`, value: n(v.mean_return_net_25bps) }))} />
            </Card>
            <Card title="Sequential equity and drawdown" definition="Illustrative: one unit per signal compounded in decision order; overlapping trades are not capital-constrained.">
              <EquityDrawdown title="Equity and drawdown" points={(econ.drawdown_curve as { date: string; equity: number; drawdown: number }[]) ?? []} />
            </Card>
            <Card title="Time to exit" definition="Sessions from fill to exit by how the trade ended (first touch on daily bars).">
              {econ.time_to_exit_hist ? (
                <Histogram title="Time to exit" series={(["TARGET", "STOP", "TIME"] as const).map((k) => ({ name: k === "TIME" ? "time exit" : k.toLowerCase(),
                  color: k === "TARGET" ? "var(--pos)" : k === "STOP" ? "var(--neg)" : "var(--faint)",
                  bins: ((econ.time_to_exit_hist as Record<string, { bucket: string; n: number }[]>)[k] ?? []) }))} />
              ) : <p className="text-[12px] text-muted">Not in this report version.</p>}
            </Card>
            <Card title="Effect of costs" definition="Selected-trade mean net return as round-trip costs rise.">
              {Object.values((rep.cost_sensitivity ?? {}) as Record<string, Json>).some((v) => Number(v.n) > 0) ? (
                <HBarChart title="Cost sensitivity" unit="mean net return per selected trade" format={(v) => fmtPct(v, 2, true)}
                  data={Object.entries((rep.cost_sensitivity ?? {}) as Record<string, Json>).map(([k, v]) => ({ label: k, value: n(v.mean_return_net), note: `n=${String(v.n)}` }))} />
              ) : <p className="text-[13px] text-muted">No trades cleared the selection threshold, so there is nothing to cost. That is itself the result.</p>}
            </Card>
            <Card title="Economics (all entries)" definition="Every theoretical entry, not just selected ones. CI = weekly block bootstrap.">
              <KeyValue items={[
                ["Entries / candidates", `${String(econ.entries ?? "—")} / ${String(econ.candidates ?? "—")}`],
                ["Target / stop / time first", `${fmtPct(econ.target_first_rate, 0)} / ${fmtPct(econ.stop_first_rate, 0)} / ${fmtPct(econ.time_expiry_rate, 0)}`],
                ["Mean net (p10–p90)", `${fmtPct(econ.mean_return_net, 2, true)} (${fmtPct((econ.mean_return_net_ci as Json | undefined)?.p10, 2, true)} – ${fmtPct((econ.mean_return_net_ci as Json | undefined)?.p90, 2, true)})`],
                ["Median R", fmtNum(econ.median_r_multiple)],
                ["Profit factor", econ.profit_factor === null || econ.profit_factor === undefined ? "n/a (<30 trades)" : fmtNum(econ.profit_factor)],
                ["Expected shortfall 5%", fmtPct(econ.expected_shortfall_5, 2, true)],
                ["Same-bar ambiguity", fmtPct(econ.ambiguous_same_bar_rate, 1)],
                ["Gap through stop", fmtPct(econ.gap_through_stop_rate, 1)],
              ]} />
            </Card>
          </div>
          {((sel.bias_warnings as string[]) ?? []).length ? (
            <Notice tone="warn" title="Bias warnings">{(sel.bias_warnings as string[]).join(" · ")}</Notice>
          ) : null}
          <Notice tone="neutral" title="Not yet computed in this report version">
            Parameter-robustness sweeps and feature ablations are not run by the weekly job (minutes budget). Their absence is shown rather than implied.
          </Notice>
        </>
      ) : null}

      <Card title="Glossary: Quality vs probability" definition="Tap a term.">
        <div className="grid gap-2 md:grid-cols-2">
          {GLOSSARY.map(([k, v]) => (
            <details key={k} className="rounded-lg border border-line px-3 py-2 open:bg-surface-2">
              <summary className="cursor-pointer text-[13px] font-medium">{k}</summary>
              <p className="mt-1.5 text-[13px] text-muted">{v}</p>
            </details>
          ))}
        </div>
      </Card>
    </>
  );
}
