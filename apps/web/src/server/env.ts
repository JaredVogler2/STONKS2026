import "server-only";
import { randomBytes } from "node:crypto";

export type ReleaseMode = "DEMO" | "PAPER_LIVE_DATA" | "PERSONAL_RESEARCH" | "GROUP_POSITION_ALERTS";
const MODES: ReleaseMode[] = ["DEMO", "PAPER_LIVE_DATA", "PERSONAL_RESEARCH", "GROUP_POSITION_ALERTS"];

export class ConfigError extends Error {
  constructor(
    public readonly key: string,
    message: string,
  ) {
    super(message);
  }
}

function decodeKey(name: string, raw: string | undefined, minBytes: number, exactBytes?: number): Buffer | null {
  if (!raw) return null;
  let buf: Buffer;
  try {
    buf = Buffer.from(raw.trim(), "base64");
  } catch {
    throw new ConfigError(name, `${name} must be base64`);
  }
  if (exactBytes !== undefined && buf.length !== exactBytes) {
    throw new ConfigError(name, `${name} must decode to exactly ${exactBytes} bytes (got ${buf.length})`);
  }
  if (buf.length < minBytes) throw new ConfigError(name, `${name} must decode to at least ${minBytes} bytes`);
  return buf;
}

export interface ServerEnv {
  mode: ReleaseMode;
  onVercel: boolean;
  vercelEnv: string | null;
  databaseUrl: string | null;
  pgliteDir: string | null;
  encryptionKey: Buffer | null;
  encryptionKeyVersion: number;
  previousEncryptionKey: Buffer | null;
  previousEncryptionKeyVersion: number | null;
  sessionSecret: Buffer;
  sessionSecretIsEphemeral: boolean;
  agentHmacSecrets: Buffer[];
  ownerDiscordIds: string[];
  discordOAuth: { clientId: string; clientSecret: string } | null;
  publicBaseUrl: string | null;
  allowLocalDevLogin: boolean;
  demoAutoSeed: boolean;
}

let cached: ServerEnv | null = null;
// Local DEMO only (no APEX_SESSION_SECRET): one random secret per process. Kept on globalThis because Next.js may
// instantiate this module separately for route handlers and server components.
const g = globalThis as unknown as { __apexEphemeralSession?: Buffer };
const ephemeralSession = (g.__apexEphemeralSession ??= randomBytes(32));

export function serverEnv(): ServerEnv {
  if (cached) return cached;
  const e = process.env;
  const modeRaw = (e.APEX_MODE ?? "DEMO").trim().toUpperCase();
  if (!MODES.includes(modeRaw as ReleaseMode)) throw new ConfigError("APEX_MODE", `APEX_MODE must be one of ${MODES.join(", ")}`);
  const mode = modeRaw as ReleaseMode;
  const onVercel = Boolean(e.VERCEL);
  const databaseUrl = e.DATABASE_URL?.trim() || null;
  if (mode !== "DEMO" && !databaseUrl) {
    throw new ConfigError("DATABASE_URL", "Non-DEMO modes require DATABASE_URL (Supabase Postgres). Embedded storage is DEMO-only.");
  }
  const session = decodeKey("APEX_SESSION_SECRET", e.APEX_SESSION_SECRET, 32);
  if (!session && (mode !== "DEMO" || onVercel)) {
    throw new ConfigError("APEX_SESSION_SECRET", "APEX_SESSION_SECRET (base64, >=32 bytes) is required outside local DEMO.");
  }
  const hmac = [decodeKey("AGENT_HMAC_SECRET", e.AGENT_HMAC_SECRET, 32), decodeKey("AGENT_HMAC_SECRET_PREVIOUS", e.AGENT_HMAC_SECRET_PREVIOUS, 32)]
    .filter((b): b is Buffer => b !== null);
  const discordOAuth =
    e.DISCORD_OAUTH_CLIENT_ID && e.DISCORD_OAUTH_CLIENT_SECRET
      ? { clientId: e.DISCORD_OAUTH_CLIENT_ID.trim(), clientSecret: e.DISCORD_OAUTH_CLIENT_SECRET.trim() }
      : null;
  const owners = (e.APEX_OWNER_DISCORD_IDS ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter((s) => /^[1-9][0-9]{14,20}$/.test(s));
  cached = {
    mode,
    onVercel,
    vercelEnv: e.VERCEL_ENV ?? null,
    databaseUrl,
    pgliteDir: databaseUrl ? null : e.APEX_PGLITE_DIR?.trim() || (onVercel ? null : ".apex-local/pglite"),
    encryptionKey: decodeKey("APEX_CONFIG_ENCRYPTION_KEY", e.APEX_CONFIG_ENCRYPTION_KEY, 32, 32),
    encryptionKeyVersion: Number.parseInt(e.APEX_CONFIG_ENCRYPTION_KEY_VERSION ?? "1", 10) || 1,
    previousEncryptionKey: decodeKey("APEX_CONFIG_ENCRYPTION_KEY_PREVIOUS", e.APEX_CONFIG_ENCRYPTION_KEY_PREVIOUS, 32, 32),
    previousEncryptionKeyVersion: e.APEX_CONFIG_ENCRYPTION_KEY_PREVIOUS_VERSION
      ? Number.parseInt(e.APEX_CONFIG_ENCRYPTION_KEY_PREVIOUS_VERSION, 10)
      : null,
    sessionSecret: session ?? ephemeralSession,
    sessionSecretIsEphemeral: !session,
    agentHmacSecrets: hmac,
    ownerDiscordIds: owners,
    discordOAuth,
    publicBaseUrl: e.APEX_PUBLIC_BASE_URL?.trim().replace(/\/$/, "") || null,
    // Local dev login is honoured ONLY for local DEMO processes, never on Vercel or with a real database.
    allowLocalDevLogin: e.APEX_ALLOW_LOCAL_DEV_LOGIN === "1" && !onVercel && mode === "DEMO" && !databaseUrl,
    demoAutoSeed: mode === "DEMO" && e.APEX_DEMO_AUTOSEED !== "0",
  };
  return cached;
}

export function resetEnvForTests(): void {
  cached = null;
}
