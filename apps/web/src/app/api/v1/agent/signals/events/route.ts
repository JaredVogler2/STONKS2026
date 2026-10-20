import { agentRoute } from "@/server/http/route";
import { ingestSignalEvents } from "@/server/services/ingest";

export const runtime = "nodejs";
export const POST = agentRoute({}, async (ctx, _agent, _req, body) => ingestSignalEvents(ctx.db, body as never));
