import { ownerRoute } from "@/server/http/route";
import { exploreSignals, parseFilter } from "@/server/services/explorer";

export const runtime = "nodejs";
export const GET = ownerRoute(async (ctx, _o, req) => exploreSignals(ctx.db, parseFilter(new URL(req.url).searchParams), await ctx.now()));
