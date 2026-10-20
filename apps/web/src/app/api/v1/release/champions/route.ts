import { agentRoute } from "@/server/http/route";
import { artifactForAgent, championsForAgent, championSummaries } from "@/server/services/models";

export const runtime = "nodejs";
// Evaluation summaries (no artifacts) so the CI challenger is compared against the current champion with a frozen policy.
// `?include=artifacts` adds the signed champion manifests + JSON artifacts for the CI drift job (apex-model-drift.yml), which
// re-verifies checksum and signature before scoring the latest window. Release identity only; never public.
export const GET = agentRoute({ release: true }, async (ctx, _agent, req) => {
  const champions = await championSummaries(ctx.db, ctx.isDemo);
  if (new URL(req.url).searchParams.get("include") !== "artifacts") return { champions };
  const manifests = await championsForAgent(ctx.db, ctx.isDemo);
  const artifacts: Record<string, string> = {};
  for (const m of manifests) artifacts[String(m.model_id)] = await artifactForAgent(ctx.db, String(m.model_id));
  return { champions, manifests, artifacts };
});
