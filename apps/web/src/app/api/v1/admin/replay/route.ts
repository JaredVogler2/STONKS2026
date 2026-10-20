import { ownerRoute } from "@/server/http/route";
import { HttpError } from "@/server/http/responses";
import { advanceDemo, demoStatus } from "@/server/services/demo";

export const runtime = "nodejs";
export const GET = ownerRoute(async (ctx) => (ctx.isDemo ? demoStatus(ctx.db) : { disabled: true }));
// DEMO only: advance the deterministic replay through the same ingestion services the Pi uses.
export const POST = ownerRoute(async (ctx, owner, _req, body) => {
  if (!ctx.isDemo) throw new HttpError(409, "NOT_DEMO", "replay is only available in DEMO mode (or an isolated test database)");
  return advanceDemo(ctx.db, owner.userId, Number(body.sessions ?? 1));
});
