import { agentRoute } from "@/server/http/route";
import { ingestAssessments } from "@/server/services/ingest";
import { resolveMember } from "@/server/services/members";

export const runtime = "nodejs";
export const POST = agentRoute({}, async (ctx, _agent, _req, body) => {
  const items = (body.items as Record<string, unknown>[] | undefined) ?? [];
  const resolved = [];
  for (const it of items.slice(0, 200)) {
    const member = await resolveMember(ctx.db, String(it.discord_user_id), { create: false });
    resolved.push({ user_id: member.id, assessment: it.assessment as Record<string, unknown>, assessment_key: String(it.assessment_key),
      intent: it.intent as Record<string, unknown> | undefined });
  }
  return { stored: await ingestAssessments(ctx.db, resolved) };
}, 1024 * 1024);
