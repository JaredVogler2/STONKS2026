import { ownerRoute } from "@/server/http/route";
import { audit } from "@/server/services/audit";

export const runtime = "nodejs";
export const POST = ownerRoute(async (ctx, owner) => {
  const rows = await ctx.db.query<{ r: Record<string, number> }>("SELECT apex.apply_retention() AS r");
  await audit(ctx.db, { actorType: "owner", actorId: owner.userId, entity: "settings", action: "retention.applied", metadata: rows[0]?.r ?? {} });
  return rows[0]?.r ?? {};
});
