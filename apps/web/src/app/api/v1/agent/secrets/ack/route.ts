import { agentRoute } from "@/server/http/route";
import { ackSecret } from "@/server/services/pairing";

export const runtime = "nodejs";
export const POST = agentRoute({}, async (ctx, agent, _req, body) => {
  await ackSecret(ctx.db, agent.agentId, String(body.provider), Number(body.secret_version));
  return { ok: true };
});
