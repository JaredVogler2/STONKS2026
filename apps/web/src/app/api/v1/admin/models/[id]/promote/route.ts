import { requireOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { errorResponse, json } from "@/server/http/responses";
import { promote } from "@/server/services/models";

export const runtime = "nodejs";
// Owner promotion still runs the frozen policy; a failing candidate is NOT promoted by clicking.
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const ctx = await appContext();
    const owner = await requireOwner(ctx.db, req);
    return json(await promote(ctx.db, (await params).id, { type: "owner", id: owner.userId }, false, await ctx.now()));
  } catch (e) {
    return errorResponse(e);
  }
}
