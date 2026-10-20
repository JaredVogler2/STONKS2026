import { agentRoute } from "@/server/http/route";
import { recordActivation } from "@/server/services/models";

export const runtime = "nodejs";
export const POST = agentRoute({}, async (ctx, agent, _req, body) => {
  await recordActivation(ctx.db, agent.agentId, body as never);
  return { ok: true };
});
