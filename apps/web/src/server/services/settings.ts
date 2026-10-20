import "server-only";
import { z } from "zod";
import type { Db } from "../db";
import { serverEnv } from "../env";
import { HttpError } from "../http/responses";
import { openField, sealField, type Keyring } from "../security/fieldCrypto";
import { audit } from "./audit";

/* ------------------------------------------------------------------------------------------------
 * Provider integrations: secrets are AES-256-GCM encrypted per field; values are NEVER returned after save.
 * ------------------------------------------------------------------------------------------------ */

export interface FieldDef {
  key: string;
  label: string;
  secret: boolean;
  pattern: RegExp;
  help: string;
  hint?: (v: string) => string;
}

export interface ProviderDef {
  provider: string;
  label: string;
  purpose: string;
  required: boolean;
  docs: string;
  fields: FieldDef[];
  deliveredToPi: boolean;
  testable: boolean;
}

export const PROVIDERS: ProviderDef[] = [
  {
    provider: "alpaca", label: "Alpaca Market Data (Basic, free)", required: false, deliveredToPi: true, testable: true,
    purpose: "IEX real-time (single venue, <=30 streamed symbols) and delayed SIP historical bars. Never used for orders.",
    docs: "https://docs.alpaca.markets/us/docs/about-market-data-api",
    fields: [
      { key: "key_id", label: "API key ID", secret: true, pattern: /^[A-Z0-9]{16,40}$/, help: "From the Alpaca dashboard (paper keys are fine).", hint: (v) => v.slice(-4) },
      { key: "secret_key", label: "API secret", secret: true, pattern: /^[A-Za-z0-9/+=]{20,100}$/, help: "Shown once by Alpaca. Never paste it into chat or Git." },
    ],
  },
  {
    provider: "fred", label: "FRED macro data (optional)", required: false, deliveredToPi: false, testable: true,
    purpose: "Release-time-aware macro series (ALFRED vintages). Disabled without a key.",
    docs: "https://fred.stlouisfed.org/docs/api/api_key.html",
    fields: [{ key: "api_key", label: "API key", secret: true, pattern: /^[a-f0-9]{32}$/, help: "32-character FRED API key." }],
  },
  {
    provider: "github", label: "GitHub Actions observability (optional)", required: false, deliveredToPi: false, testable: true,
    purpose: "Read workflow runs/minutes and dispatch the training workflow. Fine-grained token: Actions read/write on this repo only.",
    docs: "https://docs.github.com/en/rest/actions/workflows",
    fields: [
      { key: "token", label: "Fine-grained token", secret: true, pattern: /^(github_pat_|ghp_)[A-Za-z0-9_]{20,255}$/, help: "Scope it to this repository; Actions: read & write.", hint: (v) => v.slice(-4) },
      { key: "repo", label: "Repository (owner/name)", secret: false, pattern: /^[A-Za-z0-9_.-]{1,39}\/[A-Za-z0-9_.-]{1,100}$/, help: "e.g. JaredVogler2/STONKS2026" },
    ],
  },
  {
    provider: "webhook", label: "Owner alert webhook (optional)", required: false, deliveredToPi: false, testable: false,
    purpose: "Optional Discord webhook for owner-only system alerts when the Pi itself is down. HTTPS discord.com only.",
    docs: "https://support.discord.com/hc/en-us/articles/228383668",
    fields: [{ key: "url", label: "Webhook URL", secret: true, pattern: /^https:\/\/(discord\.com|discordapp\.com)\/api\/webhooks\/[0-9]{15,21}\/[A-Za-z0-9_-]{30,100}$/, help: "Only discord.com webhook URLs are accepted (SSRF protection)." }],
  },
];

export function keyring(): Keyring {
  const env = serverEnv();
  if (!env.encryptionKey) {
    throw new HttpError(503, "ENCRYPTION_KEY_MISSING", "APEX_CONFIG_ENCRYPTION_KEY (32 bytes, base64) is not configured; secrets cannot be stored");
  }
  return {
    current: { version: env.encryptionKeyVersion, key: env.encryptionKey },
    previous: env.previousEncryptionKey && env.previousEncryptionKeyVersion
      ? { version: env.previousEncryptionKeyVersion, key: env.previousEncryptionKey } : null,
  };
}

