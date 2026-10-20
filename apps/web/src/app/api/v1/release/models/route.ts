import { agentRoute } from "@/server/http/route";
import { promote, registerCandidate } from "@/server/services/models";
import { getSettings } from "@/server/services/settings";

export const runtime = "nodejs";
// CI release endpoint: separate release HMAC secret (GitHub Environment secret) + Ed25519-signed manifest.
export const POST = agentRoute({ release: true }, async (ctx, agent, _req, body) => {
  const res = await registerCandidate(ctx.db, { manifest: body.manifest as Record<string, unknown>, artifact_b64: String(body.artifact_b64 ?? ""),
    training_run_id: (body.training_run_id as string | undefined) ?? null }, undefined, ctx.mode);
  const policy = (await getSettings(ctx.db))["models.policy"].value;
  let promotion = { promoted: false, reason: "manual promotion mode: awaiting owner review" };
  if (policy.promotion_mode === "auto" && res.created) {
    const status = ((body.manifest as Record<string, unknown>).evaluation as Record<string, unknown>)?.confidence_status;
    promotion = status === policy.auto_requires_status
      ? await promote(ctx.db, res.model_id, { type: "system", id: agent.agentId })
      : { promoted: false, reason: `auto-promotion requires ${policy.auto_requires_status}` };
  }
  return { ...res, promotion };
}, 3 * 1024 * 1024);
