import "server-only";
import { createHash, randomBytes, randomInt } from "node:crypto";
import type { Db } from "../db";
import { HttpError } from "../http/responses";
import { canonicalJson } from "../security/canonical";
import { fingerprint, verifyEd25519 } from "../security/ed25519";
import { sealToDevice, type SecretEnvelope } from "../security/envelope";
import { audit, securityEvent } from "./audit";
import { readIntegrationSecrets } from "./settings";

const CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"; // no 0/O/1/I/L/U ambiguity
const CODE_TTL_MINUTES = 10;
const PAIRING_PREFIX = "apex-pairing-proof-v1\n";
const ENVELOPE_TTL_SECONDS = 300;

export function normalizeCode(code: string): string {
  return code.toUpperCase().replace(/[^A-Z0-9]/g, "");
}

export function codeHash(code: string): string {
  return createHash("sha256").update(`apex-pairing|${normalizeCode(code)}`).digest("hex");
}

/** Server nonce bound into the Pi's proof-of-possession signature (both sides derive it from the code). */
export function serverNonceFor(code: string): string {
  return codeHash(code).slice(0, 16);
}

export async function startPairing(db: Db, ownerId: string): Promise<{ code: string; expires_at: string }> {
  const raw = Array.from({ length: 8 }, () => CODE_ALPHABET[randomInt(CODE_ALPHABET.length)]).join("");
  const code = `${raw.slice(0, 4)}-${raw.slice(4)}`;
  const expires = new Date(Date.now() + CODE_TTL_MINUTES * 60_000);
  await db.query(
    "INSERT INTO apex.pairing_challenges(code_hash, server_nonce, created_by, expires_at) VALUES ($1, $2, $3, $4)",
    [codeHash(code), randomBytes(16).toString("hex"), ownerId, expires],
  );
  await audit(db, { actorType: "owner", actorId: ownerId, entity: "pairing_challenges", action: "pairing.started" });
  return { code, expires_at: expires.toISOString() };
}

export interface PairRequest {
  code: string;
  device_name: string;
  x25519_public_key: string;
  ed25519_public_key: string;
  proof: string;
}

export async function completePairing(db: Db, agentId: string, req: PairRequest): Promise<{ status: string; fingerprint: string }> {
  if (!/^[A-Za-z0-9_-]{43}$/.test(req.x25519_public_key) || !/^[A-Za-z0-9_-]{43}$/.test(req.ed25519_public_key)) {
    throw new HttpError(422, "INVALID_KEYS", "public keys must be 32-byte base64url");
  }
  const name = String(req.device_name ?? "apex-pi").slice(0, 48) || "apex-pi";
  return db.tx(async (sql) => {
    const rows = await sql.query<{ id: string; expires_at: Date; used_at: Date | null; failed_attempts: number }>(
      "SELECT id, expires_at, used_at, failed_attempts FROM apex.pairing_challenges WHERE code_hash = $1 FOR UPDATE", [codeHash(req.code)]);
    const ch = rows[0];
    if (!ch || ch.used_at || new Date(ch.expires_at).getTime() < Date.now() || ch.failed_attempts >= 5) {
      await securityEvent(sql, "pairing_invalid_code", { agentId, detail: ch ? "expired/used/locked" : "unknown code" });
      throw new HttpError(403, "PAIRING_CODE_INVALID", "pairing code is invalid, used or expired; start a new pairing in the dashboard");
    }
    const message = Buffer.from(PAIRING_PREFIX + canonicalJson({ agent_id: agentId, code: normalizeCode(req.code),
      server_nonce: serverNonceFor(req.code), x25519: req.x25519_public_key }), "utf8");
    if (!verifyEd25519(req.ed25519_public_key, message, req.proof)) {
      await sql.query("UPDATE apex.pairing_challenges SET failed_attempts = failed_attempts + 1 WHERE id = $1", [ch.id]);
      await securityEvent(sql, "pairing_bad_proof", { agentId });
      throw new HttpError(403, "PAIRING_PROOF_INVALID", "proof of key possession failed");
    }
    const fp = fingerprint(req.ed25519_public_key);
    const existing = await sql.query<{ status: string }>("SELECT status FROM apex.agent_devices WHERE id = $1", [agentId]);
    if (existing[0]?.status === "revoked") throw new HttpError(403, "DEVICE_REVOKED", "this device id was revoked; generate a new agent id");
    await sql.query(
      `INSERT INTO apex.agent_devices(id, device_name, status, x25519_public_key, ed25519_public_key, fingerprint)
       VALUES ($1,$2,'pending_approval',$3,$4,$5)
       ON CONFLICT (id) DO UPDATE SET device_name = EXCLUDED.device_name, status = 'pending_approval', x25519_public_key = EXCLUDED.x25519_public_key,
         ed25519_public_key = EXCLUDED.ed25519_public_key, fingerprint = EXCLUDED.fingerprint, approved_at = NULL, approved_by = NULL`,
      [agentId, name, req.x25519_public_key, req.ed25519_public_key, fp],
    );
    await sql.query("UPDATE apex.pairing_challenges SET used_at = now(), device_id = $2 WHERE id = $1", [ch.id, agentId]);
    await audit(sql, { actorType: "agent", actorId: agentId, entity: "agent_devices", entityId: agentId, action: "pairing.requested",
      metadata: { fingerprint: fp } });
    return { status: "pending_approval", fingerprint: fp };
  });
}

