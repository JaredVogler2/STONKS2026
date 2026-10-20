import { agentRoute } from "@/server/http/route";
import { leaseOutbox } from "@/server/services/ingest";
import { championsForAgent } from "@/server/services/models";

export const runtime = "nodejs";
// Secret-free work: notifications to deliver, member positions to monitor (the paired Pi is the trusted assessor),
// and the champion model versions it should be running.
export const GET = agentRoute({}, async (ctx, agent, req) => {
  const limit = Number(new URL(req.url).searchParams.get("limit") ?? "20");
  const outbox = await leaseOutbox(ctx.db, agent.agentId, limit);
  const positions = await ctx.db.query<{ id: string; user_id: string; discord_user_id: string | null; symbol: string; signal_id: string | null; state: string }>(
    `SELECT p.id, p.user_id, u.discord_user_id, p.symbol, p.signal_id, p.state FROM apex.positions p JOIN apex.app_users u ON u.id = p.user_id
     WHERE p.state IN ('OPEN','PARTIALLY_CLOSED','WATCHING') AND u.disabled_at IS NULL ORDER BY p.created_at LIMIT 500`);
  const champions = await championsForAgent(ctx.db, ctx.isDemo);
  return {
    server_time: new Date().toISOString(), release_mode: ctx.mode, outbox,
    // user_id lets the Pi address the member's private assessment intent (the server re-checks it against the position)
    monitored_positions: positions.map((p) => ({ position_id: p.id, user_id: p.user_id, discord_user_id: p.discord_user_id, symbol: p.symbol,
      signal_id: p.signal_id, state: p.state })),
    champion_models: champions.map((m) => ({ model_id: m.model_id, strategy_id: m.strategy_id, validation_expires_at: m.validation_expires_at })),
  };
});
