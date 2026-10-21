import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { ActionButton } from "@/components/client/action-button";
import { Badge, Card, Notice, PageHeader, StatusPill } from "@/components/ui/primitives";
import { pageOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { migrationStatus } from "@/server/db/migrate";
import { demoStatus } from "@/server/services/demo";
import { computeHealth } from "@/server/services/health";
import { operations } from "@/server/services/queries";
import { getSettings, listIntegrations } from "@/server/services/settings";

export const dynamic = "force-dynamic";
export const metadata = { title: "Guided Setup" };

type Json = Record<string, unknown>;
const DOCS = (process.env.APEX_DOCS_BASE_URL ?? "https://github.com/JaredVogler2/STONKS2026/blob/main").replace(/\/$/, "");

interface Step {
  n: number; id: string; title: string; why: string; doc: { label: string; path: string }[];
  state: "PASS" | "FAIL" | "MANUAL" | "WAITING"; evidence: string; next: string; manualKey?: "pi_assembled" | "vercel_protected" | "replay_passed"; href?: string;
}

export default async function OnboardingPage() {
  await pageOwner();
  const ctx = await appContext();
  const now = await ctx.now();
  const [health, ops, integrations, settings, ms, replay] = await Promise.all([computeHealth(ctx.db, now), operations(ctx.db), listIntegrations(ctx.db),
    getSettings(ctx.db), migrationStatus(ctx.db), ctx.isDemo ? demoStatus(ctx.db) : Promise.resolve(null)]);
  const comp = (k: string) => health.components.find((c) => c.key === k);
  const manual = settings["onboarding.manual"];
  const devices = ops.devices as Json[];
  const active = devices.find((d) => d.status === "active");
  const hb = ops.heartbeat as Json | null;
  const alpaca = integrations.find((i) => i.provider === "alpaca");
  const discord = comp("discord_gateway");
  const feed = comp("market_feed");
  const scanner = comp("scanner");
  const dbOk = ms.pending.length === 0 && ms.checksumMismatch.length === 0;

  const steps: Step[] = [
    { n: 1, id: "pi", title: "Raspberry Pi assembled and booted", why: "The Pi runs the scanner, quote stream and Discord bot 24/7 at a few watts.",
      doc: [{ label: "Shopping list", path: "raspberry-pi/01_AMAZON_SHOPPING_LIST.md" }, { label: "Assemble", path: "raspberry-pi/02_ASSEMBLE_HARDWARE.md" },
        { label: "First boot & SSH", path: "raspberry-pi/03_FIRST_BOOT_AND_SSH.md" }],
      state: hb ? "PASS" : manual.value.pi_assembled ? "MANUAL" : "WAITING",
      evidence: hb ? `heartbeat received from ${String(hb.agent_id)}` : manual.value.pi_assembled ? "confirmed by you (no heartbeat yet)" : "no heartbeat yet",
      next: "Follow the guides, then confirm here or just pair it (step 5).", manualKey: "pi_assembled" },
    { n: 2, id: "discord", title: "Discord bot invited to your server", why: "Members use slash commands and buttons; alerts go to mapped channels.",
      doc: [{ label: "Discord configuration", path: "raspberry-pi/04_DISCORD_CONFIGURATION.md" }, { label: "Bot guide", path: "docs/DISCORD_BOT.md" }],
      state: discord?.status === "OK" && !ctx.isDemo ? "PASS" : ctx.isDemo ? "WAITING" : discord?.status === "UNKNOWN" ? "WAITING" : "FAIL",
      evidence: ctx.isDemo ? "DEMO uses a dry-run adapter (no token)" : `gateway: ${discord?.detail ?? "not reported"}`,
      next: "Invite with the permissions printed by `apex bot --dry-run` (no Administrator), then map channels in Settings.", href: "/settings#discord" },
    { n: 3, id: "vercel", title: "Vercel deployment protected", why: "Only you may reach the dashboard; machine routes use signed requests.",
      doc: [{ label: "Security & integration", path: "raspberry-pi/07_SECURITY_AND_INTEGRATION.md" }],
      state: manual.value.vercel_protected ? "MANUAL" : "WAITING", evidence: manual.value.vercel_protected ? "confirmed by you" : "cannot be verified from inside the app",
      next: "Enable Vercel Authentication for all deployments, then run `node scripts/smoke-deployment.mjs <url>` from your computer and confirm.", manualKey: "vercel_protected" },
    { n: 4, id: "db", title: "Supabase Postgres connected", why: "Durable storage for signals, positions (row-level security) and the model registry.",
      doc: [{ label: "Operator guide", path: "docs/README_OPERATOR.md" }],
      state: ctx.db.kind === "postgres" && dbOk ? "PASS" : ctx.db.kind === "postgres" ? "FAIL" : "WAITING",
      evidence: ctx.db.kind === "postgres" ? `${ms.applied.length} migrations applied, ${ms.pending.length} pending` : "embedded DEMO database in use",
      next: "Set DATABASE_URL (pooler, transaction mode) in Vercel and run `pnpm --filter @apex/web db:migrate`." },
    { n: 5, id: "pair", title: "Pi paired with this dashboard", why: "Pairing gives the Pi its own key; no shared passwords travel over chat.",
      doc: [{ label: "Install", path: "raspberry-pi/05_INSTALL_BOT.md" }, { label: "Pi worker", path: "docs/PI_WORKER.md" }],
      state: active ? "PASS" : devices.some((d) => d.status === "pending_approval") ? "FAIL" : "WAITING",
      evidence: active ? `${String(active.device_name)} active (fingerprint ${String(active.fingerprint)})` : devices.length ? "device waiting for your approval" : "no device",
      next: "Settings → Pi pairing → Start pairing, run the command on the Pi, compare fingerprints, approve.", href: "/settings#pairing" },
    { n: 6, id: "alpaca", title: "Alpaca market data connected", why: "Free IEX quotes (≤30 symbols) and delayed SIP history. Never used for orders.",
      doc: [{ label: "Security & integration", path: "raspberry-pi/07_SECURITY_AND_INTEGRATION.md" }],
      state: alpaca?.status === "CONNECTED" && (alpaca.pi_ack_version ?? 0) >= (alpaca.secret_version ?? 0) ? "PASS" : alpaca?.status === "TEST_FAILED" ? "FAIL" : "WAITING",
      evidence: alpaca ? `status ${alpaca.status}; Pi has secret v${alpaca.pi_ack_version} of v${alpaca.secret_version}` : "not configured",
      next: "Settings → Integrations → Alpaca: save, Test connection, then wait for the Pi to acknowledge.", href: "/settings#integrations" },
    { n: 7, id: "health", title: "Data health checks pass", why: "Scans only run on fresh, validated data; stale inputs block new alerts.",
      doc: [{ label: "Troubleshooting", path: "raspberry-pi/06_OPERATIONS_TROUBLESHOOTING.md" }],
      state: !ctx.isDemo && feed?.status === "OK" && scanner?.status === "OK" ? "PASS" : !ctx.isDemo && (feed?.status === "DOWN" || scanner?.status === "STALE") ? "FAIL" : "WAITING",
      evidence: `feed ${feed?.status ?? "?"}, scanner ${scanner?.status ?? "?"}${ctx.isDemo ? " (DEMO replay, not live data)" : ""}`,
      next: "Leave the worker running through one market session; check Operations if a light stays red.", href: "/operations" },
    { n: 8, id: "replay", title: "Private replay scenarios pass", why: "Deterministic scenarios prove fills, stops, DM privacy and model safety before anything goes live.",
      doc: [{ label: "Pi worker", path: "docs/PI_WORKER.md" }],
      state: ctx.isDemo && replay && replay.cursor > 0 ? "PASS" : manual.value.replay_passed ? "MANUAL" : "WAITING",
      evidence: ctx.isDemo && replay ? `DEMO replay at step ${replay.cursor}/${replay.total}` : manual.value.replay_passed ? "confirmed by you" : "not run",
      next: "On the Pi: `uv run apex replay --all` (all 12 scenarios must pass), then confirm.", manualKey: "replay_passed" },
  ];
  const done = steps.filter((s) => s.state === "PASS" || s.state === "MANUAL").length;

  return (
    <>
      <PageHeader title="Guided Setup" subtitle={`${done} of ${steps.length} steps complete. Automated checks run on every visit; steps the app cannot see are confirmed by you and recorded.`} />
      <Notice tone="warn" title="Signals stay research-only until the evidence exists">
        Even with every step green, APEX publishes high-conviction alerts only after a model passes the frozen out-of-sample gates, and group position guidance only after a recorded external compliance review.
      </Notice>
      <ol className="space-y-3">
        {steps.map((s) => (
          <li key={s.id}>
            <Card title={`${s.n}. ${s.title}`} definition={s.why}
              actions={<StatusPill status={s.state === "PASS" ? "OK" : s.state === "MANUAL" ? "OK" : s.state === "FAIL" ? "DOWN" : "UNKNOWN"}
                label={s.state === "MANUAL" ? "Confirmed" : s.state === "PASS" ? "Verified" : s.state === "FAIL" ? "Needs attention" : "Waiting"} />}>
              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                <div className="space-y-1.5 text-[13px]">
                  <div><span className="text-faint">Check: </span>{s.evidence}</div>
                  <div><span className="text-faint">Next: </span>{s.next}{s.href ? <> <Link href={s.href} className="text-info underline decoration-info/40 underline-offset-2 hover:decoration-info">Open →</Link></> : null}</div>
                </div>
                <div className="flex flex-wrap items-start gap-2">
                  {s.doc.map((d) => (
                    <a key={d.path} href={`${DOCS}/${d.path}`} target="_blank" rel="noreferrer noopener"
                      className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 text-[12px] text-info hover:bg-surface-2">
                      {d.label} <ExternalLink aria-hidden="true" className="size-3" />
                    </a>
                  ))}
                  {s.manualKey && s.state !== "PASS" ? (
                    <ActionButton url="/api/v1/admin/settings" method="PUT" variant={manual.value[s.manualKey] ? "ghost" : "secondary"}
                      label={manual.value[s.manualKey] ? "Undo confirmation" : "I've done this"}
                      body={{ key: "onboarding.manual", value: { ...manual.value, [s.manualKey]: !manual.value[s.manualKey] }, version: manual.version }} />
                  ) : null}
                  {s.state === "PASS" ? <Badge tone="pos">verified automatically</Badge> : null}
                </div>
              </div>
            </Card>
          </li>
        ))}
      </ol>
    </>
  );
}