export async function setDeviceStatus(db: Db, ownerId: string, agentId: string, action: "approve" | "revoke"): Promise<void> {
  const rows = action === "approve"
    ? await db.query("UPDATE apex.agent_devices SET status='active', approved_at=now(), approved_by=$2 WHERE id=$1 AND status='pending_approval' RETURNING id", [agentId, ownerId])
    : await db.query("UPDATE apex.agent_devices SET status='revoked', revoked_at=now() WHERE id=$1 AND status <> 'revoked' RETURNING id", [agentId]);
  if (!rows.length) throw new HttpError(409, "INVALID_DEVICE_STATE", `device cannot be ${action}d from its current state`);
  await audit(db, { actorType: "owner", actorId: ownerId, entity: "agent_devices", entityId: agentId, action: `device.${action}d` });
}

/** Encrypt provider credentials to the paired Pi's X25519 key. Plaintext exists only inside this request. */
export async function secretEnvelope(db: Db, agentId: string, provider: string): Promise<SecretEnvelope> {
  if (provider !== "alpaca") throw new HttpError(404, "NOT_FOUND", "no deliverable secret for that provider");
  const dev = await db.query<{ x25519_public_key: string | null; status: string }>("SELECT x25519_public_key, status FROM apex.agent_devices WHERE id=$1", [agentId]);
  if (dev[0]?.status !== "active" || !dev[0].x25519_public_key) throw new HttpError(403, "DEVICE_NOT_ACTIVE", "device not approved");
  const secret = await readIntegrationSecrets(db, provider);
  if (!secret) throw new HttpError(404, "NOT_CONFIGURED", "provider credentials are not configured in Settings");
  const issued = new Date();
  const expires = new Date(issued.getTime() + ENVELOPE_TTL_SECONDS * 1000);
  const iso = (d: Date) => d.toISOString().replace(/\.\d{3}Z$/, "Z");
  const env = sealToDevice(dev[0].x25519_public_key, { device_id: agentId, provider, secret_version: secret.secretVersion,
    issued_at: iso(issued), expires_at: iso(expires) }, JSON.stringify(secret.fields));
  await db.query("INSERT INTO apex.secret_deliveries(device_id, provider, secret_version, expires_at) VALUES ($1,$2,$3,$4)", [
    agentId, provider, secret.secretVersion, expires]);
  await audit(db, { actorType: "agent", actorId: agentId, entity: "secret_deliveries", action: "secret.envelope_issued",
    metadata: { provider, version: secret.secretVersion } });
  return env;
}

export async function ackSecret(db: Db, agentId: string, provider: string, version: number): Promise<void> {
  await db.tx(async (sql) => {
    await sql.query("UPDATE apex.secret_deliveries SET acked_at = now() WHERE device_id=$1 AND provider=$2 AND secret_version=$3 AND acked_at IS NULL",
      [agentId, provider, version]);
    await sql.query("UPDATE apex.integration_status SET pi_ack_version = GREATEST(pi_ack_version, $2), pi_acked_at = now() WHERE provider = $1", [provider, version]);
    await sql.query("UPDATE apex.agent_devices SET secret_version_ack = GREATEST(secret_version_ack, $2) WHERE id = $1", [agentId, version]);
  });
}
