import { agentRoute } from "@/server/http/route";
import { HttpError } from "@/server/http/responses";
import { ackOutbox, type AckInput } from "@/server/services/ingest";

export const runtime = "nodejs";
export const POST = agentRoute({}, async (ctx, agent, _req, body) => {
  const acks = ((body.acks as AckInput[] | undefined) ?? []).slice(0, 100);
  const results = [];
  // one bad item (unknown id, lease held elsewhere) must not drop the acknowledgements after it
  for (const a of acks) {
    try {
      results.push({ outbox_id: a.outbox_id, state: await ackOutbox(ctx.db, agent.agentId, a) });
    } catch (e) {
      if (!(e instanceof HttpError) || e.status >= 500) throw e;
      results.push({ outbox_id: a.outbox_id, error: { code: e.code, status: e.status } });
    }
  }
  return { results };
});
