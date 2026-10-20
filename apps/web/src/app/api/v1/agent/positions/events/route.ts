import { agentRoute } from "@/server/http/route";
import { HttpError } from "@/server/http/responses";
import { assertContract } from "@/server/http/validate";
import { releaseDeferredForMember } from "@/server/services/ingest";
import { resolveMember } from "@/server/services/members";
import { recordPositionEvent, type PositionEventInput } from "@/server/services/positions";

export const runtime = "nodejs";
export const POST = agentRoute({}, async (ctx, _agent, _req, body) => {
  const discord = String(body.discord_user_id ?? "");
  if (!discord) throw new HttpError(422, "DISCORD_USER_REQUIRED", "discord_user_id required");
  const event = assertContract<PositionEventInput>("PositionEvent", body.event);
  if (event.source !== "discord" && event.source !== "starter_import" && !(ctx.isDemo && event.source === "replay")) {
    throw new HttpError(422, "INVALID_SOURCE", "agent position events must come from Discord");
  }
  const member = await resolveMember(ctx.db, discord, { create: true, guildId: (body.guild_id as string | undefined) ?? null });
  const res = await recordPositionEvent(ctx.db, member.id, event, await ctx.now(), ctx.isDemo);
  return { ...res, pending_private_notifications: await releaseDeferredForMember(ctx.db, member.id) };
});
