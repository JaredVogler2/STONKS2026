import { ownerRoute } from "@/server/http/route";
import { startPairing } from "@/server/services/pairing";

export const runtime = "nodejs";
export const POST = ownerRoute(async (ctx, owner) => startPairing(ctx.db, owner.userId));
