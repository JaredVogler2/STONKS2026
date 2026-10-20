import { requireOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { errorResponse } from "@/server/http/responses";
import { exportSignalsCsv, parseFilter } from "@/server/services/explorer";

export const runtime = "nodejs";
// CSV of the filtered signal list. Signal fields only; member fills are never exported from here.
export async function GET(req: Request) {
  try {
    const ctx = await appContext();
    await requireOwner(ctx.db, req);
    const now = await ctx.now();
    const csv = await exportSignalsCsv(ctx.db, parseFilter(new URL(req.url).searchParams), now);
    return new Response(csv, {
      headers: {
        "content-type": "text/csv; charset=utf-8", "cache-control": "no-store",
        "content-disposition": `attachment; filename="apex-signals-${now.toISOString().slice(0, 10)}${ctx.isDemo ? "-DEMO" : ""}.csv"`,
      },
    });
  } catch (e) {
    return errorResponse(e);
  }
}
