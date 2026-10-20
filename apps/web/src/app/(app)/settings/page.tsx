import { ExternalLink } from "lucide-react";
import { headers } from "next/headers";
import { ActionButton } from "@/components/client/action-button";
import { ComplianceSwitch, IntegrationForm, PairingStarter, SettingEditor, type FieldSpec } from "@/components/client/settings-forms";
import { Badge, Card, EmptyState, Notice, PageHeader, Table, Td, Th, type Tone } from "@/components/ui/primitives";
import { ageSeconds, fmtAge, fmtEt, humanize } from "@/lib/format";
import { pageOwner } from "@/server/auth/owner";
import { appContext } from "@/server/context";
import { serverEnv } from "@/server/env";
import { operations } from "@/server/services/queries";
import { ACK_PHRASE, getSettings, listCompliance, listIntegrations } from "@/server/services/settings";

export const dynamic = "force-dynamic";
export const metadata = { title: "Settings & Secrets" };

type Json = Record<string, unknown>;
const num = (path: string, label: string, min: number, max: number, help?: string, step?: number): FieldSpec => ({ path, label, type: "number", min, max, help, step });
const bool = (path: string, label: string, help?: string): FieldSpec => ({ path, label, type: "boolean", help });

const FIELDS: Record<string, FieldSpec[]> = {
  "market.universe": [
    num("min_price", "Minimum price ($)", 1, 1000), num("min_median_dollar_volume", "Min median dollar volume", 1e6, 1e11, "20-session median of price × volume"),
    num("min_avg_volume", "Min average share volume", 1e4, 1e9), num("min_history_sessions", "Min history (sessions)", 60, 2000, "Features need ~180 sessions"),
    bool("include_etfs", "Include ETFs"), bool("exclude_leveraged", "Exclude leveraged/inverse ETFs"),
    { path: "include", label: "Always include (symbols)", type: "symbols", help: "Comma separated" },
    { path: "exclude", label: "Always exclude (symbols)", type: "symbols" },
    { path: "user_verified", label: "Verified tradable on Robinhood by you (symbols)", type: "symbols", help: "APEX cannot check Robinhood; this records your own verification." },
  ],
  "market.slots": [num("max_slots", "Live streamed symbols (IEX cap 30)", 1, 30), num("reserve_for_positions", "Slots reserved for open positions", 0, 30)],
  "market.cadence": [
    { path: "intraday_minutes", label: "Intraday check cadence", type: "select", options: [{ value: "15", label: "Every 15 minutes" }, { value: "30", label: "Every 30 minutes" }] },
    { path: "nightly_refresh_et", label: "Nightly refresh (ET, HH:MM)", type: "text" },
  ],
  "risk.policy": [
    num("min_quality", "Min Quality for high-conviction", 50, 100), num("max_new_per_session", "Max new signals per session", 0, 10),
    num("max_open_per_sector", "Max open signals per sector", 1, 10), bool("research_setups_enabled", "Publish UNVALIDATED research setups"),
    num("research_min_quality", "Min Quality for research setups", 50, 100), num("entry_valid_sessions", "Entry zone valid (sessions)", 1, 5),
    num("hypothetical_risk_per_trade_pct", "Illustrative risk per trade (%)", 0.1, 2, "Only for the illustration text in alerts", 0.1),
    num("portfolio_open_risk_cap_pct", "Illustrative open-risk cap (%)", 0.5, 10, undefined, 0.5),
  ],
  "discord.channels": [
    { path: "guild_id", label: "Server (guild) id", type: "snowflake" },
    ...["high_conviction_alerts", "active_trades", "exit_alerts", "market_intelligence", "performance_reports", "system_status"].map((k) =>
      ({ path: `channels.${k}`, label: `#${k.replace(/_/g, "-")} channel id`, type: "snowflake" as const })),
    { path: "high_priority_role_id", label: "Opt-in mention role id", type: "snowflake", help: "Members opt in themselves; never @everyone." },
  ],
  "models.policy": [num("poll_minutes", "Pi model poll interval (minutes)", 15, 120, "Amendment: 30–60 recommended")],
  budgets: [
    num("actions_monthly_minutes", "GitHub Actions minutes budget / month", 0, 50000, "Shared with every repo and workflow on the account"),
    num("train_timeout_minutes", "Training job timeout (minutes)", 5, 120), bool("drift_enabled", "Run weekday drift checks"),
    num("alpaca_daily_requests", "Alpaca requests / day budget", 100, 300000),
  ],
  "sec.contact": [{ path: "user_agent", label: "SEC User-Agent (name + email)", type: "text", help: "SEC EDGAR requires a descriptive User-Agent with contact email." }],
  "owner.risk_budget": [bool("enabled", "Show open risk against a hypothetical account"), num("hypothetical_account_usd", "Hypothetical account size ($)", 1000, 100_000_000)],
};

