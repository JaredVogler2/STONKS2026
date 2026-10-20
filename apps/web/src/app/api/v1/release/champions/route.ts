import { agentRoute } from "@/server/http/route";
import { championSummaries } from "@/server/services/models";

export const runtime = "nodejs";
// Evaluation summaries (no artifacts) so the CI challenger is compared against the current champion with a frozen policy.
export const GET = agentRoute({ release: true }, async (ctx) => ({ champions: await championSummaries(ctx.db, ctx.isDemo) }));
