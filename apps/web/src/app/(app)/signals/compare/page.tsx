import Link from "next/link";
import { Badge, Card, ConfidenceBadge, EmptyState, Notice, PageHeader, QualityMeter, SignalStateBadge } from "@/components/ui/primitives";
import { buttonClass } from "@/lib/button";
import { fmtEt, fmtNum, humanize, strategyLabel } from "@/lib/format";
import { pageOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { exploreSignals, signalFull, type SignalFull } from "@/server/services/explorer";

export const dynamic = "force-dynamic";
export const metadata = { title: "Compare signals" };

type Json = Record<string, unknown>;

/** Expected value in R units of the published plan: p·RR − (1−p)·1 − cost. Only defined for calibrated probabilities. */
function expectedR(d: SignalFull): number | null {
  const c = d.snapshot.confidence as Json;
  const p = c.status === "CALIBRATED" && typeof c.probability === "number" ? c.probability : null;
  const rr = Number((d.snapshot.levels as Json).reward_to_risk);
  if (p === null || !Number.isFinite(rr)) return null;
  const costBps = Number((d.snapshot.risk as Json).cost_bps_per_side ?? 10) * 2;
  const lv = d.snapshot.levels as Json;
  const mid = (Number(lv.entry_min) + Number(lv.entry_max)) / 2;
  const riskPerShare = mid - Number(lv.stop_level);
  const costR = riskPerShare > 0 ? ((costBps / 10_000) * mid) / riskPerShare : 0;
  return p * rr - (1 - p) - costR;
}

function Column({ d, eu }: { d: SignalFull; eu: number | null }) {
  const s = d.snapshot;
  const lv = s.levels as Json;
  const regime = (s.thesis as Json).regime as Json | undefined;
  return (
    <div className="space-y-3">
      <div>
        <Link href={`/signals/${String(s.id)}`} className="text-base font-semibold text-info hover:underline">{String(s.symbol)}</Link>
        <div className="text-[12px] text-muted">{strategyLabel(String(s.strategy_id))}</div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <SignalStateBadge state={d.state} outcome={d.outcome} />
        <ConfidenceBadge status={String((s.confidence as Json).status)} probability={((s.confidence as Json).probability as number | undefined) ?? null} />
      </div>
      <dl className="num grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[13px]">
        <dt className="text-muted">Quality</dt><dd><QualityMeter value={Number(s.quality_score)} /></dd>
        <dt className="text-muted">Expected value</dt>
        <dd>{eu === null ? <span className="text-muted">not computable (no calibrated probability)</span> : `${eu >= 0 ? "+" : ""}${eu.toFixed(2)} R after costs`}</dd>
        <dt className="text-muted">Reward / risk</dt><dd>{String(lv.reward_to_risk)}</dd>
        <dt className="text-muted">Entry · stop · target</dt><dd>{fmtNum(lv.entry_min)}–{fmtNum(lv.entry_max)} · {fmtNum(lv.stop_level)} · {fmtNum(lv.target_primary)}</dd>
        <dt className="text-muted">Horizon</dt><dd>≤{String((s.horizon as Json).max_sessions)} sessions</dd>
        <dt className="text-muted">Regime then</dt><dd>{regime ? humanize(String(regime.label)) : "n/a"}</dd>
        <dt className="text-muted">Sector</dt><dd>{humanize(String((s.risk as Json).sector ?? "n/a"))}</dd>
        <dt className="text-muted">Earnings</dt><dd>{humanize(String((s.risk as Json).earnings_status ?? "n/a"))}</dd>
        <dt className="text-muted">As of</dt><dd>{fmtEt(String(s.asof_at))}</dd>
      </dl>
    </div>
  );
}

export default async function ComparePage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  await pageOwner();
  const sp = await searchParams;
  const ctx = await appContext();
  const now = await ctx.now();
  const a = typeof sp.a === "string" ? sp.a : undefined;
  const b = typeof sp.b === "string" ? sp.b : undefined;
  const [da, db, recent] = await Promise.all([a ? signalFull(ctx.db, a) : null, b ? signalFull(ctx.db, b) : null,
    exploreSignals(ctx.db, { pageSize: 60, sort: "asof", dir: "desc" }, now)]);
  const eua = da ? expectedR(da) : null;
  const eub = db ? expectedR(db) : null;
  const both = da && db;
  const winner = both && eua !== null && eub !== null ? (eua > eub ? "a" : eub > eua ? "b" : null) : null;
  const sameRegime = both && ((da.snapshot.thesis as Json).regime as Json | undefined)?.label === ((db.snapshot.thesis as Json).regime as Json | undefined)?.label;
  const options = recent.rows.map((r) => ({ id: r.id, label: `${r.symbol} · ${strategyLabel(r.strategy_id)} · ${fmtEt(r.asof_at)}` }));

  return (
    <>
      <PageHeader title="Compare signals"
        subtitle="Side by side on the published plan, expected value (only when both are calibrated) and the market regime at decision time." />
      <Card title="Choose two signals">
        <form method="get" className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
          {(["a", "b"] as const).map((k) => (
            <label key={k} className="flex min-w-0 flex-col gap-1">
              <span className="text-[11px] text-faint">Signal {k.toUpperCase()}</span>
              <select name={k} defaultValue={k === "a" ? a : b} className="min-h-9 min-w-0 rounded-lg border border-line-strong bg-surface-2 px-2 text-[13px]">
                <option value="">Select…</option>
                {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </select>
            </label>
          ))}
          <div className="flex items-end"><button type="submit" className={buttonClass("primary")}>Compare</button></div>
        </form>
      </Card>
      {both ? (
        <>
          {winner ? (
            <Notice tone="info" title={`Higher expected value: ${String((winner === "a" ? da : db).snapshot.symbol)}`}>
              Based on calibrated probabilities, the published levels and round-trip costs. {sameRegime ? "" : "The two decisions were made in different market regimes, which limits the comparison."}
            </Notice>
          ) : (
            <Notice tone="neutral" title="No winner declared">
              At least one of these signals has no calibrated probability, so expected value cannot be computed. Quality ranks setups but is not a
              probability, so APEX does not pick a winner on Quality alone.
            </Notice>
          )}
          <div className="grid gap-4 md:grid-cols-2">
            <Card title="Signal A"><Column d={da} eu={eua} /></Card>
            <Card title="Signal B"><Column d={db} eu={eub} /></Card>
          </div>
          {!sameRegime ? <Badge tone="warn">different regimes at decision time</Badge> : null}
        </>
      ) : (
        <EmptyState title="Pick two signals to compare" detail="Or use the “compare” link on any row of the Signals Explorer." action="Open Signals Explorer" href="/signals" />
      )}
    </>
  );
}
