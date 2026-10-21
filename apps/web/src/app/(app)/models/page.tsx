import { Play, RotateCcw } from "lucide-react";
import { ActionButton } from "@/components/client/action-button";
import { Badge, Card, confidenceTone, EmptyState, KeyValue, Notice, PageHeader, StatusPill, Table, Td, Th } from "@/components/ui/primitives";
import { BudgetGauge } from "@/components/ui/gauge";
import { ageSeconds, fmtAge, fmtBytes, fmtEt, fmtNum, humanize, strategyLabel } from "@/lib/format";
import { pageOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { modelsPanel } from "@/server/services/dashboard";
import { computeHealth } from "@/server/services/health";
import { getSettings, listIntegrations } from "@/server/services/settings";

export const dynamic = "force-dynamic";
export const metadata = { title: "Models & Automation" };

type Json = Record<string, unknown>;
// Worst-case month (same profile as apex_worker/release/budget.py): 5 Mondays, 23 weekdays, every run at its timeout.
const DRIFT_TIMEOUT_MIN = 10; // per-run cap in apex-model-drift.yml
const RELEASE_TIMEOUT_MIN = 10; // per-run cap in apex-model-release.yml (after a training, promotion_mode=auto only)
const MAX_MONDAYS = 5;
const MAX_WEEKDAYS = 23;

export default async function ModelsPage() {
  await pageOwner();
  const ctx = await appContext();
  const now = await ctx.now();
  const [panel, settings, integrations, health] = await Promise.all([modelsPanel(ctx.db, ctx.isDemo, now), getSettings(ctx.db), listIntegrations(ctx.db),
    computeHealth(ctx.db, now)]);
  const versions = panel.versions as Json[];
  const policy = settings["models.policy"];
  const budgets = settings.budgets.value;
  const gh = integrations.find((i) => i.provider === "github");
  const ghReady = gh?.fields.every((f) => f.configured) ?? false;
  const maxMonthly = budgets.train_timeout_minutes * MAX_MONDAYS + (budgets.drift_enabled ? DRIFT_TIMEOUT_MIN * MAX_WEEKDAYS : 0)
    + (policy.value.promotion_mode === "auto" ? RELEASE_TIMEOUT_MIN * MAX_MONDAYS : 0);
  const used = Number(panel.minutes_month ?? 0);
  const strategies = [...new Set(versions.map((v) => String(v.strategy_id)))];
  const trainHealth = health.components.find((c) => c.key === "training_schedule");
  const modelHealth = health.components.find((c) => c.key === "model");
  const lastOk = (panel.last_train_ok ?? panel.last_local) as Json | null;
  const lastDrift = panel.last_drift as Json | null;
  const dispatchDisabled = ctx.isDemo ? "DEMO mode: run `uv run apex train --source synthetic` locally instead (no GitHub minutes used)."
    : !ghReady ? "Configure Settings → Integrations → GitHub (fine-grained token with Actions read/write on this repo, plus owner/repo)." : undefined;

  return (
    <>
      <PageHeader title="Models & Automation"
        subtitle="Weekly retraining runs on GitHub-hosted runners, candidates are signed and registered here, and the Pi pulls, verifies and swaps the approved model by itself. Nothing here needs your laptop." />

      <section aria-label="Automation status" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card title="Weekly training" interval={`next ≈ ${String(panel.next_train)}`}>
          <div className="flex items-center justify-between gap-2"><StatusPill status={trainHealth?.status ?? "UNKNOWN"} /><span className="text-[12px] text-muted">{trainHealth?.detail}</span></div>
          <p className="mt-2 text-[12px] text-muted">Last success: {lastOk ? `${fmtEt((lastOk.finished_at ?? lastOk.started_at) as string)} (${humanize(String(lastOk.workflow))})` : "never"}</p>
          <p className="text-[11px] text-faint">Missed-run alert after 8 days without a success (Pi #system-status notice).</p>
        </Card>
        <Card title="Daily drift check" interval={`next ≈ ${String(panel.next_drift)}`}>
          {lastDrift ? (
            <>
              <Badge tone={lastDrift.outcome === "drift_breach" ? "neg" : lastDrift.outcome === "drift_ok" ? "pos" : "neutral"}>{humanize(String(lastDrift.outcome ?? lastDrift.status))}</Badge>
              <p className="mt-2 text-[12px] text-muted">Last run {fmtEt(lastDrift.started_at as string)}</p>
            </>
          ) : <p className="text-[13px] text-muted">UNKNOWN: no drift run reported yet{budgets.drift_enabled ? "" : " (disabled in budgets)"}.</p>}
        </Card>
        <Card title="Calibrated model" interval={modelHealth?.status === "OK" ? "serving" : undefined}>
          <StatusPill status={modelHealth?.status ?? "UNKNOWN"} />
          <p className="mt-2 text-[12px] text-muted">{modelHealth?.detail}</p>
        </Card>
        <Card title="Actions minutes" definition="Shared with every workflow on the account; app checks are not a billing safeguard.">
          <BudgetGauge used={used} budget={budgets.actions_monthly_minutes} label="this month (reported by runs)" />
          <p className="mt-2 text-[11px] text-faint">Max possible: {fmtNum(maxMonthly, 0)} min/month in a 5-Monday month ({budgets.train_timeout_minutes} min × {MAX_MONDAYS}{budgets.drift_enabled ? ` + ${DRIFT_TIMEOUT_MIN} min × ${MAX_WEEKDAYS} weekdays` : ""}{policy.value.promotion_mode === "auto" ? ` + ${RELEASE_TIMEOUT_MIN} min × ${MAX_MONDAYS} auto-releases` : ""}), plus CI. Set the GitHub spending limit to $0.</p>
        </Card>
      </section>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Champion / challenger" definition="Per strategy: the serving champion and newer candidates with the frozen promotion gate result. Promotion re-runs the gate server-side; a failing candidate cannot be promoted by clicking."
          interval={ctx.isDemo ? "DEMO registry" : "live registry"}>
          {strategies.length ? strategies.map((sid) => {
            const rows = versions.filter((v) => v.strategy_id === sid);
            const champ = rows.find((v) => v.status === "champion");
            return (
              <div key={sid} className="mb-5 last:mb-0">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-[13px] font-semibold">{strategyLabel(sid)}</h3>
                  {champ?.rollback_parent ? (
                    <ActionButton url="/api/v1/admin/models/rollback" body={{ strategy_id: sid }} label="Roll back champion" variant="danger"
                      icon={<RotateCcw aria-hidden="true" className="size-4" />} confirm={`Restore the previous champion for ${sid}? The Pi will pick it up at its next poll.`} />
                  ) : null}
                </div>
                <Table caption={`${sid} model versions`}>
                  <thead><tr><Th>Model</Th><Th>Status</Th><Th>Evidence</Th><Th align="right">Brier skill</Th><Th align="right">ECE</Th><Th>Valid until</Th><Th>Gate</Th><Th /></tr></thead>
                  <tbody>
                    {rows.map((v) => {
                      const ev = (v.evaluation ?? {}) as Json;
                      const gate = (v.promotion_report ?? null) as Json | null;
                      const expired = new Date(String(v.validation_expires_at)).getTime() < now.getTime();
                      return (
                        <tr key={String(v.id)}>
                          <Td className="num max-w-[220px] truncate text-[12px]" ><span title={String(v.id)}>{String(v.id)}</span><div className="text-[11px] text-faint">{fmtBytes(Number(v.artifact_size_bytes ?? 0))} · key v{String(v.signer_key_version)}</div></Td>
                          <Td><Badge tone={v.status === "champion" ? "pos" : v.status === "rejected" ? "neg" : "neutral"}>{String(v.status)}</Badge>{v.is_demo ? <Badge tone="demo" className="ml-1">DEMO</Badge> : null}</Td>
                          <Td><Badge tone={confidenceTone(ev.confidence_status as string)}>{String(ev.confidence_status ?? "n/a").toLowerCase()}</Badge></Td>
                          <Td align="right">{ev.brier_skill === undefined ? "—" : fmtNum(ev.brier_skill, 3)}</Td>
                          <Td align="right">{ev.ece === undefined ? "—" : fmtNum(ev.ece, 3)}</Td>
                          <Td className={expired ? "text-neg" : "text-muted"}>{fmtEt(v.validation_expires_at as string)}{expired ? " (expired)" : ""}</Td>
                          <Td className="max-w-[220px] text-[12px] text-muted">{gate ? `${gate.ok ? "passed" : "failed"}: ${String(gate.reason ?? "")}` : v.status === "champion" ? "promoted" : "not evaluated"}</Td>
                          <Td>{v.status !== "champion" && !expired ? (
                            <ActionButton url={`/api/v1/admin/models/${String(v.id)}/promote`} label="Promote" variant="secondary"
                              confirm="Run the frozen promotion gate for this candidate? It is promoted only if it passes." />) : null}</Td>
                        </tr>
                      );
                    })}
                  </tbody>
                </Table>
              </div>
            );
          }) : <EmptyState title="Registry is empty" detail="The first weekly training run registers candidates here. Until a model passes, every signal stays UNVALIDATED." />}
        </Card>

        <div className="space-y-4">
          <Card title="Run training now" definition="Dispatches the weekly workflow server-side with the encrypted GitHub token (never sent to the browser). Max 2 per 6 hours; refused if it would exceed the minutes budget.">
            <ActionButton url="/api/v1/admin/training/dispatch" label="Run training now" variant="primary" icon={<Play aria-hidden="true" className="size-4" />}
              disabled={Boolean(dispatchDisabled)} disabledReason={dispatchDisabled} confirm="Start a training run on GitHub Actions now? It uses Actions minutes." />
          </Card>
          <Card title="Promotion policy" definition="Manual is the default. Auto promotes only CALIBRATED candidates that pass the same frozen gates, with an audit entry."
            interval={`v${policy.version}`}>
            <KeyValue items={[["Mode", policy.value.promotion_mode === "auto" ? "AUTO (frozen gates)" : "Manual review"], ["Auto requires", policy.value.auto_requires_status],
              ["Pi poll interval", `${policy.value.poll_minutes} min`]]} />
            <div className="mt-3">
              {policy.value.promotion_mode === "manual" ? (
                <ActionButton url="/api/v1/admin/settings" method="PUT" variant="secondary" label="Switch to AUTO promotion"
                  body={{ key: "models.policy", value: { ...policy.value, promotion_mode: "auto" }, version: policy.version }}
                  confirm="Enable unattended promotion? Do this only after independent backtests, a risk review and stable shadow evaluation." successMessage="AUTO promotion enabled" />
              ) : (
                <ActionButton url="/api/v1/admin/settings" method="PUT" variant="secondary" label="Return to manual promotion"
                  body={{ key: "models.policy", value: { ...policy.value, promotion_mode: "manual" }, version: policy.version }} successMessage="Manual promotion restored" />
              )}
            </div>
          </Card>
          <Card title="Pi model status" definition="What each paired Pi reports it is actually running (acknowledged after signature, checksum and compatibility checks).">
            {(panel.activations as Json[]).length ? (
              <ul className="space-y-2">
                {(panel.activations as Json[]).map((a) => (
                  <li key={String(a.agent_id)} className="rounded-lg border border-line px-3 py-2 text-[13px]">
                    <div className="flex items-center justify-between gap-2"><span>{String(a.device_name ?? a.agent_id)}</span>
                      <Badge tone={a.status === "activated" ? "pos" : a.status === "rejected" ? "neg" : "warn"}>{String(a.status)}</Badge></div>
                    <div className="num truncate text-[11px] text-faint">{String(a.model_id)} · {fmtAge(ageSeconds(now, a.reported_at as string))} ago</div>
                    {a.detail ? <div className="text-[11px] text-muted">{String(a.detail)}</div> : null}
                  </li>
                ))}
              </ul>
            ) : <p className="text-[13px] text-muted">UNKNOWN: no Pi has reported a model activation yet{ctx.isDemo ? " (the DEMO replay has no live Pi)" : ""}.</p>}
          </Card>
        </div>
      </div>

      <Card title="Run history" definition="Training, drift and release runs reported back by the workflows (or local replay). Failed and skipped runs are kept.">
        {(panel.runs as Json[]).length ? (
          <Table caption="Training runs">
            <thead><tr><Th>Started</Th><Th>Workflow</Th><Th>Trigger</Th><Th>Status</Th><Th>Outcome</Th><Th align="right">Minutes</Th><Th>Run</Th></tr></thead>
            <tbody>
              {(panel.runs as Json[]).map((r) => (
                <tr key={String(r.id)}>
                  <Td className="text-muted">{fmtEt(r.started_at as string)}</Td><Td>{humanize(String(r.workflow))}</Td><Td className="text-muted">{humanize(String(r.trigger))}</Td>
                  <Td><Badge tone={r.status === "succeeded" ? "pos" : r.status === "failed" ? "neg" : "neutral"}>{humanize(String(r.status))}</Badge></Td>
                  <Td className="text-muted">{r.outcome ? humanize(String(r.outcome)) : "—"}</Td>
                  <Td align="right">{r.minutes_used === null || r.minutes_used === undefined ? "UNKNOWN" : fmtNum(r.minutes_used, 1)}</Td>
                  <Td className="num text-[12px] text-faint">{String(r.github_run_id ?? "—")}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : <EmptyState title="No runs reported yet" />}
      </Card>
      {ctx.isDemo ? (
        <Notice tone="demo" title="DEMO registry">
          The only champion here is the red-team planted-alpha control model, trained on a synthetic dataset with a known edge to prove the pipeline can detect skill. It never scores real symbols.
        </Notice>
      ) : null}
    </>
  );
}
