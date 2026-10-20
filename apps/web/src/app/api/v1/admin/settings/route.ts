import { ownerRoute } from "@/server/http/route";
import { getSettings, listCompliance, listIntegrations, putSetting } from "@/server/services/settings";

export const runtime = "nodejs";
// Sanitized: integration values are never returned, only status/hints.
export const GET = ownerRoute(async (ctx) => ({
  settings: await getSettings(ctx.db), integrations: await listIntegrations(ctx.db), compliance: await listCompliance(ctx.db), release_mode: ctx.mode,
}));
export const PUT = ownerRoute(async (ctx, owner, _req, body) => ({
  version: await putSetting(ctx.db, owner.userId, String(body.key), body.value, body.version === undefined ? undefined : Number(body.version)),
}));