export interface IntegrationView {
  provider: string;
  label: string;
  purpose: string;
  docs: string;
  status: string;
  tested_at: string | null;
  detail: string | null;
  secret_version: number;
  pi_ack_version: number;
  delivered_to_pi: boolean;
  testable: boolean;
  fields: { key: string; label: string; secret: boolean; help: string; configured: boolean; hint: string | null }[];
}

export async function listIntegrations(db: Db): Promise<IntegrationView[]> {
  const enc = await db.query<{ provider: string; field: string; hint: string | null }>("SELECT provider, field, hint FROM apex.encrypted_integrations");
  const status = await db.query<{ provider: string; status: string; tested_at: Date | null; detail: string | null; secret_version: number; pi_ack_version: number }>(
    "SELECT provider, status, tested_at, detail, secret_version, pi_ack_version FROM apex.integration_status");
  const nonSecret = await db.query<{ key: string; value_json: Record<string, string> }>("SELECT key, value_json FROM apex.settings WHERE key LIKE 'integration.%'");
  return PROVIDERS.map((p) => {
    const st = status.find((s) => s.provider === p.provider);
    const ns = nonSecret.find((s) => s.key === `integration.${p.provider}`)?.value_json ?? {};
    return {
      provider: p.provider, label: p.label, purpose: p.purpose, docs: p.docs, status: st?.status ?? "NOT_CONFIGURED",
      tested_at: st?.tested_at ? new Date(st.tested_at).toISOString() : null, detail: st?.detail ?? null, secret_version: st?.secret_version ?? 0,
      pi_ack_version: st?.pi_ack_version ?? 0, delivered_to_pi: p.deliveredToPi, testable: p.testable,
      fields: p.fields.map((f) => {
        const e = enc.find((x) => x.provider === p.provider && x.field === f.key);
        return { key: f.key, label: f.label, secret: f.secret, help: f.help, configured: f.secret ? Boolean(e) : Boolean(ns[f.key]),
          hint: f.secret ? (e?.hint ? `…${e.hint}` : null) : (ns[f.key] ?? null) };
      }),
    };
  });
}

export async function saveIntegration(db: Db, ownerId: string, provider: string, values: Record<string, unknown>): Promise<void> {
  const def = PROVIDERS.find((p) => p.provider === provider);
  if (!def) throw new HttpError(404, "NOT_FOUND", "unknown provider");
  const ring = keyring();
  const clean: Record<string, string> = {};
  for (const f of def.fields) {
    const raw = values[f.key];
    if (raw === undefined || raw === null || raw === "") continue;
    if (typeof raw !== "string" || raw.length > 400 || !f.pattern.test(raw.trim())) {
      throw new HttpError(422, "INVALID_FIELD", `${f.label} has an invalid format`, { field: f.key }); // value never echoed
    }
    clean[f.key] = raw.trim();
  }
  if (!Object.keys(clean).length) throw new HttpError(422, "NOTHING_TO_SAVE", "no fields provided");
  await db.tx(async (sql) => {
    const st = await sql.query<{ secret_version: number }>("SELECT secret_version FROM apex.integration_status WHERE provider=$1 FOR UPDATE", [provider]);
    const version = (st[0]?.secret_version ?? 0) + 1;
    const nonSecret: Record<string, string> = {};
    for (const f of def.fields) {
      const v = clean[f.key];
      if (v === undefined) continue;
      if (!f.secret) {
        nonSecret[f.key] = v;
        continue;
      }
      const sealed = sealField(ring, provider, f.key, v);
      await sql.query(
        `INSERT INTO apex.encrypted_integrations(provider, field, ciphertext, iv, auth_tag, key_version, secret_version, hint, configured_by)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
         ON CONFLICT (provider, field) DO UPDATE SET ciphertext=EXCLUDED.ciphertext, iv=EXCLUDED.iv, auth_tag=EXCLUDED.auth_tag,
           key_version=EXCLUDED.key_version, secret_version=EXCLUDED.secret_version, hint=EXCLUDED.hint, configured_by=EXCLUDED.configured_by`,
        [provider, f.key, sealed.ciphertext, sealed.iv, sealed.authTag, sealed.keyVersion, version, f.hint ? f.hint(v) : null, ownerId],
      );
    }
    if (Object.keys(nonSecret).length) {
      await sql.query(
        `INSERT INTO apex.settings(key, value_json, updated_by) VALUES ($1, $2::jsonb, $3)
         ON CONFLICT (key) DO UPDATE SET value_json = apex.settings.value_json || EXCLUDED.value_json, version = apex.settings.version + 1,
           updated_by = EXCLUDED.updated_by, updated_at = now()`,
        [`integration.${provider}`, JSON.stringify(nonSecret), ownerId],
      );
    }
    await sql.query(
      `INSERT INTO apex.integration_status(provider, status, detail, secret_version) VALUES ($1, 'STALE', 'saved; run Test Connection', $2)
       ON CONFLICT (provider) DO UPDATE SET status='STALE', detail='saved; run Test Connection', secret_version=$2, tested_at=NULL`,
      [provider, version],
    );
    await sql.query("INSERT INTO apex.settings_history(key, redacted_change, actor_id, version) VALUES ($1, $2::jsonb, $3, $4)", [
      `integration.${provider}`, JSON.stringify({ fields_changed: Object.keys(clean), values: "[REDACTED]" }), ownerId, version]);
    await audit(sql, { actorType: "owner", actorId: ownerId, entity: "encrypted_integrations", entityId: provider, action: "integration.saved",
      metadata: { fields_changed: Object.keys(clean), secret_version: version } });
  });
}

