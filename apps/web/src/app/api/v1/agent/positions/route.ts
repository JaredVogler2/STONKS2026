import { agentRoute } from "@/server/http/route";
import { HttpError } from "@/server/http/responses";
import { resolveMember } from "@/server/services/members";
import { listPositions } from "@/server/services/positions";

export const runtime = "nodejs";
// The Pi asserts the Discord user id it received on an authenticated Gateway interaction; results are RLS-scoped to that member.
export const GET = agentRoute({}, async (ctx, _agent, req) => {
  const url = new URL(req.url);
  const discord = url.searchParams.get("discord_user_id") ?? "";
  const guild = url.searchParams.get("guild_id");
  if (!discord) throw new HttpError(422, "DISCORD_USER_REQUIRED", "discord_user_id required");
  const member = await resolveMember(ctx.db, discord, { create: false, guildId: guild }).catch((e) => {
    if (e instanceof HttpError && e.status === 404) return null;
    throw e;
  });
  if (!member) return { positions: [] };
  return { positions: await listPositions(ctx.db, member.id, url.searchParams.get("include_closed") === "1") };
});
