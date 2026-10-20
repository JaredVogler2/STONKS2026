import { agentRoute } from "@/server/http/route";
import { ingestObservations } from "@/server/services/ingest";

export const runtime = "nodejs";
export const POST = agentRoute({}, async (ctx, _agent, _req, body) => ingestObservations(ctx.db, body as never), 2 * 1024 * 1024);
