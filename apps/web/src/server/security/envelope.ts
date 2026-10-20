import { createCipheriv, createPublicKey, diffieHellman, generateKeyPairSync, hkdfSync, randomBytes, type KeyObject,
  createPrivateKey } from "node:crypto";
import { b64url, canonicalJson, fromB64url } from "./canonical";

/**
 * Short-lived secret delivery to a paired Pi: ephemeral X25519 ECDH with the Pi's registered public key,
 * HKDF-SHA256, AES-256-GCM with the canonical metadata as AAD. The browser and the database never see the
 * plaintext; the envelope is produced in the request that delivers it and is never persisted.
 */
export const ENVELOPE_ALG = "X25519-HKDF-SHA256-A256GCM";
const INFO = Buffer.from("apex-secret-envelope-v1", "utf8");

export interface EnvelopeMeta {
  device_id: string;
  provider: string;
  secret_version: number;
  issued_at: string;
  expires_at: string;
}

export interface SecretEnvelope {
  v: 1;
  alg: typeof ENVELOPE_ALG;
  epk: string;
  salt: string;
  iv: string;
  ct: string;
  tag: string;
  meta: EnvelopeMeta;
}

export function x25519PublicFromRaw(raw: Buffer): KeyObject {
  if (raw.length !== 32) throw new Error("X25519 public key must be 32 bytes");
  return createPublicKey({ key: { kty: "OKP", crv: "X25519", x: b64url(raw) }, format: "jwk" });
}

const X25519_PKCS8_PREFIX = Buffer.from("302e020100300506032b656e04220420", "hex");

export function x25519PrivateFromRaw(raw: Buffer): KeyObject {
  if (raw.length !== 32) throw new Error("X25519 private key must be 32 bytes");
  return createPrivateKey({ key: Buffer.concat([X25519_PKCS8_PREFIX, raw]), format: "der", type: "pkcs8" });
}

export function sealToDevice(devicePublicKeyB64url: string, meta: EnvelopeMeta, plaintext: string,
  testOnly?: { ephemeralPrivateRaw: Buffer; salt: Buffer; iv: Buffer }): SecretEnvelope {
  const recipient = x25519PublicFromRaw(fromB64url(devicePublicKeyB64url));
  const ephPriv: KeyObject = testOnly
    ? x25519PrivateFromRaw(testOnly.ephemeralPrivateRaw)
    : generateKeyPairSync("x25519").privateKey;
  const ephPubRaw = fromB64url(createPublicKey(ephPriv).export({ format: "jwk" }).x as string);
  const shared = diffieHellman({ privateKey: ephPriv, publicKey: recipient });
  const salt = testOnly?.salt ?? randomBytes(16);
  const key = Buffer.from(hkdfSync("sha256", shared, salt, Buffer.concat([INFO, ephPubRaw]), 32));
  const iv = testOnly?.iv ?? randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key, iv);
  cipher.setAAD(Buffer.from(canonicalJson(meta), "utf8"));
  const ct = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  return { v: 1, alg: ENVELOPE_ALG, epk: b64url(ephPubRaw), salt: b64url(salt), iv: b64url(iv), ct: b64url(ct),
    tag: b64url(cipher.getAuthTag()), meta };
}
