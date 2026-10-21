import "server-only";
import type { Db } from "../db";
import { serverEnv } from "../env";
import { HttpError } from "../http/responses";
import { NONCE_TTL_SECONDS, verifySignedRequest, type SignedHeaders } from "../security/hmac";
import type { ReleaseOidcClaims } from "../security/oidc";
import { securityEvent } from "./audit";

export interface AgentIdentity {
  agentId: string;
  status: "active" | "pending_approval" | "release";
  skewSeconds: number;
  /** release identity only: the verified GitHub OIDC claims (null when not required, e.g. DEMO without a token) */
  oidc?: ReleaseOidcClaims | null;
}

export interface AgentAuthOptions {
  /** pairing + heartbeat accept a device that is not yet approved (or not yet registered for pairing) */
  allowPending?: boolean;
  allowUnregistered?: boolean;
  /** CI release endpoints use the separate release secret and rel_* identities */
  release?: boolean;
}

function headersOf(req: Request): Partial<SignedHeaders> {
  return {
    agentId: req.headers.get("x-apex-agent-id") ?? undefined,
    timestamp: req.headers.get("x-apex-timestamp") ?? undefined,
    nonce: req.headers.get("x-apex-nonce") ?? undefined,
    bodySha256: req.headers.get("x-apex-body-sha256") ?? undefined,
    signature: req.headers.get("x-apex-signature") ?? undefined,
  };
}

function releaseSecrets(): Buffer[] {
  const raw = [process.env.APEX_RELEASE_HMAC_SECRET, process.env.APEX_RELEASE_HMAC_SECRET_PREVIOUS];
  return raw
    .filter((v): v is string => Boolean(v))
    .map((v) => Buffer.from(v, "base64"))
    .filter((b) => b.length >= 32);
}

/**
 * Authenticate a Pi (or CI release) request: signature over method+path+timestamp+nonce+body hash with
 * +/-60 s tolerance and constant-time compare, then device status, then single-use nonce.
 * 401 bad/missing/stale signature, 403 revoked or unapproved device, 409 replayed nonce.
 */
export async function authenticateAgent(db: Db, req: Request, body: Uint8Array, opts: AgentAuthOptions = {}): Promise<AgentIdentity> {
  const url = new URL(req.url);
  const pathWithQuery = url.pathname + url.search;
  const secrets = opts.release ? releaseSecrets() : serverEnv().agentHmacSecrets;
  const res = verifySignedRequest({
    secrets,
    method: req.method,
    pathWithQuery,
    headers: headersOf(req),
    body,
    nowSeconds: Math.floor(Date.now() / 1000),
  });
  const claimedId = req.headers.get("x-apex-agent-id")?.slice(0, 40) ?? null;
  if (!res.ok) {
    if (res.reason === "NOT_CONFIGURED") throw new HttpError(503, "AGENT_AUTH_NOT_CONFIGURED", "agent authentication is not configured on the server");
    await securityEvent(db, `hmac_${res.reason.toLowerCase()}`, { agentId: claimedId, route: url.pathname });
    if (res.reason === "CLOCK_SKEW") {
      throw new HttpError(401, "CLOCK_SKEW", "request timestamp outside the 60 s window; sync the Pi clock (NTP)", {
        server_time: Math.floor(Date.now() / 1000),
      });
    }
    throw new HttpError(401, "UNAUTHENTICATED", "missing or invalid request signature");
  }
  if (opts.release) {
    if (!res.agentId.startsWith("rel_")) throw new HttpError(403, "FORBIDDEN", "release identity required");
  } else {
    if (!res.agentId.startsWith("agt_")) throw new HttpError(403, "FORBIDDEN", "agent identity required");
    const rows = await db.query<{ status: string }>("SELECT status FROM apex.agent_devices WHERE id = $1", [res.agentId]);
    const status = rows[0]?.status;
    if (!status && !opts.allowUnregistered) {
      await securityEvent(db, "agent_unknown_device", { agentId: res.agentId, route: url.pathname });
      throw new HttpError(403, "DEVICE_NOT_PAIRED", "device is not paired");
    }
    if (status === "revoked") {
      await securityEvent(db, "agent_revoked_device", { agentId: res.agentId, route: url.pathname });
      throw new HttpError(403, "DEVICE_REVOKED", "device has been revoked");
    }
    if (status === "pending_approval" && !opts.allowPending) {
      throw new HttpError(403, "DEVICE_PENDING_APPROVAL", "device pairing awaits owner approval in the dashboard");
    }
  }
  // single-use nonce (insert-or-conflict is atomic)
  const inserted = await db.query<{ nonce: string }>(
    `INSERT INTO apex.agent_nonces(nonce, agent_id, expires_at) VALUES ($1, $2, now() + make_interval(secs => $3))
     ON CONFLICT (nonce) DO NOTHING RETURNING nonce`,
    [res.nonce, res.agentId, NONCE_TTL_SECONDS],
  );
  if (!inserted.length) {
    await securityEvent(db, "agent_replayed_nonce", { agentId: res.agentId, route: url.pathname });
    throw new HttpError(409, "NONCE_REPLAYED", "nonce already used");
  }
  if (!opts.release) {
    await db.query("UPDATE apex.agent_devices SET last_seen_at = now(), last_clock_skew_ms = $2 WHERE id = $1", [
      res.agentId,
      res.skewSeconds * 1000,
    ]);
  }
  return {
    agentId: res.agentId,
    status: opts.release ? "release" : (((await db.query<{ status: string }>("SELECT status FROM apex.agent_devices WHERE id=$1", [res.agentId]))[0]?.status ?? "pending_approval") as AgentIdentity["status"]),
    skewSeconds: res.skewSeconds,
  };
}
