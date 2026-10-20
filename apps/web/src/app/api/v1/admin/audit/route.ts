import { ownerRoute } from "@/server/http/route";
import { operations } from "@/server/services/queries";

export const runtime = "nodejs";
export const GET = ownerRoute(async (ctx) => operations(ctx.db));