/** Decrypt for server-side use only (connectivity tests, Pi envelopes). Fails closed on any crypto error. */
export async function readIntegrationSecrets(db: Db, provider: string): Promise<{ fields: Record<string, string>; secretVersion: number } | null> {
  const rows = await db.query<{ field: string; ciphertext: Uint8Array; iv: Uint8Array; auth_tag: Uint8Array; key_version: number; secret_version: number }>(
    "SELECT field, ciphertext, iv, auth_tag, key_version, secret_version FROM apex.encrypted_integrations WHERE provider = $1", [provider]);
  if (!rows.length) return null;
  const st = await db.query<{ status: string }>("SELECT status FROM apex.integration_status WHERE provider=$1", [provider]);
  if (st[0]?.status === "DISABLED") return null;
  const ring = keyring();
  const fields: Record<string, string> = {};
  for (const r of rows) {
    fields[r.field] = openField(ring, provider, r.field, { ciphertext: Buffer.from(r.ciphertext), iv: Buffer.from(r.iv),
      authTag: Buffer.from(r.auth_tag), keyVersion: r.key_version });
  }
  const ns = await db.query<{ value_json: Record<string, string> }>("SELECT value_json FROM apex.settings WHERE key=$1", [`integration.${provider}`]);
  return { fields: { ...(ns[0]?.value_json ?? {}), ...fields }, secretVersion: Math.max(...rows.map((r) => r.secret_version)) };
}

export type FetchLike = (url: string, init: RequestInit) => Promise<Response>;

