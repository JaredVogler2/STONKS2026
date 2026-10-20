import { agentRoute } from "@/server/http/route";
import { recordTrainingRun } from "@/server/services/models";

export const runtime = "nodejs";
export const POST = agentRoute({ release: true }, async (ctx, _agent, _req, body) => ({ id: await recordTrainingRun(ctx.db, body) }));
