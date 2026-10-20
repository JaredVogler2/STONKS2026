import { appContext } from "@/server/context";
import { errorResponse, json, readBodyCapped } from "@/server/http/responses";
import { authenticateAgent } from "@/server/services/agents";
import { artifactForAgent } from "@/server/services/models";

export const runtime = "nodejs";
export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const ctx = await appContext();
    await authenticateAgent(ctx.db, req, await readBodyCapped(req, 0));
    const { id } = await params;
    return json({ model_id: id, artifact_b64: await artifactForAgent(ctx.db, id) });
  } catch (e) {
    return errorResponse(e);
  }
}
