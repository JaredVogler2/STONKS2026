import "server-only";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { createHash } from "node:crypto";
import { getDb, type Db } from "../db";
import { serverEnv } from "../env";
import { HttpError } from "../http/responses";
import { SESSION_COOKIE, SESSION_TTL_SECONDS, cookieSecure, signSession, verifySessionToken, type SessionClaims } from "../security/session";
import { audit } from "../services/audit";

export interface Owner {
  userId: string;
  discordUserId: string | null;
  sessionId: string;
  method: SessionClaims["method"];
}

export async function ownerFromToken(db: Db, token: string | undefined): Promise<Owner | null> {
  const claims = await verifySessionToken(token);
  if (!claims) return null;
  const rows = await db.query<{ revoked_at: Date | null; expires_at: Date; role: string; disabled_at: Date | null; discord_user_id: string | null }>(
    `SELECT s.revoked_at, s.expires_at, u.role, u.disabled_at, u.discord_user_id FROM apex.admin_sessions s
     JOIN apex.app_users u ON u.id = s.owner_user_id WHERE s.id = $1 AND s.owner_user_id = $2`,
    [claims.sid, claims.sub],
  );
  const r = rows[0];
  if (!r || r.revoked_at || new Date(r.expires_at).getTime() < Date.now() || r.role !== "owner" || r.disabled_at) return null;
  if (claims.method === "local_dev" && !serverEnv().allowLocalDevLogin) return null; // dev sessions die when the switch is off
  if (claims.method === "discord_oauth" && r.discord_user_id && !serverEnv().ownerDiscordIds.includes(r.discord_user_id)) return null;
  return { userId: claims.sub, discordUserId: r.discord_user_id, sessionId: claims.sid, method: claims.method };
}

/** For server components: null when not signed in. Authorization is still enforced on every API route. */
export async function currentOwner(): Promise<Owner | null> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) return null;
  try {
    return await ownerFromToken(await getDb(), token);
  } catch {
    return null;
  }
}

/** For pages: layouts and pages render concurrently, so every page re-checks the session itself. */
export async function pageOwner(): Promise<Owner> {
  const owner = await currentOwner();
  if (!owner) redirect("/login");
  return owner;
}

function cookieFrom(req: Request, name: string): string | undefined {
  const header = req.headers.get("cookie") ?? "";
  for (const part of header.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return decodeURIComponent(v.join("="));
  }
  return undefined;
}

/**
 * API guard: valid owner session AND (for mutations) same-origin + custom header. Cookies are SameSite=Strict,
 * so cross-site requests carry no session; the explicit checks are defence in depth. Identity never comes from
 * client-supplied headers such as X-User-Email.
 */
export async function requireOwner(db: Db, req: Request): Promise<Owner> {
  const owner = await ownerFromToken(db, cookieFrom(req, SESSION_COOKIE));
  if (!owner) throw new HttpError(401, "UNAUTHENTICATED", "owner sign-in required");
  if (!["GET", "HEAD", "OPTIONS"].includes(req.method)) {
    const site = req.headers.get("sec-fetch-site");
    const origin = req.headers.get("origin");
    const host = req.headers.get("host");
    const sameOrigin = site ? site === "same-origin" : origin ? new URL(origin).host === host : false;
    if (!sameOrigin || req.headers.get("x-apex-csrf") !== "1") throw new HttpError(403, "CSRF", "cross-site request refused");
  }
  return owner;
}

export async function createOwnerSession(db: Db, userId: string, method: SessionClaims["method"], userAgent: string | null): Promise<string> {
  const rows = await db.query<{ id: string }>(
    "INSERT INTO apex.admin_sessions(owner_user_id, auth_method, expires_at, user_agent_hash) VALUES ($1, $2, now() + make_interval(secs => $3), $4) RETURNING id",
    [userId, method, SESSION_TTL_SECONDS, userAgent ? createHash("sha256").update(userAgent).digest("hex").slice(0, 32) : null],
  );
  await audit(db, { actorType: "owner", actorId: userId, entity: "admin_sessions", entityId: rows[0]!.id, action: "session.created", metadata: { method } });
  return signSession({ sid: rows[0]!.id, sub: userId, method });
}

export function sessionCookie(token: string): string {
  return `${SESSION_COOKIE}=${token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${SESSION_TTL_SECONDS}${cookieSecure() ? "; Secure" : ""}`;
}

export function clearedSessionCookie(): string {
  return `${SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0${cookieSecure() ? "; Secure" : ""}`;
}

/** Owner enrollment: only Discord ids listed in APEX_OWNER_DISCORD_IDS (or the DEMO seed owner) can become owner. */
export async function upsertOwner(db: Db, discordUserId: string): Promise<string> {
  const existing = await db.query<{ id: string; discord_user_id: string | null }>(
    "SELECT id, discord_user_id FROM apex.app_users WHERE role='owner' AND disabled_at IS NULL");
  if (existing[0] && existing[0].discord_user_id !== discordUserId) {
    throw new HttpError(409, "OWNER_EXISTS", "a different owner is already registered; disable it from the database runbook first");
  }
  const rows = await db.query<{ id: string }>(
    `INSERT INTO apex.app_users(discord_user_id, role, dm_opt_in) VALUES ($1, 'owner', true)
     ON CONFLICT (discord_user_id) DO UPDATE SET role = 'owner' RETURNING id`, [discordUserId]);
  return rows[0]!.id;
}
