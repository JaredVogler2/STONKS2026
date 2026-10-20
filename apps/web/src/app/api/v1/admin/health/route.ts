import { ownerRoute } from "@/server/http/route";
import { computeHealth } from "@/server/services/health";

export const runtime = "nodejs";
export const GET = ownerRoute(async (ctx) => computeHealth(ctx.db, await ctx.now()));
