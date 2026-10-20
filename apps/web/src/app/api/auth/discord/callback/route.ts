import { NextResponse } from "next/server";
import { createOwnerSession, sessionCookie, upsertOwner } from "@/server/auth/owner";
import { getDb } from "@/server/db";
import { serverEnv } from "@/server/env";
import { OAUTH_COOKIE, verifyOAuthState } from "@/server/security/session";
import { audit } from "@/server/services/audit";

export const runtime = "nodejs";

function fail(base: string, code: string) {
  return NextResponse.redirect(`${base}/login?error=${encodeURIComponent(code)}`);
}

export async function GET(req: Request) {
  const env = serverEnv();
  const base = env.publicBaseUrl ?? new URL(req.url).origin;
  if (!env.discordOAuth || !env.publicBaseUrl) return fail(base, "not_configured");
  const url = new URL(req.url);
  const cookie = (req.headers.get("cookie") ?? "").split(";").map((c) => c.trim()).find((c) => c.startsWith(`${OAUTH_COOKIE}=`));
  const st = await verifyOAuthState(cookie?.slice(OAUTH_COOKIE.length + 1));
  if (!st || st.state !== url.searchParams.get("state")) return fail(base, "state_mismatch");
  const code = url.searchParams.get("code");
  if (!code) return fail(base, "no_code");
  const tokenRes = await fetch("https://discord.com/api/oauth2/token", {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, signal: AbortSignal.timeout(8000),
    body: new URLSearchParams({ client_id: env.discordOAuth.clientId, client_secret: env.discordOAuth.clientSecret, grant_type: "authorization_code",
      code, redirect_uri: `${env.publicBaseUrl}/api/auth/discord/callback`, code_verifier: st.verifier }),
  });
  if (!tokenRes.ok) return fail(base, "token_exchange_failed");
  const { access_token } = (await tokenRes.json()) as { access_token?: string };
  const me = await fetch("https://discord.com/api/users/@me", { headers: { Authorization: `Bearer ${access_token}` }, signal: AbortSignal.timeout(8000) });
  if (!me.ok) return fail(base, "identity_failed");
  const user = (await me.json()) as { id?: string };
  const db = await getDb();
  if (!user.id || !env.ownerDiscordIds.includes(user.id)) {
    await audit(db, { actorType: "system", entity: "admin_sessions", action: "session.denied_non_owner" });
    return fail(base, "not_owner");
  }
  const userId = await upsertOwner(db, user.id);
  const token = await createOwnerSession(db, userId, "discord_oauth", req.headers.get("user-agent"));
  const res = NextResponse.redirect(`${base}/`);
  res.headers.append("Set-Cookie", sessionCookie(token));
  res.headers.append("Set-Cookie", `${OAUTH_COOKIE}=; Path=/api/auth/discord; Max-Age=0`);
  return res;
}