const SECTIONS: { id: string; title: string; definition: string; keys: string[] }[] = [
  { id: "market", title: "Market rules", definition: "Universe filters, live-slot allocation and scan cadence. The Pi re-reads these at each cycle.", keys: ["market.universe", "market.slots", "market.cadence"] },
  { id: "risk", title: "Signal policy and risk wording", definition: "Gates for publishing. Changing them changes what is published, not what is validated; a recalibration is required before probabilities change.", keys: ["risk.policy"] },
  { id: "discord", title: "Discord channel map", definition: "Where each kind of notice goes. Personal position updates are always private DMs and are never routed to a channel.", keys: ["discord.channels"] },
  { id: "automation", title: "Models, budgets and sources", definition: "Model poll interval, free-tier budgets and the SEC contact string.", keys: ["models.policy", "budgets", "sec.contact", "owner.risk_budget"] },
];

const INTEGRATION_STATUS: Record<string, { tone: Tone; label: string; glyph: string }> = {
  CONNECTED: { tone: "pos", label: "Connected", glyph: "●" }, NOT_CONFIGURED: { tone: "neutral", label: "Not set up", glyph: "◌" },
  STALE: { tone: "warn", label: "Saved, not tested", glyph: "▲" }, TEST_FAILED: { tone: "neg", label: "Test failed", glyph: "■" },
  DISABLED: { tone: "neutral", label: "Disabled", glyph: "○" },
};

const COMPLIANCE_TEXT: Record<string, string> = {
  ALLOW_PERSONALIZED_GROUP_GUIDANCE: "Lets member position updates use action-oriented wording (e.g. “consider trimming”) for the group. Off: informational wording only.",
  ALLOW_EXTERNAL_MARKET_DATA_REDISPLAY: "Lets the dashboard and Discord redisplay vendor market data beyond personal use. Check your data agreement first.",
  ALLOW_COMMERCIAL_HOSTING: "Records that commercial operation was reviewed. APEX ships for private, non-commercial use.",
};

