import "server-only";
import { SignJWT, jwtVerify } from "jose";
import { randomBytes } from "node:crypto";
import { serverEnv } from "../env";

export const SESSION_COOKIE = "apex_session";
export const OAUTH_COOKIE = "apex_oauth";
export const SESSION_TTL_SECONDS = 8 * 60 * 60;

export interface SessionClaims {
  sid: string;
  sub: string;
  method: "discord_oauth" | "local_dev";
}

export async function signSession(claims: SessionClaims): Promise<string> {
  return new SignJWT({ sid: claims.sid, method: claims.method })
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setSubject(claims.sub)
    .setIssuer("apex")
    .setAudience("apex-admin")
    .setIssuedAt()
    .setExpirationTime(`${SESSION_TTL_SECONDS}s`)
    .sign(serverEnv().sessionSecret);
}

export async function verifySessionToken(token: string | undefined): Promise<SessionClaims | null> {
  if (!token || token.length > 2048) return null;
  try {
    const { payload } = await jwtVerify(token, serverEnv().sessionSecret, {
      issuer: "apex",
      audience: "apex-admin",
      algorithms: ["HS256"],
    });
    if (typeof payload.sid !== "string" || typeof payload.sub !== "string") return null;
    const method = payload.method === "discord_oauth" || payload.method === "local_dev" ? payload.method : null;
    if (!method) return null;
    return { sid: payload.sid, sub: payload.sub, method };
  } catch {
    return null;
  }
}

export async function signOAuthState(state: string, verifier: string): Promise<string> {
  return new SignJWT({ state, verifier })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuer("apex")
    .setAudience("apex-oauth")
    .setIssuedAt()
    .setExpirationTime("10m")
    .sign(serverEnv().sessionSecret);
}

export async function verifyOAuthState(token: string | undefined): Promise<{ state: string; verifier: string } | null> {
  if (!token) return null;
  try {
    const { payload } = await jwtVerify(token, serverEnv().sessionSecret, { issuer: "apex", audience: "apex-oauth" });
    if (typeof payload.state !== "string" || typeof payload.verifier !== "string") return null;
    return { state: payload.state, verifier: payload.verifier };
  } catch {
    return null;
  }
}

export function randomToken(bytes = 32): string {
  return randomBytes(bytes).toString("base64url");
}

export function cookieSecure(): boolean {
  const env = serverEnv();
  return env.onVercel || (env.publicBaseUrl?.startsWith("https://") ?? false);
}
