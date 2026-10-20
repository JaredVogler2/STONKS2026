import { agentRoute } from "@/server/http/route";

export const runtime = "nodejs";
// Lets a restarted Pi rebuild its view of PUBLISHED/ACTIVE signals (and recent closures for member assessments).
export const GET = agentRoute({}, async (ctx) => {
  const rows = await ctx.db.query<{ immutable_snapshot: Record<string, unknown>; state: string }>(
    `SELECT s.immutable_snapshot, h.state FROM apex.signal_heads h JOIN apex.signals s ON s.id = h.signal_id AND s.revision = h.current_revision
     WHERE h.state IN ('PUBLISHED','ACTIVE') OR h.updated_at > now() - interval '30 days' ORDER BY s.asof_at DESC LIMIT 300`);
  return { signals: rows.map((r) => ({ signal: { ...r.immutable_snapshot, state: r.state }, state: r.state })) };
});
