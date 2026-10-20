import { agentRoute } from "@/server/http/route";
import { secretEnvelope } from "@/server/services/pairing";

export const runtime = "nodejs";
// Returns an X25519-encrypted envelope readable only by this device's private key; plaintext never leaves this request.
export const GET = agentRoute({}, async (ctx, agent, req) =>
  ({ envelope: await secretEnvelope(ctx.db, agent.agentId, new URL(req.url).searchParams.get("provider") ?? "") }));
