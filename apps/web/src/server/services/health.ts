import "server-only";
import type { Db } from "../db";
import { migrationStatus } from "../db/migrate";
import { serverEnv, type ReleaseMode } from "../env";

/* Separate health lights; one green light is never sufficient. Every component says what to do next. */

export type HealthStatus = "OK" | "DEGRADED" | "DOWN" | "STALE" | "DISABLED" | "UNCONFIGURED" | "UNKNOWN";

export interface HealthComponent {
  key: string;
  label: string;
  status: HealthStatus;
  last_ok_at?: string;
  age_seconds: number | null;
  detail?: string;
  action?: string;
}

export interface ServiceHealth {
  generated_at: string;
  overall: HealthStatus;
  release_mode: ReleaseMode;
  components: HealthComponent[];
}

export const DB_WARN_BYTES = 350 * 1024 * 1024;
export const DB_STOP_BYTES = 450 * 1024 * 1024;
const HEARTBEAT_LATE_S = 3 * 60;
const HEARTBEAT_DOWN_S = 10 * 60;
const TRAIN_SLA_DAYS = 8;

const iso = (d: Date) => d.toISOString().replace(/\.\d{3}Z$/, "Z");

function age(now: Date, then: Date | string | null | undefined): number | null {
  if (!then) return null;
  return Math.max(0, Math.floor((now.getTime() - new Date(then).getTime()) / 1000));
}

