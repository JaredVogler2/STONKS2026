import { ownerRoute } from "@/server/http/route";
import { assertContract } from "@/server/http/validate";
import { listPositions, recordPositionEvent, type PositionEventInput } from "@/server/services/positions";

export const runtime = "nodejs";
// The OWNER's own self-reported positions only (member-scoped RLS session). Other members' fills are never listed.
export const GET = ownerRoute(async (ctx, owner, req) => ({
  positions: await listPositions(ctx.db, owner.userId, new URL(req.url).searchParams.get("include_closed") === "1"),
}));
export const POST = ownerRoute(async (ctx, owner, _req, body) => {
  const ev = assertContract<PositionEventInput>("PositionEvent", { ...body, source: "dashboard" });
  return recordPositionEvent(ctx.db, owner.userId, ev, await ctx.now(), ctx.isDemo);
});
