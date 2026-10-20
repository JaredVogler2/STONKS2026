import { ownerRoute } from "@/server/http/route";
import { research } from "@/server/services/queries";

export const runtime = "nodejs";
export const GET = ownerRoute(async (ctx) => research(ctx.db));
