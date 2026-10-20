import { createHash } from "node:crypto";
import { NextResponse } from "next/server";
import { serverEnv } from "@/server/env";
import { OAUTH_COOKIE, cookieSecure, randomToken, signOAuthState } from "@/server/security/session";

export const runtime = "nodejs";

export async function GET() {
  const env = serverEnv();
  if (!env.discordOAuth || !env.publicBaseUrl || !env.ownerDiscordIds.length) {
    return NextResponse.json({ error: { code: "NOT_CONFIGURED", message: "Discord owner login needs DISCORD_OAUTH_CLIENT_ID/SECRET, APEX_PUBLIC_BASE_URL and APEX_OWNER_DISCORD_IDS" } }, { status: 503 });
  }
  const state = randomToken(24);
  const verifier = randomToken(48);
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  const url = new URL("https://discord.com/oauth2/authorize");
  url.search = new URLSearchParams({ response_type: "code", client_id: env.discordOAuth.clientId, scope: "identify", state, prompt: "none",
    redirect_uri: `${env.publicBaseUrl}/api/auth/discord/callback`, code_challenge: challenge, code_challenge_method: "S256" }).toString();
  const res = NextResponse.redirect(url.toString());
  res.headers.append("Set-Cookie", `${OAUTH_COOKIE}=${await signOAuthState(state, verifier)}; Path=/api/auth/discord; HttpOnly; SameSite=Lax; Max-Age=600${cookieSecure() ? "; Secure" : ""}`);
  return res;
}
