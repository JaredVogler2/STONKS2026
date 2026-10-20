import { agentRoute } from "@/server/http/route";
import { ingestSignal, type SignalPublish } from "@/server/services/ingest";

export const runtime = "nodejs";
export const POST = agentRoute({}, async (ctx, agent, _req, body) => ingestSignal(ctx.db, agent.agentId, body as unknown as SignalPublish, ctx.mode));
