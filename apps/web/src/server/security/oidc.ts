import "server-only";
import { createHash, createPublicKey, verify as verifySignature, type JsonWebKey, type KeyObject } from "node:crypto";
import type { Db } from "../db";
import { HttpError } from "../http/responses";
import { audit, securityEvent } from "../services/audit";

/*
 * GitHub Actions OIDC as the short-lived second factor of the CI release identity (amendment 2026-10-20, Phase 11).
 *
 * The `rel_*` HMAC secret lives in the protected `model-release` GitHub Environment, but it is long-lived. Every release
 * request must also carry `x-apex-oidc`: a GitHub-issued RS256 JWT, minutes-long, fetched per request by the job
 * (`permissions: id-token: write`) and bound here to this repository, the `model-release` environment, the release
 * branch, a scheduled or manually dispatched run and an `apex-model-*` workflow file. A leaked HMAC secret alone can no
 * longer call the release API, and each token is single use (its `jti` is burnt in apex.agent_nonces until it expires).
 */

export const GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com";
export const GITHUB_OIDC_JWKS_URL = `${GITHUB_OIDC_ISSUER}/.well-known/jwks`;
export const RELEASE_ENVIRONMENT = "model-release";
const ALLOWED_EVENTS = new Set(["schedule", "workflow_dispatch"]);
const SKEW_SECONDS = 60;
const MAX_LIFETIME_SECONDS = 60 * 60; // GitHub's tokens live minutes; anything longer is not one of them
const MAX_TOKEN_BYTES = 8 * 1024;
const REPO_RE = /^[A-Za-z0-9-]{1,39}\/[A-Za-z0-9._-]{1,100}$/;
const B64URL = /^[A-Za-z0-9_-]+$/;

export interface ReleaseOidcConfig {
  /** owner/repo the token must come from (APEX_RELEASE_GITHUB_REPOSITORY); required outside DEMO */
  repository: string | null;
  audience: string;
  /** branch name, without refs/heads/ */
  ref: string;
  off: boolean;
}

