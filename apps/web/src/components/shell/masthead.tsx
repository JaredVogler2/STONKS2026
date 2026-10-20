import { LogOut } from "lucide-react";
import Link from "next/link";
import { EtClock } from "@/components/client/et-clock";
import { PrivacyToggle, ThemeToggle } from "@/components/client/prefs";
import { Badge, StatusPill, type Tone } from "@/components/ui/primitives";
import { fmtAge } from "@/lib/format";
import type { PhaseInfo } from "@/server/market/calendar";
import type { ServiceHealth } from "@/server/services/health";

const PHASE_TONE: Record<PhaseInfo["phase"], Tone> = { OPEN: "pos", PRE: "info", AFTER: "info", CLOSED: "neutral" };
const MODE_TONE: Record<string, Tone> = { DEMO: "demo", PAPER_LIVE_DATA: "info", PERSONAL_RESEARCH: "warn", GROUP_POSITION_ALERTS: "warn" };
const MODE_LABEL: Record<string, string> = {
  DEMO: "DEMO · synthetic", PAPER_LIVE_DATA: "LIVE data · paper", PERSONAL_RESEARCH: "LIVE · personal research", GROUP_POSITION_ALERTS: "LIVE · group alerts",
};

export function Masthead({ health, phase, nowIso, isDemo, theme, privacy, asofIso }: {
  health: ServiceHealth; phase: PhaseInfo; nowIso: string; isDemo: boolean; theme: "dark" | "light"; privacy: boolean; asofIso: string | null;
}) {
  const light = (key: string, label: string) => {
    const c = health.components.find((x) => x.key === key);
    return <StatusPill key={key} status={c?.status ?? "UNKNOWN"} label={label} />;
  };
  const asofAge = asofIso ? Math.max(0, Math.floor((new Date(nowIso).getTime() - new Date(asofIso).getTime()) / 1000)) : null;
  return (
    <header className="no-print sticky top-0 z-30 border-b border-line bg-bg/90 backdrop-blur">
      <div className="flex min-h-12 items-center gap-3 px-4 md:px-6">
        <Link href="/" className="flex items-baseline gap-1.5 md:hidden" aria-label="APEX home">
          <span className="text-sm font-bold tracking-[0.18em] text-info">APEX</span>
        </Link>
        <div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto py-2 [scrollbar-width:none]">
          <Badge tone={PHASE_TONE[phase.phase]} title={phase.early_close ? "Early close today" : undefined}>
            {phase.label}
            {phase.early_close ? " · early close" : ""}
          </Badge>
          <span className="text-xs text-muted"><EtClock initialIso={nowIso} frozen={isDemo} /></span>
          <span className="hidden h-4 w-px bg-line sm:block" aria-hidden="true" />
          <span className="hidden items-center gap-1.5 sm:flex">
            {light("pi_agent", "Pi")}
            {light("market_feed", "Feed")}
            {light("database", "DB")}
            {light("discord_gateway", "Discord")}
          </span>
          <span className="hidden text-[11px] text-faint lg:inline">data as of {asofAge === null ? "n/a" : `${fmtAge(asofAge)} ago`}</span>
        </div>
        <Badge tone={MODE_TONE[health.release_mode] ?? "neutral"}>{MODE_LABEL[health.release_mode] ?? health.release_mode}</Badge>
        <div className="flex items-center">
          <PrivacyToggle initial={privacy} />
          <ThemeToggle initial={theme} />
          <form method="post" action="/api/auth/logout">
            <button type="submit" aria-label="Sign out" className="inline-flex min-h-8 items-center rounded-lg px-2 text-muted hover:bg-surface-2 hover:text-fg">
              <LogOut aria-hidden="true" className="size-4" />
            </button>
          </form>
        </div>
      </div>
      <div className="flex items-center gap-1.5 overflow-x-auto px-4 pb-2 sm:hidden [scrollbar-width:none]">
        {light("pi_agent", "Pi")}
        {light("market_feed", "Feed")}
        {light("database", "DB")}
        {light("discord_gateway", "Discord")}
      </div>
    </header>
  );
}
