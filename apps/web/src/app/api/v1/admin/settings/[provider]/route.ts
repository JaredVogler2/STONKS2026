import { requireOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { errorResponse, json, parseJsonBounded, readBodyCapped } from "@/server/http/responses";
import { disableIntegration, saveIntegration } from "@/server/services/settings";

export const runtime = "nodejs";
type P = { params: Promise<{ provider: string }> };

// PUT stores encrypted values; the response NEVER echoes them.
export async function PUT(req: Request, { params }: P) {
  try {
    const ctx = await appContext();
    const owner = await requireOwner(ctx.db, req);
    const body = parseJsonBounded(await readBodyCapped(req, 16 * 1024)) as Record<string, unknown>;
    await saveIntegration(ctx.db, owner.userId, (await params).provider, (body.values as Record<string, unknown>) ?? {});
    return json({ ok: true });
  } catch (e) {
    return errorResponse(e);
  }
}

export async function DELETE(req: Request, { params }: P) {
  try {
    const ctx = await appContext();
    const owner = await requireOwner(ctx.db, req);
    const remove = new URL(req.url).searchParams.get("mode") !== "disable";
    await disableIntegration(ctx.db, owner.userId, (await params).provider, remove);
    return json({ ok: true });
  } catch (e) {
    return errorResponse(e);
  }
}
