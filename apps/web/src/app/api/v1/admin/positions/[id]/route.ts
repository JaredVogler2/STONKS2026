import { requireOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { errorResponse, json } from "@/server/http/responses";
import { getPosition } from "@/server/services/positions";

export const runtime = "nodejs";
export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const ctx = await appContext();
    const owner = await requireOwner(ctx.db, req);
    return json(await getPosition(ctx.db, owner.userId, (await params).id));
  } catch (e) {
    return errorResponse(e);
  }
}
