import { agentRoute } from "@/server/http/route";
import { ingestHeartbeat } from "@/server/services/ingest";
import { secretVersions } from "@/server/services/settings";

export const runtime = "nodejs";
export const POST = agentRoute({ allowPending: true }, async (ctx, agent, _req, body) => ({
  ok: true, status: agent.status, server_time: new Date().toISOString(), ...(await ingestHeartbeat(ctx.db, agent.agentId, body)),
  secret_versions: agent.status === "active" ? await secretVersions(ctx.db) : {},
}));
