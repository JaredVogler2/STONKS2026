import { agentRoute } from "@/server/http/route";
import { ackOutbox, type AckInput } from "@/server/services/ingest";

export const runtime = "nodejs";
export const POST = agentRoute({}, async (ctx, agent, _req, body) => {
  const acks = ((body.acks as AckInput[] | undefined) ?? []).slice(0, 100);
  const results = [];
  for (const a of acks) results.push({ outbox_id: a.outbox_id, state: await ackOutbox(ctx.db, agent.agentId, a) });
  return { results };
});
