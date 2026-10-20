import { ownerRoute } from "@/server/http/route";
import { listSignals } from "@/server/services/queries";

export const runtime = "nodejs";
export const GET = ownerRoute(async (ctx, _o, req) => {
  const p = new URL(req.url).searchParams;
  const n = (k: string) => (p.get(k) ? Number(p.get(k)) : undefined);
  return listSignals(ctx.db, { state: p.get("state") ?? undefined, tier: p.get("tier") ?? undefined, strategy: p.get("strategy") ?? undefined,
    confidence: p.get("confidence") ?? undefined, sector: p.get("sector") ?? undefined, minQuality: n("min_quality"), q: p.get("q") ?? undefined,
    limit: n("limit"), offset: n("offset") });
});
