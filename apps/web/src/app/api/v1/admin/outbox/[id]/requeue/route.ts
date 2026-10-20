import { requireOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { errorResponse, HttpError, json } from "@/server/http/responses";
import { audit } from "@/server/services/audit";

export const runtime = "nodejs";
// Requeue a dead-lettered SHARED-CHANNEL notice. Private DMs are never requeued from here (they are the member's).
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const ctx = await appContext();
    const owner = await requireOwner(ctx.db, req);
    const { id } = await params;
    if (!/^[0-9a-f-]{36}$/.test(id)) throw new HttpError(404, "NOT_FOUND", "notice not found");
    const rows = await ctx.db.query<{ id: string }>(
      `UPDATE apex.notification_outbox SET state = 'pending', attempts = 0, next_attempt_at = now(), last_error = NULL
       WHERE id = $1 AND state = 'dead_letter' AND route_json->>'type' = 'CHANNEL' RETURNING id`, [id]);
    if (!rows.length) throw new HttpError(409, "NOT_REQUEUEABLE", "only dead-lettered channel notices can be requeued");
    await audit(ctx.db, { actorType: "owner", actorId: owner.userId, entity: "notification_outbox", entityId: id, action: "outbox.requeued" });
    return json({ ok: true });
  } catch (e) {
    return errorResponse(e);
  }
}
