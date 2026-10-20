import { ownerRoute } from "@/server/http/route";
import { listCompliance, setCompliance } from "@/server/services/settings";

export const runtime = "nodejs";
export const POST = ownerRoute(async (ctx, owner, _req, body) => {
  await setCompliance(ctx.db, owner.userId, String(body.flag), Boolean(body.enabled), (body.acknowledgement as string) ?? null,
    (body.reference as string) ?? null);
  return { flags: await listCompliance(ctx.db) };
});
