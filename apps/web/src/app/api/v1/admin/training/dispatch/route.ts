import { ownerRoute } from "@/server/http/route";
import { HttpError } from "@/server/http/responses";
import { audit } from "@/server/services/audit";
import { getSettings, readIntegrationSecrets } from "@/server/services/settings";

export const runtime = "nodejs";
// "Run Training Now": dispatches the GitHub workflow server-side with the encrypted fine-grained token.
// The token never reaches the browser. Disabled with exact instructions when not configured.
export const POST = ownerRoute(async (ctx, owner) => {
  if (ctx.isDemo) throw new HttpError(409, "DEMO_MODE", "training dispatch is disabled in DEMO; run `apex train --source synthetic` locally");
  const gh = await readIntegrationSecrets(ctx.db, "github");
  if (!gh?.fields.token || !gh.fields.repo) {
    throw new HttpError(412, "NOT_CONFIGURED", "configure Settings -> Integrations -> GitHub (fine-grained token, Actions read/write) first");
  }
  const recent = await ctx.db.query<{ n: number }>(
    "SELECT count(*)::int AS n FROM apex.audit_events WHERE action='training.dispatched' AND at > now() - interval '6 hours'");
  if ((recent[0]?.n ?? 0) >= 2) throw new HttpError(429, "RATE_LIMITED", "at most 2 manual training runs per 6 hours (minutes budget)");
  const budget = (await getSettings(ctx.db)).budgets.value;
  const used = await ctx.db.query<{ m: string | null }>(
    "SELECT sum(minutes_used)::text AS m FROM apex.training_runs WHERE started_at > date_trunc('month', now())");
  if (Number(used[0]?.m ?? 0) + budget.train_timeout_minutes > budget.actions_monthly_minutes) {
    throw new HttpError(429, "BUDGET_EXHAUSTED", "monthly Actions minutes budget would be exceeded");
  }
  const res = await fetch(`https://api.github.com/repos/${gh.fields.repo}/actions/workflows/apex-model-train-weekly.yml/dispatches`, {
    method: "POST", headers: { Authorization: `Bearer ${gh.fields.token}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" },
    // inputs must match apex-model-train-weekly.yml; requested_by=admin records the run as an admin_dispatch
    body: JSON.stringify({ ref: "main", inputs: { reason: "owner dispatch from dashboard", source: "auto", requested_by: "admin" } }),
    signal: AbortSignal.timeout(8000),
  });
  if (res.status !== 204) throw new HttpError(502, "DISPATCH_FAILED", `GitHub returned HTTP ${res.status}`);
  await audit(ctx.db, { actorType: "owner", actorId: owner.userId, entity: "training_runs", action: "training.dispatched" });
  return { ok: true, detail: "workflow_dispatch accepted; the run reports back through the release API" };
});
