import { HttpError } from "@/server/http/responses";
import { agentRoute } from "@/server/http/route";
import { promote, rollback } from "@/server/services/models";

export const runtime = "nodejs";
// CI promote / rollback (apex-model-release.yml, protected `model-release` environment, release identity only).
// The frozen promotion policy is re-applied server-side and `force` is never reachable from here, so a candidate that
// fails the policy cannot be promoted from CI. Rollback only applies to the current champion of this deployment's mode.
const MODEL_ID = /^mdl_[a-z0-9]{1,60}$/;

export const POST = agentRoute({ release: true }, async (ctx, agent, _req, body) => {
  const modelId = typeof body.model_id === "string" ? body.model_id : "";
  if (!MODEL_ID.test(modelId)) throw new HttpError(422, "INVALID_MODEL_ID", "model_id must look like mdl_<id>");
  const actor = { type: "system" as const, id: agent.agentId };
  const now = await ctx.now();
  if (body.action === "promote") {
    return { action: "promote", requested_model_id: modelId, ...(await promote(ctx.db, modelId, actor, false, now)) };
  }
  if (body.action === "rollback") {
    const rows = await ctx.db.query<{ strategy_id: string; status: string; is_demo: boolean }>(
      "SELECT strategy_id, status, is_demo FROM apex.model_versions WHERE id = $1", [modelId]);
    const m = rows[0];
    if (!m) throw new HttpError(404, "NOT_FOUND", "model not found");
    if (m.status !== "champion" || m.is_demo !== ctx.isDemo) throw new HttpError(409, "NOT_CHAMPION", "only the current champion can be rolled back");
    return { action: "rollback", requested_model_id: modelId, ...(await rollback(ctx.db, m.strategy_id, ctx.isDemo, actor, now)) };
  }
  throw new HttpError(422, "INVALID_ACTION", "action must be promote or rollback");
});
