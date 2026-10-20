import { redirect } from "next/navigation";
import { currentOwner } from "@/server/auth/owner";
import { serverEnv } from "@/server/env";

export const dynamic = "force-dynamic";
export const metadata = { title: "Sign in" };

const ERRORS: Record<string, string> = {
  not_owner: "That Discord account is not an APEX owner. Only IDs listed in APEX_OWNER_DISCORD_IDS can sign in.",
  state_mismatch: "The sign-in attempt expired or was tampered with. Start again.",
  no_code: "Discord did not complete the sign-in. Try again.",
  token_exchange_failed: "Discord rejected the sign-in exchange. Check the OAuth client secret and redirect URL.",
  identity_failed: "Could not read your Discord identity. Try again.",
  not_configured: "Discord sign-in is not configured on this deployment.",
};

export default async function LoginPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  if (await currentOwner()) redirect("/");
  const env = serverEnv();
  const sp = await searchParams;
  const error = typeof sp.error === "string" ? (ERRORS[sp.error] ?? `Sign-in failed (${sp.error.slice(0, 40)}).`) : null;
  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-6 px-4 py-10">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.2em] text-info">APEX</div>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">Trading Intelligence</h1>
        <p className="mt-2 text-sm text-muted">
          Private owner console. Discord members never need an account here; they interact through the bot.
        </p>
      </div>
      {env.mode === "DEMO" ? (
        <div className="rounded-lg border border-demo/50 bg-demo/10 px-3.5 py-2.5 text-[13px] text-demo">
          <strong>DEMO mode.</strong> Everything shown is synthetic (fictional DM* tickers). No market data, no Discord delivery.
        </div>
      ) : null}
      {error ? <div role="alert" className="rounded-lg border border-neg/50 bg-neg/10 px-3.5 py-2.5 text-[13px] text-neg">{error}</div> : null}
      <div className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4">
        {env.discordOAuth && env.publicBaseUrl ? (
          <a href="/api/auth/discord/start" className="inline-flex min-h-10 items-center justify-center rounded-lg border border-info/50 bg-info/15 px-4 text-sm font-medium hover:bg-info/25">
            Sign in with Discord
          </a>
        ) : (
          <div className="text-[13px] text-muted">
            Discord sign-in is not configured. Set <code className="text-fg">DISCORD_OAUTH_CLIENT_ID</code>,{" "}
            <code className="text-fg">DISCORD_OAUTH_CLIENT_SECRET</code>, <code className="text-fg">APEX_PUBLIC_BASE_URL</code> and{" "}
            <code className="text-fg">APEX_OWNER_DISCORD_IDS</code> (see docs/README_OPERATOR.md).
          </div>
        )}
        {env.allowLocalDevLogin ? (
          <form method="post" action="/api/auth/dev-login" className="border-t border-line pt-3">
            <button type="submit" className="inline-flex min-h-10 w-full items-center justify-center rounded-lg border border-demo/50 bg-demo/10 px-4 text-sm font-medium text-demo hover:bg-demo/20">
              Enter local DEMO as the demo owner
            </button>
            <p className="mt-2 text-[11px] text-faint">
              Available only for a local DEMO process with APEX_ALLOW_LOCAL_DEV_LOGIN=1 and no database URL. Refused on Vercel.
            </p>
          </form>
        ) : env.mode === "DEMO" ? (
          <p className="border-t border-line pt-3 text-[11px] text-faint">
            Local DEMO without Discord: run <code className="text-fg">pnpm demo</code> (sets APEX_ALLOW_LOCAL_DEV_LOGIN=1 for this process only).
          </p>
        ) : null}
      </div>
      <p className="text-[11px] leading-relaxed text-faint">
        Research and education tool. Signals are hypotheses with stated uncertainty, not recommendations. APEX never places orders and never
        connects to a brokerage account.
      </p>
    </main>
  );
}
