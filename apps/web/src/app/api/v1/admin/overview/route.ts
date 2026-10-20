import { ownerRoute } from "@/server/http/route";
import { overview } from "@/server/services/queries";

export const runtime = "nodejs";
export const GET = ownerRoute(async (ctx) => overview(ctx.db));