export function releaseOidcConfig(env: Record<string, string | undefined> = process.env): ReleaseOidcConfig {
  return {
    repository: env.APEX_RELEASE_GITHUB_REPOSITORY?.trim() || null,
    audience: env.APEX_RELEASE_OIDC_AUDIENCE?.trim() || "apex-release",
    ref: env.APEX_RELEASE_GITHUB_REF?.trim().replace(/^refs\/heads\//, "") || "main",
    off: env.APEX_RELEASE_OIDC?.trim().toLowerCase() === "off",
  };
}

export type OidcFailure =
  | "MALFORMED" | "ALGORITHM" | "UNKNOWN_KEY" | "SIGNATURE" | "ISSUER" | "AUDIENCE" | "EXPIRED" | "NOT_YET_VALID"
  | "LIFETIME" | "REPOSITORY" | "ENVIRONMENT" | "REF" | "EVENT" | "WORKFLOW" | "JTI";

export class OidcError extends Error {
  constructor(public readonly reason: OidcFailure) {
    super(`GitHub OIDC token rejected (${reason})`);
  }
}

export interface ReleaseOidcClaims {
  jti: string;
  exp: number;
  repository: string;
  ref: string;
  event_name: string;
  run_id: string | null;
  run_attempt: string | null;
  sha: string | null;
  workflow_ref: string;
}

type Jwks = { keys?: unknown };
export type JwksFetcher = () => Promise<Jwks>;

async function fetchGithubJwks(): Promise<Jwks> {
  const res = await fetch(GITHUB_OIDC_JWKS_URL, { signal: AbortSignal.timeout(5000), headers: { accept: "application/json" }, cache: "no-store" });
  if (!res.ok) throw new Error(`JWKS HTTP ${res.status}`);
  return (await res.json()) as Jwks;
}

/**
 * RSA signing keys by `kid`, cached ~1 h; an unknown kid triggers one refetch (at most once a minute). If a refetch fails,
 * keys fetched within the last 24 h keep working (GitHub rotates rarely); with nothing usable the error propagates.
 */
export class JwksCache {
  private keys = new Map<string, KeyObject>();
  private fetchedAt = 0;
  private lastAttempt = 0;

  constructor(private readonly fetcher: JwksFetcher = fetchGithubJwks, private readonly ttlMs = 60 * 60 * 1000,
    private readonly minRefetchMs = 60 * 1000, private readonly maxStaleMs = 24 * 60 * 60 * 1000) {}

  async key(kid: string, nowMs = Date.now()): Promise<KeyObject | null> {
    const stale = nowMs - this.fetchedAt > this.ttlMs;
    if (stale || (!this.keys.has(kid) && nowMs - this.lastAttempt >= this.minRefetchMs)) {
      try {
        await this.refresh(nowMs);
      } catch (e) {
        if (!this.keys.size || nowMs - this.fetchedAt > this.maxStaleMs) throw e;
      }
    }
    return this.keys.get(kid) ?? null;
  }

  private async refresh(nowMs: number): Promise<void> {
    this.lastAttempt = nowMs;
    const body = await this.fetcher(); // network errors propagate: the caller answers 503, never "valid"
    if (!body || !Array.isArray(body.keys)) throw new Error("JWKS malformed");
    const next = new Map<string, KeyObject>();
    for (const k of body.keys as (JsonWebKey & { kid?: unknown; use?: unknown; alg?: unknown })[]) {
      if (k?.kty !== "RSA" || typeof k.kid !== "string") continue;
      if (k.use !== undefined && k.use !== "sig") continue;
      if (k.alg !== undefined && k.alg !== "RS256") continue;
      try {
        next.set(k.kid, createPublicKey({ key: { kty: "RSA", n: k.n, e: k.e }, format: "jwk" }));
      } catch {
        // skip a malformed key rather than failing the whole set
      }
    }
    this.keys = next;
    this.fetchedAt = nowMs;
  }
}

let defaultJwks = new JwksCache();

/** Tests only: route-level tests sign tokens with a local key. */
export function __setJwksForTests(cache: JwksCache | null): void {
  defaultJwks = cache ?? new JwksCache();
}

function segment(s: string | undefined): Record<string, unknown> {
  if (!s || !B64URL.test(s)) throw new OidcError("MALFORMED");
  try {
    const v = JSON.parse(Buffer.from(s, "base64url").toString("utf8"));
    if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error("not an object");
    return v as Record<string, unknown>;
  } catch {
    throw new OidcError("MALFORMED");
  }
}

const str = (v: unknown): string | null => (typeof v === "string" && v.length > 0 && v.length <= 512 ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** Verify a GitHub Actions OIDC JWT for the release identity. Throws OidcError; JWKS outages throw plain Errors. */
export async function verifyGithubOidcToken(token: string, cfg: ReleaseOidcConfig,
  opts: { jwks?: JwksCache; nowSeconds?: number } = {}): Promise<ReleaseOidcClaims> {
  if (token.length > MAX_TOKEN_BYTES) throw new OidcError("MALFORMED");
  const parts = token.split(".");
  if (parts.length !== 3 || !parts[2] || !B64URL.test(parts[2])) throw new OidcError("MALFORMED");
  const header = segment(parts[0]);
  const claims = segment(parts[1]);
  if (header.alg !== "RS256") throw new OidcError("ALGORITHM"); // never none / HS* / anything we did not ask for
  const kid = str(header.kid);
  if (!kid) throw new OidcError("UNKNOWN_KEY");
  const now = opts.nowSeconds ?? Math.floor(Date.now() / 1000);
  const key = await (opts.jwks ?? defaultJwks).key(kid, now * 1000);
  if (!key) throw new OidcError("UNKNOWN_KEY");
  const ok = verifySignature("sha256", Buffer.from(`${parts[0]}.${parts[1]}`), key, Buffer.from(parts[2], "base64url"));
  if (!ok) throw new OidcError("SIGNATURE");

  if (claims.iss !== GITHUB_OIDC_ISSUER) throw new OidcError("ISSUER");
  const aud = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!aud.includes(cfg.audience)) throw new OidcError("AUDIENCE");
  const exp = num(claims.exp);
  const iat = num(claims.iat);
  const nbf = claims.nbf === undefined ? null : num(claims.nbf);
  if (exp === null || iat === null || (claims.nbf !== undefined && nbf === null)) throw new OidcError("MALFORMED");
  if (now > exp + SKEW_SECONDS) throw new OidcError("EXPIRED");
  if (iat > now + SKEW_SECONDS || (nbf !== null && nbf > now + SKEW_SECONDS)) throw new OidcError("NOT_YET_VALID");
  if (exp - iat > MAX_LIFETIME_SECONDS) throw new OidcError("LIFETIME");

  const repository = str(claims.repository);
  if (!repository) throw new OidcError("REPOSITORY");
  if (cfg.repository && repository.toLowerCase() !== cfg.repository.toLowerCase()) throw new OidcError("REPOSITORY");
  if (claims.environment !== RELEASE_ENVIRONMENT) throw new OidcError("ENVIRONMENT");
  if (claims.ref !== `refs/heads/${cfg.ref}`) throw new OidcError("REF");
  const event = str(claims.event_name);
  if (!event || !ALLOWED_EVENTS.has(event)) throw new OidcError("EVENT");
  // the job's own workflow file (job_workflow_ref names the called file for a reusable workflow) must be apex-model-*
  const workflowRef = str(claims.job_workflow_ref) ?? str(claims.workflow_ref);
  const wf = workflowRef?.match(/^([^/]+\/[^/]+)\/\.github\/workflows\/apex-model-[a-z0-9-]+\.ya?ml@refs\/heads\/(.+)$/);
  if (!wf || wf[1]!.toLowerCase() !== repository.toLowerCase() || wf[2] !== cfg.ref) throw new OidcError("WORKFLOW");
  const jti = str(claims.jti);
  if (!jti || jti.length > 256) throw new OidcError("JTI");
  return {
    jti, exp, repository, ref: String(claims.ref), event_name: event, workflow_ref: workflowRef!,
    run_id: str(claims.run_id), run_attempt: str(claims.run_attempt), sha: str(claims.sha),
  };
}

let warnedOff = false;

/**
 * Enforce the OIDC factor on a release request whose HMAC already verified.
 * - Outside DEMO: required; 503 when APEX_RELEASE_GITHUB_REPOSITORY is unset.
 * - DEMO: optional, but a token that is sent is verified (repository checked when configured).
 * - APEX_RELEASE_OIDC=off: explicit escape hatch (warning logged), HMAC only.
 * Returns the verified claims, or null when no token was required.
 */
export async function requireReleaseOidc(db: Db, req: Request, agentId: string,
  opts: { isDemo: boolean; env?: Record<string, string | undefined>; jwks?: JwksCache; nowSeconds?: number }): Promise<ReleaseOidcClaims | null> {
  const cfg = releaseOidcConfig(opts.env);
  const route = new URL(req.url).pathname;
  if (cfg.off) {
    if (!warnedOff) {
      warnedOff = true;
      console.warn("APEX_RELEASE_OIDC=off: release API accepts HMAC-only CI requests (no GitHub OIDC second factor)");
    }
    return null;
  }
  if (cfg.repository && !REPO_RE.test(cfg.repository)) {
    throw new HttpError(503, "RELEASE_OIDC_NOT_CONFIGURED", "APEX_RELEASE_GITHUB_REPOSITORY must look like owner/repo");
  }
  if (!cfg.repository && !opts.isDemo) {
    throw new HttpError(503, "RELEASE_OIDC_NOT_CONFIGURED",
      "set APEX_RELEASE_GITHUB_REPOSITORY=owner/repo in Vercel (the repository whose model-release workflows may call the release API) and redeploy; see docs/AUTONOMOUS_TRAINING.md");
  }
  const token = req.headers.get("x-apex-oidc")?.trim();
  if (!token) {
    if (opts.isDemo) return null;
    await securityEvent(db, "release_oidc_missing", { agentId, route });
    throw new HttpError(401, "RELEASE_OIDC_REQUIRED",
      "GitHub OIDC token required (x-apex-oidc): the calling job needs `permissions: id-token: write` in the model-release environment");
  }
  let claims: ReleaseOidcClaims;
  try {
    claims = await verifyGithubOidcToken(token, cfg, { jwks: opts.jwks, nowSeconds: opts.nowSeconds });
  } catch (e) {
    if (e instanceof OidcError) {
      await securityEvent(db, `release_oidc_${e.reason.toLowerCase()}`, { agentId, route });
      throw new HttpError(401, "RELEASE_OIDC_INVALID", e.message);
    }
    throw new HttpError(503, "RELEASE_OIDC_UNAVAILABLE", "GitHub OIDC signing keys could not be fetched; retry later");
  }
  // single use: burn the jti until the token expires (shared by every serverless instance, pruned by retention)
  const burnt = await db.query<{ nonce: string }>(
    `INSERT INTO apex.agent_nonces(nonce, agent_id, expires_at) VALUES ($1, $2, to_timestamp($3))
     ON CONFLICT (nonce) DO NOTHING RETURNING nonce`,
    [`oidc_${createHash("sha256").update(claims.jti).digest("base64url")}`, agentId, claims.exp + SKEW_SECONDS],
  );
  if (!burnt.length) {
    await securityEvent(db, "release_oidc_replayed", { agentId, route });
    throw new HttpError(409, "OIDC_REPLAYED", "GitHub OIDC token already used; request a fresh token per call");
  }
  if (req.method !== "GET" && req.method !== "HEAD") {
    await audit(db, { actorType: "system", actorId: agentId, entity: "release_api", entityId: route, action: "release.oidc_verified",
      metadata: { run_id: claims.run_id, run_attempt: claims.run_attempt, sha: claims.sha, workflow_ref: claims.workflow_ref,
        event_name: claims.event_name } });
  }
  return claims;
}
