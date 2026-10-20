#!/usr/bin/env node
/*
 * Free-tier headroom estimate for a configurable deployment size. Prints Markdown.
 *
 *   node scripts/cost-report.mjs [--symbols 25] [--universe 300] [--members 10] [--signals 3] [--positions 20] [--dashboard-views 60]
 *
 * Every provider limit below is an ASSUMPTION to verify against the provider's current pricing page (links printed).
 * Usage figures are modelled from APEX's own schedules; replace them with measured values from Operations once live.
 */
const arg = (name, dflt) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? Number(process.argv[i + 1]) : dflt;
};
const p = {
  symbols: arg("symbols", 25), // live IEX stream slots
  universe: arg("universe", 300), // daily-bar universe scanned at the close
  members: arg("members", 10),
  signals: arg("signals", 3), // published signals per session (policy caps at 3)
  positions: arg("positions", 20), // open self-reported positions across members
  views: arg("dashboard-views", 60), // owner page views per day
};
const SESSIONS_PER_MONTH = 21;
const DAYS_PER_MONTH = 30.4;

// ---- Pi -> Vercel agent API (per day) ----
const intradayChecks = (6.5 * 60) / 15; // 15-minute cadence during the regular session
const agentCalls = {
  heartbeat: 24 * 60, // every 60 s
  outbox_poll: 24 * 60 * 2, // /work lease every 30 s (bridge to the Discord bot)
  config_poll: 24 * 4, // every 15 min
  model_manifest_poll: (24 * 60) / 45,
  observations_upload: intradayChecks + 2,
  scan_run: 1,
  signals_and_events: p.signals * 4,
  assessments: Math.ceil(p.positions / 50) * intradayChecks,
  catalysts: 24, // hourly batch
};
const agentPerDay = Object.values(agentCalls).reduce((a, b) => a + b, 0);
const dashboardPerDay = p.views * 1.3; // page + occasional API call
const vercelPerMonth = (agentPerDay + dashboardPerDay) * DAYS_PER_MONTH;

// ---- Alpaca (REST requests per session; the IEX websocket is one connection) ----
const alpacaPerSession = Math.ceil(p.universe / 200) * 2 + Math.ceil(p.symbols / 100) * intradayChecks + 10;

// ---- Supabase storage growth (bytes) ----
const KB = 1024;
const perSession = {
  signals: p.signals * 16 * KB + p.signals * 6 * 1.2 * KB, // immutable snapshot + lifecycle events
  scan_and_candidates: 6 * KB + 12 * 3 * KB,
  feature_snapshots: 12 * 3.5 * KB,
  daily_bars: (p.symbols + p.positions) * 0.25 * KB,
  outbox: p.signals * 5 * 6 * KB + p.positions * intradayChecks * 0.15 * 1.5 * KB, // channel renders + private updates (sparse)
  position_snapshots: p.positions * 2 * 2 * KB,
  audit_and_runs: 8 * KB,
};
const growthPerMonth = Object.values(perSession).reduce((a, b) => a + b, 0) * SESSIONS_PER_MONTH;
const steadyState = 1440 * 7 * 1.6 * KB /* heartbeats, 7-day retention */ + 14 * 400 * 0.6 * KB /* quote events, 14-day retention */ + 12 * 1024 * KB /* schema + indexes */;
const dbLimit = 500 * 1024 * KB;
const monthsTo350 = (350 * 1024 * KB - steadyState) / growthPerMonth;

// ---- Discord ----
const discordMsgsPerDay = p.signals * 4 + p.positions * 1.5 + 2;

// ---- GitHub Actions (minutes / month) ----
const trainTimeout = 45;
const driftTimeout = 10;
const actionsMax = trainTimeout * 4.35 + driftTimeout * SESSIONS_PER_MONTH;

const fmt = (n, d = 0) => Number(n).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
const pct = (a, b) => `${fmt((a / b) * 100, 1)}%`;

const rows = [
  ["Vercel Hobby function invocations", `${fmt(vercelPerMonth)} / month`, "1,000,000 / month (ASSUMED)", pct(vercelPerMonth, 1_000_000), "https://vercel.com/docs/plans/hobby"],
  ["Alpaca Basic REST requests", `${fmt(alpacaPerSession)} / session (peak ≈ ${fmt(Math.ceil(p.universe / 200) * 2 + 5)} / minute)`, "200 / minute", pct(Math.ceil(p.universe / 200) * 2 + 5, 200), "https://docs.alpaca.markets/us/docs/about-market-data-api"],
  ["Alpaca IEX streamed symbols", `${p.symbols}`, "30", pct(p.symbols, 30), "https://docs.alpaca.markets/us/docs/about-market-data-api"],
  ["Supabase database size", `≈ ${fmt(steadyState / 1024 / 1024, 1)} MB steady + ${fmt(growthPerMonth / 1024 / 1024, 1)} MB / month`, "500 MB (warn 350, stop 450)", `${fmt(monthsTo350, 0)} months to 350 MB`, "https://supabase.com/docs/guides/platform/billing-on-supabase"],
  ["Discord messages", `${fmt(discordMsgsPerDay)} / day`, "per-route rate limits (bot honours 429 retry-after)", "n/a", "https://discord.com/developers/docs/topics/rate-limits"],
  ["GitHub Actions minutes (max possible)", `${fmt(actionsMax)} / month`, "2,000 / month on Free, shared by ALL repos", pct(actionsMax, 2000), "https://docs.github.com/en/billing/concepts/product-billing/github-actions"],
  ["SEC EDGAR", "≤ 2 requests / second (polite pacing)", "10 / second fair-access ceiling", "≤ 20%", "https://www.sec.gov/os/accessing-edgar-data"],
];

console.log(`# APEX free-tier headroom estimate

Inputs: ${p.symbols} streamed symbols, ${p.universe}-symbol daily universe, ${p.members} members, ${p.signals} signals/session, ${p.positions} open positions, ${p.views} owner page views/day.

| Resource | Modelled usage | Limit (verify) | Share of limit | Source |
|---|---|---|---|---|
${rows.map((r) => `| ${r.join(" | ")} |`).join("\n")}

Agent API calls per day (Pi → Vercel): ${Object.entries(agentCalls).map(([k, v]) => `${k} ${fmt(v)}`).join(", ")}; total ≈ ${fmt(agentPerDay)}.

Network egress (Pi): ≈ ${fmt((agentPerDay * 2.5 + alpacaPerSession * 40) / 1024, 1)} MB/day, far below any home connection limit.

Notes
- Limits marked ASSUMED or "verify" change over time; check the linked page before relying on them. Nothing in APEX upgrades a plan automatically.
- GitHub Actions minutes are shared with every private repository and workflow on the account; the weekly job skips optional work when the configured budget is low.
- Supabase Free pauses after inactivity; the Pi keeps its spool and the dashboard reports the outage. Resume the project from the Supabase dashboard.
- Market data shown to other Discord members can be subject to redistribution terms even when the API itself is free (compliance switch ALLOW_EXTERNAL_MARKET_DATA_REDISPLAY stays OFF by default).
`);
