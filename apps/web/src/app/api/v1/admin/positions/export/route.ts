import { requireOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { errorResponse } from "@/server/http/responses";
import { getPosition, listPositions } from "@/server/services/positions";

export const runtime = "nodejs";

const cell = (v: unknown) => {
  if (v === null || v === undefined) return "";
  let s = v instanceof Date ? v.toISOString() : String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

// The OWNER's own trade report (member-scoped session). Self-reported values; never other members' data.
export async function GET(req: Request) {
  try {
    const ctx = await appContext();
    const owner = await requireOwner(ctx.db, req);
    const lines = ["symbol,position_id,state,event_seq,event_type,price,quantity,fees,event_at,source,notes"];
    for (const p of await listPositions(ctx.db, owner.userId, true)) {
      const d = await getPosition(ctx.db, owner.userId, p.position_id);
      for (const e of d.events) {
        lines.push([p.symbol, p.position_id, p.state, e.seq, e.event_type, e.price, e.quantity, e.fees, e.event_at, e.source, e.notes].map(cell).join(","));
      }
    }
    const now = await ctx.now();
    return new Response(`${lines.join("\n")}\n`, { headers: { "content-type": "text/csv; charset=utf-8", "cache-control": "no-store",
      "content-disposition": `attachment; filename="apex-my-positions-${now.toISOString().slice(0, 10)}${ctx.isDemo ? "-DEMO" : ""}.csv"` } });
  } catch (e) {
    return errorResponse(e);
  }
}
