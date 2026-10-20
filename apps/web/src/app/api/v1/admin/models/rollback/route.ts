import { ownerRoute } from "@/server/http/route";
import { rollback } from "@/server/services/models";

export const runtime = "nodejs";
export const POST = ownerRoute(async (ctx, owner, _req, body) =>
  rollback(ctx.db, String(body.strategy_id), ctx.isDemo, { type: "owner", id: owner.userId }, await ctx.now()));
