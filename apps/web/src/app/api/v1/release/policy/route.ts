import { agentRoute } from "@/server/http/route";
import { getSettings } from "@/server/services/settings";

export const runtime = "nodejs";
// Read-only policy for the CI workflows (release identity): deployment mode, promotion policy, owner-configured Actions
// budget and this month's measured APEX minutes, plus the latest run per workflow (missed-run detection). The CI budget gate
// is an application-level forecast only; the hard spending stop is the GitHub budget configured by the owner.
export const GET = agentRoute({ release: true }, async (ctx) => {
  const settings = await getSettings(ctx.db);
  const wall = new Date(); // budgets follow the real calendar month, not the DEMO replay clock
  const monthStart = new Date(Date.UTC(wall.getUTCFullYear(), wall.getUTCMonth(), 1));
  const usage = await ctx.db.query<{ minutes: number | null; runs: number }>(
    `SELECT coalesce(sum(minutes_used), 0)::float8 AS minutes, count(*)::int AS runs
       FROM apex.training_runs WHERE started_at >= $1 AND workflow IN ('train_weekly', 'drift', 'release')`,
    [monthStart],
  );
  const last = await ctx.db.query<{ workflow: string; status: string; outcome: string | null; started_at: Date; finished_at: Date | null }>(
    `SELECT DISTINCT ON (workflow) workflow, status, outcome, started_at, finished_at
       FROM apex.training_runs ORDER BY workflow, started_at DESC`,
  );
  return {
    mode: ctx.mode,
    is_demo: ctx.isDemo,
    policy: settings["models.policy"].value,
    budgets: settings["budgets"].value,
    usage_month: { since: monthStart.toISOString(), minutes_used: Number(usage[0]?.minutes ?? 0), runs: usage[0]?.runs ?? 0 },
    last_runs: Object.fromEntries(last.map((r) => [r.workflow, { status: r.status, outcome: r.outcome,
      started_at: new Date(r.started_at).toISOString(), finished_at: r.finished_at ? new Date(r.finished_at).toISOString() : null }])),
  };
});
