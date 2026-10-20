import { agentRoute } from "@/server/http/route";
import { HttpError } from "@/server/http/responses";
import { enqueueIntent } from "@/server/services/ingest";

export const runtime = "nodejs";
// Pi system notices (missed training, registry unreachable, stale feed, quota low) recorded in the dashboard outbox and
// delivered to #system-status. Only SYSTEM_ALERT kinds to that channel are accepted here: never investment or private content.
export const POST = agentRoute({}, async (ctx, _agent, _req, body) => {
  const intents = ((body.intents as Record<string, unknown>[] | undefined) ?? []).slice(0, 20);
  let queued = 0;
  for (const i of intents) {
    const route = (i.route ?? {}) as Record<string, unknown>;
    if (i.kind !== "SYSTEM_ALERT" || route.type !== "CHANNEL" || route.channel_key !== "system_status") {
      throw new HttpError(422, "NOT_A_SYSTEM_NOTICE", "only SYSTEM_ALERT notices to #system-status are accepted");
    }
    if (await enqueueIntent(ctx.db, i)) queued++;
  }
  return { queued };
});