export async function testIntegration(db: Db, ownerId: string, provider: string, fetchImpl: FetchLike = fetch): Promise<{ status: string; detail: string }> {
  const def = PROVIDERS.find((p) => p.provider === provider);
  if (!def || !def.testable) throw new HttpError(404, "NOT_TESTABLE", "this integration has no server-side test");
  const recent = await db.query<{ tested_at: Date | null }>("SELECT tested_at FROM apex.integration_status WHERE provider=$1", [provider]);
  const last = recent[0]?.tested_at ? new Date(recent[0].tested_at).getTime() : 0;
  if (Date.now() - last < 30_000) throw new HttpError(429, "TEST_RATE_LIMITED", "wait 30 seconds between connection tests");
  let secret: Awaited<ReturnType<typeof readIntegrationSecrets>>;
  try {
    secret = await readIntegrationSecrets(db, provider);
  } catch {
    await setStatus(db, provider, "TEST_FAILED", "stored secret could not be decrypted (key version mismatch or corruption)");
    return { status: "TEST_FAILED", detail: "stored secret could not be decrypted" };
  }
  if (!secret) return { status: "NOT_CONFIGURED", detail: "save credentials first" };
  let status = "TEST_FAILED";
  let detail = "unexpected response";
  const signal = AbortSignal.timeout(8000);
  try {
    let res: Response;
    if (provider === "alpaca") {
      res = await fetchImpl("https://data.alpaca.markets/v2/stocks/bars/latest?symbols=SPY&feed=iex", {
        headers: { "APCA-API-KEY-ID": secret.fields.key_id ?? "", "APCA-API-SECRET-KEY": secret.fields.secret_key ?? "" }, signal });
    } else if (provider === "fred") {
      res = await fetchImpl(`https://api.stlouisfed.org/fred/series?series_id=CPIAUCSL&file_type=json&api_key=${encodeURIComponent(secret.fields.api_key ?? "")}`, { signal });
    } else {
      const repo = secret.fields.repo ?? "";
      if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo)) return { status: "TEST_FAILED", detail: "repository not set" };
      res = await fetchImpl(`https://api.github.com/repos/${repo}/actions/workflows`, {
        headers: { Authorization: `Bearer ${secret.fields.token ?? ""}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" }, signal });
    }
    if (res.ok) {
      status = "CONNECTED";
      detail = provider === "alpaca" ? "authenticated; IEX latest bar returned (single-venue data)" : "authenticated";
    } else if (res.status === 429) {
      status = "RATE_LIMITED";
      detail = "provider rate limit reached; retry later";
    } else if (res.status === 401 || res.status === 403) {
      status = "TEST_FAILED";
      detail = `provider rejected the credentials (HTTP ${res.status})`;
    } else {
      detail = `provider returned HTTP ${res.status}`;
    }
  } catch (e) {
    detail = e instanceof Error && e.name === "TimeoutError" ? "timed out after 8 s" : "network error";
  }
  await setStatus(db, provider, status, detail);
  await audit(db, { actorType: "owner", actorId: ownerId, entity: "integration_status", entityId: provider, action: "integration.tested", metadata: { status } });
  return { status, detail };
}

async function setStatus(db: Db, provider: string, status: string, detail: string): Promise<void> {
  await db.query(
    `INSERT INTO apex.integration_status(provider, status, detail, tested_at) VALUES ($1,$2,$3,now())
     ON CONFLICT (provider) DO UPDATE SET status=$2, detail=$3, tested_at=now()`,
    [provider, status, detail.slice(0, 240)],
  );
}

export async function disableIntegration(db: Db, ownerId: string, provider: string, remove: boolean): Promise<void> {
  if (!PROVIDERS.some((p) => p.provider === provider)) throw new HttpError(404, "NOT_FOUND", "unknown provider");
  await db.tx(async (sql) => {
    if (remove) {
      await sql.query("DELETE FROM apex.encrypted_integrations WHERE provider=$1", [provider]);
      await sql.query("DELETE FROM apex.settings WHERE key=$1", [`integration.${provider}`]);
    }
    await sql.query(
      `INSERT INTO apex.integration_status(provider, status, detail) VALUES ($1,$2,$3)
       ON CONFLICT (provider) DO UPDATE SET status=$2, detail=$3`,
      [provider, remove ? "NOT_CONFIGURED" : "DISABLED", remove ? "deleted by owner" : "disabled by owner"],
    );
    await audit(sql, { actorType: "owner", actorId: ownerId, entity: "encrypted_integrations", entityId: provider, action: remove ? "integration.deleted" : "integration.disabled" });
  });
}

/* ------------------------------------------------------------------------------------------------
 * Non-secret application settings (validated, versioned, audited)
 * ------------------------------------------------------------------------------------------------ */

const symbolList = z.array(z.string().regex(/^[A-Z][A-Z0-9.]{0,9}$/)).max(500);
const snowflake = z.string().regex(/^[1-9][0-9]{14,20}$/);

export const SETTING_SCHEMAS = {
  "market.universe": z.object({
    min_price: z.number().min(1).max(1000), min_median_dollar_volume: z.number().min(1e6).max(1e11), min_avg_volume: z.number().min(1e4).max(1e9),
    min_history_sessions: z.number().int().min(60).max(2000), include_etfs: z.boolean(), exclude_leveraged: z.boolean(),
    include: symbolList, exclude: symbolList, user_verified: symbolList,
  }),
  "market.slots": z.object({ max_slots: z.number().int().min(1).max(30), reserve_for_positions: z.number().int().min(0).max(30) }),
  "market.cadence": z.object({ intraday_minutes: z.union([z.literal(15), z.literal(30)]), nightly_refresh_et: z.string().regex(/^([01][0-9]|2[0-3]):[0-5][0-9]$/) }),
  "risk.policy": z.object({
    min_quality: z.number().min(50).max(100), max_new_per_session: z.number().int().min(0).max(10), max_open_per_sector: z.number().int().min(1).max(10),
    research_setups_enabled: z.boolean(), research_min_quality: z.number().min(50).max(100), entry_valid_sessions: z.number().int().min(1).max(5),
    hypothetical_risk_per_trade_pct: z.number().min(0.1).max(2), portfolio_open_risk_cap_pct: z.number().min(0.5).max(10),
  }),
  "discord.channels": z.object({
    guild_id: snowflake.nullable(),
    channels: z.object({ high_conviction_alerts: snowflake.nullable(), active_trades: snowflake.nullable(), exit_alerts: snowflake.nullable(),
      market_intelligence: snowflake.nullable(), performance_reports: snowflake.nullable(), system_status: snowflake.nullable() }),
    high_priority_role_id: snowflake.nullable(),
  }),
  "models.policy": z.object({ promotion_mode: z.enum(["manual", "auto"]), auto_requires_status: z.enum(["CALIBRATED"]), poll_minutes: z.number().int().min(15).max(120) }),
  "budgets": z.object({ actions_monthly_minutes: z.number().int().min(0).max(50_000), train_timeout_minutes: z.number().int().min(5).max(120),
    drift_enabled: z.boolean(), alpaca_daily_requests: z.number().int().min(100).max(300_000) }),
  "sec.contact": z.object({ user_agent: z.string().regex(/^[\x20-\x7e]{8,120}$/).refine((s) => /@/.test(s), "must include a contact email") }),
} as const;

export type SettingKey = keyof typeof SETTING_SCHEMAS;

export const SETTING_DEFAULTS: { [K in SettingKey]: z.infer<(typeof SETTING_SCHEMAS)[K]> } = {
  "market.universe": { min_price: 10, min_median_dollar_volume: 30e6, min_avg_volume: 1e6, min_history_sessions: 180, include_etfs: true,
    exclude_leveraged: true, include: [], exclude: [], user_verified: [] },
  "market.slots": { max_slots: 25, reserve_for_positions: 10 },
  "market.cadence": { intraday_minutes: 15, nightly_refresh_et: "19:30" },
  "risk.policy": { min_quality: 70, max_new_per_session: 3, max_open_per_sector: 2, research_setups_enabled: true, research_min_quality: 75,
    entry_valid_sessions: 2, hypothetical_risk_per_trade_pct: 0.5, portfolio_open_risk_cap_pct: 3 },
  "discord.channels": { guild_id: null, channels: { high_conviction_alerts: null, active_trades: null, exit_alerts: null, market_intelligence: null,
    performance_reports: null, system_status: null }, high_priority_role_id: null },
  "models.policy": { promotion_mode: "manual", auto_requires_status: "CALIBRATED", poll_minutes: 45 },
  "budgets": { actions_monthly_minutes: 1500, train_timeout_minutes: 45, drift_enabled: true, alpaca_daily_requests: 150_000 },
  "sec.contact": { user_agent: "APEX research owner@example.com" },
};

export async function getSettings(db: Db): Promise<{ [K in SettingKey]: { value: z.infer<(typeof SETTING_SCHEMAS)[K]>; version: number; updated_at: string | null } }> {
  const rows = await db.query<{ key: string; value_json: unknown; version: number; updated_at: Date }>(
    "SELECT key, value_json, version, updated_at FROM apex.settings WHERE key = ANY($1)", [Object.keys(SETTING_SCHEMAS)]);
  const out = {} as Record<string, { value: unknown; version: number; updated_at: string | null }>;
  for (const key of Object.keys(SETTING_SCHEMAS) as SettingKey[]) {
    const r = rows.find((x) => x.key === key);
    const parsed = r ? SETTING_SCHEMAS[key].safeParse(r.value_json) : null;
    out[key] = { value: parsed?.success ? parsed.data : SETTING_DEFAULTS[key], version: r?.version ?? 0, updated_at: r ? new Date(r.updated_at).toISOString() : null };
  }
  return out as never;
}

export async function putSetting(db: Db, ownerId: string, key: string, value: unknown, expectedVersion?: number): Promise<number> {
  if (!(key in SETTING_SCHEMAS)) throw new HttpError(404, "NOT_FOUND", "unknown setting");
  const parsed = SETTING_SCHEMAS[key as SettingKey].safeParse(value);
  if (!parsed.success) {
    throw new HttpError(422, "INVALID_SETTING", "setting failed validation", { issues: parsed.error.issues.slice(0, 6).map((i) => `${i.path.join(".")}: ${i.message}`) });
  }
  if (key === "market.slots") {
    const v = parsed.data as { max_slots: number; reserve_for_positions: number };
    if (v.reserve_for_positions > v.max_slots) throw new HttpError(422, "INVALID_SETTING", "reserved slots cannot exceed max slots");
  }
  return db.tx(async (sql) => {
    const cur = await sql.query<{ version: number; value_json: unknown }>("SELECT version, value_json FROM apex.settings WHERE key=$1 FOR UPDATE", [key]);
    if (expectedVersion !== undefined && (cur[0]?.version ?? 0) !== expectedVersion) {
      throw new HttpError(409, "VERSION_CONFLICT", "settings changed since you loaded them; reload and retry");
    }
    const version = (cur[0]?.version ?? 0) + 1;
    await sql.query(
      `INSERT INTO apex.settings(key, value_json, version, updated_by) VALUES ($1,$2::jsonb,$3,$4)
       ON CONFLICT (key) DO UPDATE SET value_json=EXCLUDED.value_json, version=EXCLUDED.version, updated_by=EXCLUDED.updated_by, updated_at=now()`,
      [key, JSON.stringify(parsed.data), version, ownerId],
    );
    await sql.query("INSERT INTO apex.settings_history(key, redacted_change, actor_id, version) VALUES ($1,$2::jsonb,$3,$4)", [
      key, JSON.stringify({ before: cur[0]?.value_json ?? null, after: parsed.data }), ownerId, version]);
    await audit(sql, { actorType: "owner", actorId: ownerId, entity: "settings", entityId: key, action: "settings.updated", metadata: { version } });
    return version;
  });
}

/* ------------------------------------------------------------------------------------------------
 * Compliance release switches: OFF by default; enabling requires an acknowledgement AND an external
 * review reference. Toggling a flag does not make anything legally compliant; it records a decision.
 * ------------------------------------------------------------------------------------------------ */

export const COMPLIANCE_FLAGS = ["ALLOW_EXTERNAL_MARKET_DATA_REDISPLAY", "ALLOW_PERSONALIZED_GROUP_GUIDANCE", "ALLOW_COMMERCIAL_HOSTING"] as const;
export const ACK_PHRASE = "I have completed the required external review";

export async function listCompliance(db: Db) {
  return db.query<{ flag: string; enabled: boolean; acknowledged_at: Date | null; external_review_reference: string | null; updated_at: Date }>(
    "SELECT flag, enabled, acknowledged_at, external_review_reference, updated_at FROM apex.compliance_flags ORDER BY flag");
}

export async function setCompliance(db: Db, ownerId: string, flag: string, enabled: boolean, ack: string | null, reference: string | null): Promise<void> {
  if (!(COMPLIANCE_FLAGS as readonly string[]).includes(flag)) throw new HttpError(404, "NOT_FOUND", "unknown flag");
  if (enabled) {
    if (serverEnv().mode === "DEMO") throw new HttpError(409, "DEMO_MODE", "compliance switches cannot be enabled in DEMO mode");
    if (ack !== ACK_PHRASE) throw new HttpError(422, "ACK_REQUIRED", `type exactly: "${ACK_PHRASE}"`);
    if (!reference || reference.trim().length < 8) throw new HttpError(422, "REFERENCE_REQUIRED", "an external review reference (>= 8 chars) is required");
  }
  await db.tx(async (sql) => {
    await sql.query(
      `UPDATE apex.compliance_flags SET enabled=$2, acknowledged_by=$3, acknowledged_at=CASE WHEN $2 THEN now() ELSE acknowledged_at END,
         acknowledgement_text=$4, external_review_reference=$5, updated_at=now() WHERE flag=$1`,
      [flag, enabled, enabled ? ownerId : null, enabled ? ack : null, enabled ? reference!.trim().slice(0, 200) : null],
    );
    await audit(sql, { actorType: "owner", actorId: ownerId, entity: "compliance_flags", entityId: flag, action: enabled ? "compliance.enabled" : "compliance.disabled" });
  });
}

export async function complianceMap(db: Db): Promise<Record<string, boolean>> {
  const rows = await listCompliance(db);
  return Object.fromEntries(rows.map((r) => [r.flag, r.enabled]));
}
