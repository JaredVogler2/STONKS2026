import { agentRoute } from "@/server/http/route";
import { recordTrainingRun } from "@/server/services/models";

export const runtime = "nodejs";
// With a verified GitHub OIDC token, the run id, attempt and commit come from GitHub's signed claims, not the request body.
export const POST = agentRoute({ release: true }, async (ctx, agent, _req, body) => {
  const o = agent.oidc;
  const run = !o ? body : {
    ...body,
    github_run_id: o.run_id ?? body.github_run_id,
    github_run_attempt: o.run_attempt ? Number(o.run_attempt) : body.github_run_attempt,
    code_sha: o.sha ?? body.code_sha,
    report: { ...((body.report as Record<string, unknown> | undefined) ?? {}),
      github_oidc: { workflow_ref: o.workflow_ref, event_name: o.event_name, ref: o.ref, repository: o.repository } },
  };
  return { id: await recordTrainingRun(ctx.db, run) };
});
