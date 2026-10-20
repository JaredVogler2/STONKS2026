import { agentRoute } from "@/server/http/route";
import { ingestCatalysts } from "@/server/services/ingest";

export const runtime = "nodejs";
export const POST = agentRoute({}, async (ctx, _agent, _req, body) => ingestCatalysts(ctx.db, (body.items as Record<string, unknown>[]) ?? []),
  1024 * 1024);
