import { requireOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { errorResponse, json, parseJsonBounded, readBodyCapped } from "@/server/http/responses";
import { reviewSignal } from "@/server/services/explorer";

export const runtime = "nodejs";
// Owner review annotation / alert suppression. Cannot change confidence or lifecycle state.
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const ctx = await appContext();
    const owner = await requireOwner(ctx.db, req);
    const body = (parseJsonBounded(await readBodyCapped(req, 4096)) as { note?: unknown; suppress?: unknown }) ?? {};
    return json(await reviewSignal(ctx.db, owner.userId, (await params).id, { note: String(body.note ?? ""), suppress: body.suppress === true }, await ctx.now()));
  } catch (e) {
    return errorResponse(e);
  }
}