export default async function SettingsPage() {
  await pageOwner();
  const ctx = await appContext();
  const now = await ctx.now();
  const env = serverEnv();
  const [settings, integrations, compliance, ops] = await Promise.all([getSettings(ctx.db), listIntegrations(ctx.db), listCompliance(ctx.db), operations(ctx.db)]);
  const h = await headers();
  const serverUrl = env.publicBaseUrl ?? `${h.get("x-forwarded-proto") ?? "http"}://${h.get("host") ?? "localhost:3000"}`;
  const devices = ops.devices as Json[];

  return (
    <>
      <PageHeader title="Settings & Secrets"
        subtitle="Credentials are encrypted at rest with a key that lives only in Vercel's environment; they are never shown again, never sent to the browser, and reach the Pi only as a device-encrypted envelope." />
      <nav aria-label="Settings sections" className="no-print flex flex-wrap gap-1.5 text-[12px]">
        {[["integrations", "Integrations"], ["pairing", "Pi pairing"], ...SECTIONS.map((s) => [s.id, s.title]), ["compliance", "Compliance switches"]].map(([id, t]) => (
          <a key={id} href={`#${id}`} className="rounded-md border border-line px-2 py-1 text-muted hover:text-fg">{t}</a>
        ))}
      </nav>
      {!env.encryptionKey ? (
        <Notice tone="warn" title="Secret storage is not configured">
          Set APEX_CONFIG_ENCRYPTION_KEY (32 random bytes, base64) in the server environment to store credentials. {ctx.isDemo ? "DEMO mode does not need any credentials." : ""}
        </Notice>
      ) : null}

      <Card id="integrations" title="Integrations" definition="All optional; APEX runs in DEMO with none. Each card says what the key is for, where to get it and whether the Pi has received the current version.">
        <div className="grid gap-4 xl:grid-cols-2">
          {integrations.map((i) => (
            <section key={i.provider} aria-label={i.label} className="rounded-lg border border-line p-3.5">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="text-[13px] font-semibold">{i.label}</h3>
                  <p className="mt-0.5 text-[12px] text-muted">{i.purpose}</p>
                </div>
<Badge tone={INTEGRATION_STATUS[i.status]?.tone ?? "warn"}>
                  <span aria-hidden="true">{INTEGRATION_STATUS[i.status]?.glyph ?? "▲"}</span>{INTEGRATION_STATUS[i.status]?.label ?? humanize(i.status)}
                </Badge>
              </div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-faint">
                <a href={i.docs} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 text-info hover:underline">Provider docs <ExternalLink aria-hidden="true" className="size-3" /></a>
                <span>tested {i.tested_at ? `${fmtAge(ageSeconds(new Date(), i.tested_at))} ago` : "never"}</span>
                {i.detail ? <span>{i.detail}</span> : null}
                {i.delivered_to_pi ? <span>Pi has secret v{i.pi_ack_version} of v{i.secret_version}{i.secret_version > i.pi_ack_version ? " (waiting for Pi)" : ""}</span> : <span>server-side only</span>}
              </div>
              <div className="mt-3"><IntegrationForm provider={i.provider} fields={i.fields} testable={i.testable} status={i.status} /></div>
            </section>
          ))}
        </div>
      </Card>

      <Card id="pairing" title="Raspberry Pi pairing" definition="One-time code plus proof that the Pi holds its own private key. Approve only a fingerprint you have compared with the Pi's output.">
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
          <PairingStarter serverUrl={serverUrl} />
          {devices.length ? (
            <Table caption="Paired devices">
              <thead><tr><Th>Device</Th><Th>Status</Th><Th>Fingerprint</Th><Th>Version</Th><Th align="right">Clock skew</Th><Th>Last seen</Th><Th /></tr></thead>
              <tbody>
                {devices.map((d) => (
                  <tr key={String(d.id)}>
                    <Td><div>{String(d.device_name)}</div><div className="num text-[11px] text-faint">{String(d.id)}</div></Td>
                    <Td><Badge tone={d.status === "active" ? "pos" : d.status === "revoked" ? "neg" : "warn"}>{String(d.status).replace(/_/g, " ")}</Badge></Td>
                    <Td className="num text-[12px]">{String(d.fingerprint ?? "—")}</Td>
                    <Td className="text-[12px] text-muted">{String(d.worker_version ?? "—")}</Td>
                    <Td align="right">{d.last_clock_skew_ms === null || d.last_clock_skew_ms === undefined ? "—" : `${(Number(d.last_clock_skew_ms) / 1000).toFixed(1)}s`}</Td>
                    <Td className="text-[12px] text-muted">{d.last_seen_at ? `${fmtAge(ageSeconds(ctx.isDemo ? now : new Date(), d.last_seen_at as string))} ago` : "never"}</Td>
                    <Td>
                      {d.status === "pending_approval" ? <ActionButton url={`/api/v1/admin/devices/${String(d.id)}`} body={{ action: "approve" }} label="Approve" variant="primary"
                        confirm={`Approve device with fingerprint ${String(d.fingerprint)}? Compare it with the Pi's output first.`} /> : null}
                      {d.status !== "revoked" ? <ActionButton url={`/api/v1/admin/devices/${String(d.id)}`} body={{ action: "revoke" }} label="Revoke" variant="danger"
                        confirm="Revoke this device? It immediately loses API access and must be re-paired." /> : null}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : <EmptyState title="No device paired" detail="Start pairing, then run the shown command on the Pi." />}
        </div>
      </Card>

      {SECTIONS.map((sec) => (
        <Card key={sec.id} id={sec.id} title={sec.title} definition={sec.definition}>
          <div className="space-y-6">
            {sec.keys.map((k) => {
              const s = settings[k as keyof typeof settings];
              return (
                <section key={k} aria-label={k}>
                  <h3 className="mb-2 flex flex-wrap items-center gap-2 text-[12px] font-semibold text-muted">
                    {k}<span className="font-normal text-faint">{s.updated_at ? `updated ${fmtEt(s.updated_at)}` : "default values"}</span>
                  </h3>
                  <SettingEditor settingKey={k} value={s.value as Record<string, unknown>} version={s.version} fields={FIELDS[k] ?? []}
                    note={k === "risk.policy" ? "gate changes do not recalibrate models" : undefined} />
                </section>
              );
            })}
          </div>
        </Card>
      ))}

      <Card id="compliance" title="Compliance switches" definition="All OFF by default. Turning one on records your acknowledgement and an external review reference in the audit log; it does not make anything compliant by itself.">
        <div className="grid gap-4 lg:grid-cols-3">
          {compliance.map((c) => (
            <section key={c.flag} aria-label={c.flag} className="rounded-lg border border-line p-3.5">
              <div className="flex items-start justify-between gap-2">
                <h3 className="text-[12px] font-semibold">{humanize(c.flag)}</h3>
                <Badge tone={c.enabled ? "warn" : "neutral"}>{c.enabled ? "ON" : "OFF"}</Badge>
              </div>
              <p className="mt-1 text-[12px] text-muted">{COMPLIANCE_TEXT[c.flag]}</p>
              {c.enabled ? <p className="mt-1 text-[11px] text-faint">Reference: {c.external_review_reference} · {fmtEt(c.acknowledged_at)}</p> : null}
              <div className="mt-3"><ComplianceSwitch flag={c.flag} enabled={c.enabled} ackPhrase={ACK_PHRASE} isDemo={ctx.isDemo} /></div>
            </section>
          ))}
        </div>
      </Card>
    </>
  );
}
