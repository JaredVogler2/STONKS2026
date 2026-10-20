import { cookies } from "next/headers";
import Link from "next/link";
import { redirect } from "next/navigation";
import { MobileNav, SideNav } from "@/components/client/nav";
import { Masthead } from "@/components/shell/masthead";
import { currentOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { marketPhase } from "@/server/market/calendar";
import { computeHealth } from "@/server/services/health";

export const dynamic = "force-dynamic";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const owner = await currentOwner();
  if (!owner) redirect("/login");
  const ctx = await appContext();
  const now = await ctx.now();
  const health = await computeHealth(ctx.db, now);
  const jar = await cookies();
  const theme = jar.get("apex_theme")?.value === "light" ? "light" : "dark";
  const privacy = jar.get("apex_privacy")?.value === "on";
  const scan = health.components.find((c) => c.key === "scanner");
  const alpaca = health.components.find((c) => c.key === "integration_alpaca");
  const compliance = health.components.find((c) => c.key === "compliance");
  return (
    <div className="min-h-dvh md:grid md:grid-cols-[232px_minmax(0,1fr)]">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-surface focus:px-3 focus:py-2">
        Skip to content
      </a>
      <aside className="no-print sticky top-0 hidden h-dvh flex-col gap-4 border-r border-line bg-surface/40 px-3 py-4 md:flex">
        <Link href="/" className="px-2.5">
          <div className="text-sm font-bold tracking-[0.2em] text-info">APEX</div>
          <div className="text-[11px] text-faint">Adaptive Predictive Equity eXchange</div>
        </Link>
        <SideNav />
        <div className="mt-auto space-y-1 px-2.5 text-[11px] leading-snug text-faint">
          <p>Research signals only. Not investment advice. APEX never places orders.</p>
          <p>Quality is a ranking, not a probability.</p>
        </div>
      </aside>
      <div className="min-w-0">
        <Masthead health={health} phase={marketPhase(now)} nowIso={now.toISOString()} isDemo={ctx.isDemo} theme={theme} privacy={privacy}
          asofIso={scan?.last_ok_at ?? null} />
        <div className="no-print space-y-2 px-4 pt-3 md:px-6">
          {ctx.isDemo ? (
            <div role="status" className="rounded-lg border border-demo/50 bg-demo/10 px-3.5 py-2 text-[12px] text-demo">
              <strong>DEMO replay.</strong> Fictional DM* tickers and synthetic prices from a deterministic fixture. Nothing here is market data or
              advice. <Link href="/operations#replay" className="underline underline-offset-2">Advance the replay</Link>
            </div>
          ) : null}
          {!ctx.isDemo && alpaca && alpaca.status !== "OK" ? (
            <div role="alert" className="rounded-lg border border-warn/50 bg-warn/10 px-3.5 py-2 text-[12px] text-warn">
              <strong>Market data credentials unavailable.</strong> {alpaca.detail}. <Link href="/settings#integrations" className="underline">Connect Alpaca</Link>
            </div>
          ) : null}
          {!ctx.isDemo && compliance?.status !== "OK" ? (
            <div className="rounded-lg border border-line-strong bg-surface-2 px-3.5 py-2 text-[12px] text-muted">
              Group personalized guidance is <strong className="text-fg">OFF</strong>; member position updates use informational wording only.
            </div>
          ) : null}
        </div>
        <main id="main" className="mx-auto w-full max-w-[1480px] space-y-5 px-4 pb-28 pt-4 md:px-6 md:pb-10">{children}</main>
      </div>
      <MobileNav />
    </div>
  );
}
