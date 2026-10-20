import { agentRoute } from "@/server/http/route";
import { ingestScanRun } from "@/server/services/ingest";

export const runtime = "nodejs";
export const POST = agentRoute({}, async (ctx, agent, _req, body) => ingestScanRun(ctx.db, agent.agentId, body), 1024 * 1024);
