import "server-only";
import type { Db, Sql } from "../db";
import { HttpError } from "../http/responses";
import { audit } from "./audit";

export interface Member {
  id: string;
  discordUserId: string | null;
  role: "owner" | "member";
  dmOptIn: boolean;
  disabled: boolean;
}

const SNOWFLAKE = /^[1-9][0-9]{14,20}$/;

/**
 * Map an authenticated Discord user id (asserted by the paired Pi's gateway connection, never by a browser)
 * to the internal member UUID. Creates the member on first use when `create` is true.
 */
export async function resolveMember(db: Db | Sql, discordUserId: string, opts: { create: boolean; guildId?: string | null }): Promise<Member> {
  if (!SNOWFLAKE.test(discordUserId)) throw new HttpError(422, "INVALID_DISCORD_ID", "invalid Discord user id");
  let rows = await db.query<{ id: string; discord_user_id: string; role: string; dm_opt_in: boolean; disabled_at: Date | null }>(
    "SELECT id, discord_user_id, role, dm_opt_in, disabled_at FROM apex.app_users WHERE discord_user_id = $1",
    [discordUserId],
  );
  if (!rows.length) {
    if (!opts.create) throw new HttpError(404, "NOT_FOUND", "member not found");
    rows = await db.query(
      `INSERT INTO apex.app_users(discord_user_id, role) VALUES ($1, 'member')
       ON CONFLICT (discord_user_id) DO UPDATE SET discord_user_id = EXCLUDED.discord_user_id
       RETURNING id, discord_user_id, role, dm_opt_in, disabled_at`,
      [discordUserId],
    );
    await audit(db, { actorType: "agent", entity: "app_users", entityId: rows[0]!.id, action: "member.enrolled" });
  }
  const r = rows[0]!;
  if (opts.guildId) {
    const g = await db.query<{ allowlisted: boolean }>("SELECT allowlisted FROM apex.guilds WHERE discord_guild_id = $1", [opts.guildId]);
    if (!g[0]?.allowlisted) throw new HttpError(403, "GUILD_NOT_ALLOWED", "this Discord server is not allowlisted");
    const m = await db.query<{ revoked_at: Date | null }>(
      `INSERT INTO apex.guild_memberships(user_id, guild_id) VALUES ($1, $2)
       ON CONFLICT (user_id, guild_id) DO UPDATE SET user_id = EXCLUDED.user_id RETURNING revoked_at`,
      [r.id, opts.guildId],
    );
    if (m[0]?.revoked_at) throw new HttpError(403, "MEMBERSHIP_REVOKED", "membership revoked");
  }
  if (r.disabled_at) throw new HttpError(403, "MEMBERSHIP_REVOKED", "membership revoked");
  return { id: r.id, discordUserId: r.discord_user_id, role: r.role as Member["role"], dmOptIn: r.dm_opt_in, disabled: false };
}

export async function revokeMember(db: Db, discordUserId: string, guildId: string | null): Promise<void> {
  await db.tx(async (sql) => {
    const u = await sql.query<{ id: string }>("SELECT id FROM apex.app_users WHERE discord_user_id = $1", [discordUserId]);
    if (!u[0]) return;
    if (guildId) {
      await sql.query("UPDATE apex.guild_memberships SET revoked_at = now() WHERE user_id = $1 AND guild_id = $2", [u[0].id, guildId]);
    } else {
      await sql.query("UPDATE apex.app_users SET disabled_at = now() WHERE id = $1 AND role = 'member'", [u[0].id]);
    }
    await audit(sql, { actorType: "agent", entity: "app_users", entityId: u[0].id, action: "member.revoked" });
  });
}

export async function memberCounts(db: Db): Promise<{ members: number; active: number; dmOptIn: number }> {
  const r = await db.query<{ members: number; active: number; dm: number }>(
    `SELECT count(*)::int AS members, count(*) FILTER (WHERE disabled_at IS NULL)::int AS active,
            count(*) FILTER (WHERE dm_opt_in)::int AS dm FROM apex.app_users WHERE role = 'member'`,
  );
  return { members: r[0]?.members ?? 0, active: r[0]?.active ?? 0, dmOptIn: r[0]?.dm ?? 0 };
}
