import { agentRoute } from "@/server/http/route";
import { trainingStatus } from "@/server/services/models";
import { complianceMap, getSettings, secretVersions } from "@/server/services/settings";

export const runtime = "nodejs";
// Sanitized configuration only: NO credentials (those travel exclusively as device-encrypted envelopes).
export const GET = agentRoute({ allowPending: true }, async (ctx) => {
  const s = await getSettings(ctx.db);
  const versions = Object.values(s).reduce((acc, v) => acc + v.version, 0);
  return {
    release_mode: ctx.mode, config_version: versions, compliance: await complianceMap(ctx.db),
    universe: s["market.universe"].value, slots: s["market.slots"].value, cadence: s["market.cadence"].value, risk: s["risk.policy"].value,
    discord: s["discord.channels"].value, models: s["models.policy"].value, sec_user_agent: s["sec.contact"].value.user_agent,
    strategies: s["strategies.enabled"].value, budgets: s["budgets"].value, alerts_stopped: s["alerts.kill_switch"].value.stopped,
    sla: s["sla.freshness"].value, secret_versions: await secretVersions(ctx.db),
    // for the Pi's missed-run detector and model-expiry notices
    training: await trainingStatus(ctx.db, ctx.isDemo),
  };
});
