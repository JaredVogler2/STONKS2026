import { agentRoute } from "@/server/http/route";
import { HttpError } from "@/server/http/responses";
import { resolveMember } from "@/server/services/members";
import { getPosition } from "@/server/services/positions";

export const runtime = "nodejs";
/*
 * A member's own position history for the bot's /apex_correct and /apex_remove flows, RLS-scoped to the Discord user the
 * Pi received on an authenticated interaction. `client_idempotency_key` finds which position an earlier submit created.
 */
export const GET = agentRoute({}, async (ctx, _agent, req) => {
  const url = new URL(req.url);
  const discord = url.searchParams.get("discord_user_id") ?? "";
  if (!/^[1-9][0-9]{14,20}$/.test(discord)) throw new HttpError(422, "DISCORD_USER_REQUIRED", "discord_user_id required");
  const member = await resolveMember(ctx.db, discord, { create: false, guildId: url.searchParams.get("guild_id") }).catch((e) => {
    if (e instanceof HttpError && e.status === 404) return null;
    throw e;
  });
  if (!member) throw new HttpError(404, "NOT_FOUND", "no positions for this member");
  let positionId = url.searchParams.get("position_id");
  const key = url.searchParams.get("client_idempotency_key");
  if (!positionId && key) {
    if (!/^[A-Za-z0-9:_-]{8,128}$/.test(key)) throw new HttpError(422, "INVALID_KEY", "invalid idempotency key");
    const rows = await ctx.db.asMember(member.id, (sql) => sql.query<{ position_id: string; id: string }>(
      "SELECT position_id, id FROM apex.position_events WHERE client_idempotency_key = $1", [key]));
    if (!rows[0]) throw new HttpError(404, "NOT_FOUND", "no event with that key");
    positionId = rows[0].position_id;
  }
  if (!positionId) throw new HttpError(422, "POSITION_REQUIRED", "position_id or client_idempotency_key required");
  return getPosition(ctx.db, member.id, positionId); // another member's id is simply not found (RLS)
});
