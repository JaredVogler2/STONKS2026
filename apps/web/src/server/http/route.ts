import "server-only";
import type { NextResponse } from "next/server";
import { requireOwner, type Owner } from "../auth/owner";
import { appContext, type AppContext } from "../context";
import { authenticateAgent, type AgentAuthOptions, type AgentIdentity } from "../services/agents";
import { requireReleaseOidc } from "../security/oidc";
import { errorResponse, json, parseJsonBounded, readBodyCapped } from "./responses";

type Json = Record<string, unknown>;

export function ownerRoute(fn: (ctx: AppContext, owner: Owner, req: Request, body: Json) => Promise<unknown>) {
  return async (req: Request): Promise<NextResponse> => {
    try {
      const ctx = await appContext();
      const owner = await requireOwner(ctx.db, req);
      const body = ["GET", "HEAD"].includes(req.method) ? {} : ((parseJsonBounded(await readBodyCapped(req, 64 * 1024)) as Json) ?? {});
      return json(await fn(ctx, owner, req, body));
    } catch (e) {
      return errorResponse(e);
    }
  };
}

export function agentRoute(opts: AgentAuthOptions, fn: (ctx: AppContext, agent: AgentIdentity, req: Request, body: Json) => Promise<unknown>,
  maxBytes = 256 * 1024) {
  return async (req: Request): Promise<NextResponse> => {
    try {
      const ctx = await appContext();
      const raw = await readBodyCapped(req, maxBytes);
      const agent = await authenticateAgent(ctx.db, req, raw, opts);
      // release identity: GitHub OIDC is the short-lived second factor on top of the HMAC (security/oidc.ts)
      if (opts.release) agent.oidc = await requireReleaseOidc(ctx.db, req, agent.agentId, { isDemo: ctx.isDemo });
      const body = raw.byteLength ? ((parseJsonBounded(raw) as Json) ?? {}) : {};
      return json(await fn(ctx, agent, req, body));
    } catch (e) {
      return errorResponse(e);
    }
  };
}
