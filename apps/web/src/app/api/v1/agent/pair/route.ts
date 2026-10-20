import { agentRoute } from "@/server/http/route";
import { completePairing, type PairRequest } from "@/server/services/pairing";

export const runtime = "nodejs";
// HMAC (bootstrap secret) + one-time owner code + Ed25519 proof of key possession; the owner then approves the fingerprint.
export const POST = agentRoute({ allowPending: true, allowUnregistered: true }, async (ctx, agent, _req, body) =>
  completePairing(ctx.db, agent.agentId, body as unknown as PairRequest));
