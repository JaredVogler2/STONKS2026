import { agentRoute } from "@/server/http/route";
import { HttpError } from "@/server/http/responses";
import { resolveMember, revokeMember } from "@/server/services/members";
import { deleteMemberData } from "@/server/services/positions";

export const runtime = "nodejs";
export const POST = agentRoute({}, async (ctx, _agent, _req, body) => {
  const discord = String(body.discord_user_id ?? "");
  const guild = (body.guild_id as string | undefined) ?? null;
  switch (body.action) {
    case "revoke":
      await revokeMember(ctx.db, discord, guild);
      return { ok: true };
    case "prefs": {
      const m = await resolveMember(ctx.db, discord, { create: true, guildId: guild });
      await ctx.db.asMember(m.id, (sql) => sql.query("UPDATE apex.app_users SET dm_opt_in = $2, high_priority_mentions = $3 WHERE id = $1", [
        m.id, Boolean(body.dm_opt_in), Boolean(body.high_priority_mentions)]));
      return { ok: true };
    }
    case "delete_my_data": {
      const m = await resolveMember(ctx.db, discord, { create: false });
      const n = await deleteMemberData(ctx.db, m.id);
      await ctx.db.query("UPDATE apex.notification_outbox SET state='suppressed', payload_json='{}'::jsonb WHERE user_id=$1 AND state <> 'sent'", [m.id]);
      return { ok: true, positions_deleted: n };
    }
    default:
      throw new HttpError(422, "UNKNOWN_ACTION", "unknown member action");
  }
});
