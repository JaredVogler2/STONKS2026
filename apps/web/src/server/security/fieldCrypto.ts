import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";

/**
 * AES-256-GCM per-field encryption for user-configured provider secrets.
 * - fresh 96-bit random IV per encryption
 * - AAD binds ciphertext to provider, field and key version (moving a ciphertext to another field fails)
 * - decryption failures throw a generic error (fail closed, no oracle detail)
 */
export interface Keyring {
  current: { version: number; key: Buffer };
  previous?: { version: number; key: Buffer } | null;
}

export interface SealedField {
  ciphertext: Buffer;
  iv: Buffer;
  authTag: Buffer;
  keyVersion: number;
}

export class DecryptionError extends Error {
  constructor() {
    super("stored secret could not be decrypted (wrong key version or corrupted ciphertext)");
  }
}

export function aadFor(provider: string, field: string, keyVersion: number): Buffer {
  return Buffer.from(`apex-field-v1|${provider}|${field}|k${keyVersion}`, "utf8");
}

export function sealField(keyring: Keyring, provider: string, field: string, plaintext: string): SealedField {
  if (keyring.current.key.length !== 32) throw new Error("encryption key must be 32 bytes");
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", keyring.current.key, iv);
  cipher.setAAD(aadFor(provider, field, keyring.current.version));
  const ciphertext = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  return { ciphertext, iv, authTag: cipher.getAuthTag(), keyVersion: keyring.current.version };
}

export function openField(keyring: Keyring, provider: string, field: string, sealed: SealedField): string {
  const candidates = [keyring.current, keyring.previous].filter(
    (k): k is { version: number; key: Buffer } => Boolean(k) && k!.version === sealed.keyVersion,
  );
  const k = candidates[0];
  if (!k || sealed.iv.length !== 12 || sealed.authTag.length !== 16) throw new DecryptionError();
  try {
    const decipher = createDecipheriv("aes-256-gcm", k.key, sealed.iv);
    decipher.setAAD(aadFor(provider, field, sealed.keyVersion));
    decipher.setAuthTag(sealed.authTag);
    return Buffer.concat([decipher.update(sealed.ciphertext), decipher.final()]).toString("utf8");
  } catch {
    throw new DecryptionError();
  }
}
