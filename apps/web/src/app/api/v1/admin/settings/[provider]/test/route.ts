import { requireOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { errorResponse, json } from "@/server/http/responses";
import { testIntegration } from "@/server/services/settings";

export const runtime = "nodejs";
export async function POST(req: Request, { params }: { params: Promise<{ provider: string }> }) {
  try {
    const ctx = await appContext();
    const owner = await requireOwner(ctx.db, req);
    return json(await testIntegration(ctx.db, owner.userId, (await params).provider));
  } catch (e) {
    return errorResponse(e);
  }
}
