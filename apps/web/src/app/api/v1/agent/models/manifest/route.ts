import { agentRoute } from "@/server/http/route";
import { championsForAgent } from "@/server/services/models";

export const runtime = "nodejs";
export const GET = agentRoute({}, async (ctx) => ({ champions: await championsForAgent(ctx.db, ctx.isDemo) }));
