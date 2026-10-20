import { createHash, createPublicKey, verify, type KeyObject } from "node:crypto";
import { b64url, fromB64url } from "./canonical";

export function ed25519PublicFromRaw(raw: Buffer): KeyObject {
  if (raw.length !== 32) throw new Error("Ed25519 public key must be 32 bytes");
  return createPublicKey({ key: { kty: "OKP", crv: "Ed25519", x: b64url(raw) }, format: "jwk" });
}

export function verifyEd25519(publicKeyB64url: string, message: Buffer, signatureB64url: string): boolean {
  try {
    const sig = fromB64url(signatureB64url);
    if (sig.length !== 64) return false;
    return verify(null, message, ed25519PublicFromRaw(fromB64url(publicKeyB64url)), sig);
  } catch {
    return false;
  }
}

export function fingerprint(publicKeyB64url: string): string {
  // short human-comparable fingerprint shown on both the Pi console and the pairing wizard
  const raw = fromB64url(publicKeyB64url);
  const hex = createHash("sha256").update(raw).digest("hex");
  return hex.slice(0, 16).toUpperCase().match(/.{4}/g)!.join("-");
}