export async function computeHealth(db: Db, now: Date): Promise<ServiceHealth> {
  const env = serverEnv();
  const isDemo = env.mode === "DEMO";
  const comps: HealthComponent[] = [];

  const hb = await db.query<{ agent_id: string; received_at: Date; payload_json: Record<string, unknown>; clock_skew_ms: number }>(
    `SELECT DISTINCT ON (agent_id) agent_id, received_at, payload_json, clock_skew_ms FROM apex.agent_heartbeats
     ORDER BY agent_id, received_at DESC LIMIT 5`);
  const devices = await db.query<{ id: string; status: string }>("SELECT id, status FROM apex.agent_devices");
  const latest = hb.sort((a, b) => new Date(b.received_at).getTime() - new Date(a.received_at).getTime())[0];
  const hbAge = age(isDemo ? now : new Date(), latest?.received_at);
  if (!devices.length) {
    comps.push({ key: "pi_agent", label: "Raspberry Pi worker", status: "UNCONFIGURED", age_seconds: null, detail: "no paired device",
      action: "Settings -> Pi pairing: start pairing, then run `apex pair CODE` on the Pi" });
  } else {
    const st: HealthStatus = hbAge === null ? "DOWN" : hbAge > HEARTBEAT_DOWN_S ? "DOWN" : hbAge > HEARTBEAT_LATE_S ? "DEGRADED" : "OK";
    const skew = latest ? Math.abs(latest.clock_skew_ms) : 0;
    comps.push({ key: "pi_agent", label: "Raspberry Pi worker", status: skew > 30_000 && st === "OK" ? "DEGRADED" : st,
      last_ok_at: latest ? iso(new Date(latest.received_at)) : undefined, age_seconds: hbAge,
      detail: latest ? `last heartbeat ${hbAge}s ago; clock skew ${Math.round(skew / 1000)}s` : "no heartbeat received",
      action: st === "OK" ? undefined : "Check Pi power/network; `sudo systemctl status apex-worker apex-discord-bot`; run `apex doctor`" });
  }
  const comp = (name: string) => ((latest?.payload_json?.components as Record<string, { status?: string; detail?: string; last_ok_at?: string; metrics?: Record<string, unknown> }>) ?? {})[name];
  const discord = comp("discord_gateway");
  comps.push({ key: "discord_gateway", label: "Discord gateway", status: (discord?.status as HealthStatus) ?? "UNKNOWN", age_seconds: age(now, discord?.last_ok_at),
    detail: discord?.detail ?? "reported by the Pi heartbeat", action: discord?.status === "OK" ? undefined : "Check DISCORD_BOT_TOKEN on the Pi and bot logs" });
  const feed = comp("market_feed");
  const quotes = await db.query<{ n: number; newest: Date | null; feed: string | null }>(
    "SELECT count(*)::int AS n, max(ts_received) AS newest, max(feed) AS feed FROM apex.latest_quotes");
  const qAge = age(now, quotes[0]?.newest);
  comps.push({ key: "market_feed", label: isDemo ? "Market feed (DEMO replay)" : "Market feed",
    status: (feed?.status as HealthStatus) ?? (quotes[0]?.n ? "STALE" : "UNKNOWN"), age_seconds: qAge,
    detail: isDemo ? "synthetic replay fixture - not market data" : `${quotes[0]?.n ?? 0} symbols cached; newest quote ${qAge ?? "n/a"}s old (${quotes[0]?.feed ?? "?"})`,
    action: feed?.status === "OK" ? undefined : "Configure Alpaca in Settings and confirm the Pi received it (secret version ack)" });
  const scan = await db.query<{ finished_at: Date | null; asof_at: Date }>("SELECT finished_at, asof_at FROM apex.scan_runs ORDER BY asof_at DESC LIMIT 1");
  const scanAge = age(now, scan[0]?.finished_at ?? scan[0]?.asof_at);
  comps.push({ key: "scanner", label: "Signal scanner", status: !scan[0] ? "UNKNOWN" : scanAge! > 4 * 86400 ? "STALE" : "OK", age_seconds: scanAge,
    last_ok_at: scan[0] ? iso(new Date(scan[0].finished_at ?? scan[0].asof_at)) : undefined,
    detail: scan[0] ? `last scan as of ${iso(new Date(scan[0].asof_at))}` : "no scan recorded yet", action: scan[0] ? undefined : "Start the Pi worker" });
  const size = await db.query<{ bytes: string }>("SELECT apex.database_size_bytes()::text AS bytes");
  const bytes = Number(size[0]?.bytes ?? 0);
  const ms = await migrationStatus(db);
  comps.push({ key: "database", label: db.kind === "pglite" ? "Database (embedded DEMO)" : "Database (Supabase Postgres)",
    status: ms.pending.length || ms.checksumMismatch.length ? "DOWN" : bytes > DB_STOP_BYTES ? "DOWN" : bytes > DB_WARN_BYTES ? "DEGRADED" : "OK",
    age_seconds: 0, detail: `${(bytes / 1024 / 1024).toFixed(1)} MB used (warn 350 MB, stop 450 MB of 500 MB free tier); ${ms.pending.length} pending migrations`,
    action: ms.pending.length ? "Run `pnpm --filter @apex/web db:migrate` with DATABASE_URL" : bytes > DB_WARN_BYTES ? "Run retention (Operations -> Retention)" : undefined });
  const ob = await db.query<{ pending: number; dead: number; deferred: number; oldest: Date | null }>(
    `SELECT count(*) FILTER (WHERE state IN ('pending','leased'))::int AS pending, count(*) FILTER (WHERE state='dead_letter')::int AS dead,
            count(*) FILTER (WHERE state='deferred')::int AS deferred, min(created_at) FILTER (WHERE state IN ('pending','leased')) AS oldest
     FROM apex.notification_outbox`);
  const o = ob[0]!;
  const oldest = age(now, o.oldest);
  comps.push({ key: "alerts_delivery", label: "Alert delivery (outbox)", status: o.dead > 0 ? "DEGRADED" : oldest !== null && oldest > 1800 ? "DEGRADED" : "OK",
    age_seconds: oldest, detail: `${o.pending} pending, ${o.deferred} held privately (DMs off), ${o.dead} dead-letter`,
    action: o.dead > 0 ? "Operations -> Delivery: inspect dead letters and requeue" : undefined });
  const champ = await db.query<{ id: string; validation_expires_at: Date; confidence: string | null }>(
    `SELECT id, validation_expires_at, manifest_json->'evaluation'->>'confidence_status' AS confidence FROM apex.model_versions
     WHERE status='champion' AND is_demo=$1 ORDER BY promoted_at DESC LIMIT 1`, [isDemo]);
  const runs = await db.query<{ finished_at: Date | null; started_at: Date; status: string }>(
    "SELECT finished_at, started_at, status FROM apex.training_runs WHERE workflow IN ('train_weekly','local_replay') ORDER BY started_at DESC LIMIT 1");
  const trainAge = age(now, runs[0]?.finished_at ?? runs[0]?.started_at);
  const expired = champ[0] && new Date(champ[0].validation_expires_at).getTime() < now.getTime();
  const calibrated = champ[0]?.confidence === "CALIBRATED";
  comps.push({ key: "model", label: "Calibrated model", status: !champ[0] ? "UNCONFIGURED" : expired ? "STALE" : calibrated ? "OK" : "DEGRADED", age_seconds: trainAge,
    detail: !champ[0] ? "no champion: every signal is UNVALIDATED (no probabilities shown)"
      : `champion ${champ[0].id} (${(champ[0].confidence ?? "unknown").toLowerCase()})${expired ? " - validation expired" : ""}${!calibrated ? ": no calibrated probabilities" : ""}`,
    action: !champ[0] ? "Training runs weekly on GitHub Actions; a model is promoted only if it passes the frozen gates" : expired ? "Investigate missed training runs" : undefined });
  comps.push({ key: "training_schedule", label: "Weekly cloud training", status: !runs[0] ? "UNKNOWN" : trainAge! > TRAIN_SLA_DAYS * 86400 * 2 ? "DOWN" : trainAge! > TRAIN_SLA_DAYS * 86400 ? "DEGRADED" : "OK",
    age_seconds: trainAge, detail: runs[0] ? `last run ${runs[0].status}` : "no training run reported (GitHub schedule not yet configured or not run)",
    action: runs[0] && trainAge! > TRAIN_SLA_DAYS * 86400 ? "Missed-run: check GitHub Actions schedule, minutes budget and the release secret" : undefined });
  const integ = await db.query<{ provider: string; status: string }>("SELECT provider, status FROM apex.integration_status");
  const alp = integ.find((i) => i.provider === "alpaca");
  comps.push({ key: "integration_alpaca", label: "Alpaca credentials", status: isDemo ? "DISABLED" : (alp?.status === "CONNECTED" ? "OK" : alp ? "DEGRADED" : "UNCONFIGURED"),
    age_seconds: null, detail: isDemo ? "not used in DEMO mode" : alp?.status ?? "not configured", action: isDemo ? undefined : "Settings -> Integrations -> Alpaca" });
  const flags = await db.query<{ enabled: boolean }>("SELECT enabled FROM apex.compliance_flags WHERE flag='ALLOW_PERSONALIZED_GROUP_GUIDANCE'");
  comps.push({ key: "compliance", label: "Group personalized guidance", status: flags[0]?.enabled ? "OK" : "DISABLED", age_seconds: null,
    detail: flags[0]?.enabled ? "enabled after recorded external review" : "OFF (default): member updates use informational wording",
    action: undefined });
  comps.push({ key: "vercel_protection", label: "Vercel Authentication", status: env.onVercel ? "UNKNOWN" : "DISABLED", age_seconds: null,
    detail: env.onVercel ? "cannot be verified from inside the app; run scripts/smoke-deployment.mjs from outside" : "local process (not deployed)",
    action: env.onVercel ? "Vercel -> Settings -> Deployment Protection -> Vercel Authentication: All Deployments" : undefined });

  const order: HealthStatus[] = ["DOWN", "STALE", "DEGRADED", "UNKNOWN", "OK"];
  const core = comps.filter((c) => ["pi_agent", "market_feed", "database", "alerts_delivery", "scanner"].includes(c.key));
  const overall = order.find((s) => core.some((c) => c.status === s)) ?? "OK";
  return { generated_at: iso(now), overall, release_mode: env.mode, components: comps };
}
