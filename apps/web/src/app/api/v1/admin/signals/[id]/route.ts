import { requireOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { errorResponse, HttpError, json } from "@/server/http/responses";
import { barsFor, signalDetail } from "@/server/services/queries";

export const runtime = "nodejs";
export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const ctx = await appContext();
    await requireOwner(ctx.db, req);
    const { id } = await params;
    const d = await signalDetail(ctx.db, id);
    if (!d) throw new HttpError(404, "NOT_FOUND", "signal not found");
    return json({ ...d, bars: await barsFor(ctx.db, String(d.snapshot.symbol)) });
  } catch (e) {
    return errorResponse(e);
  }
}
