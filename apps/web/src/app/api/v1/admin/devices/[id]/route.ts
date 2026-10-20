import { requireOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { errorResponse, HttpError, json, parseJsonBounded, readBodyCapped } from "@/server/http/responses";
import { setDeviceStatus } from "@/server/services/pairing";

export const runtime = "nodejs";
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const ctx = await appContext();
    const owner = await requireOwner(ctx.db, req);
    const body = parseJsonBounded(await readBodyCapped(req, 4096)) as { action?: string };
    if (body.action !== "approve" && body.action !== "revoke") throw new HttpError(422, "INVALID_ACTION", "action must be approve or revoke");
    await setDeviceStatus(ctx.db, owner.userId, (await params).id, body.action);
    return json({ ok: true });
  } catch (e) {
    return errorResponse(e);
  }
}
