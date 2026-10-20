import { createHash, createHmac, timingSafeEqual } from "node:crypto";

export const HMAC_SCHEME = "APEX-HMAC-SHA256-V1";
export const CLOCK_TOLERANCE_SECONDS = 60;
export const NONCE_TTL_SECONDS = 600;

export interface SignedHeaders {
  agentId: string;
  timestamp: string;
  nonce: string;
  bodySha256: string;
  signature: string;
}

export function sha256Hex(data: Uint8Array | string): string {
  return createHash("sha256").update(data).digest("hex");
}

/** Canonical string: scheme, agent id, METHOD, path+query, timestamp, nonce, body sha256 — newline separated. */
export function canonicalRequest(agentId: string, method: string, pathWithQuery: string, timestamp: string, nonce: string,
  bodySha256: string): string {
  return [HMAC_SCHEME, agentId, method.toUpperCase(), pathWithQuery, timestamp, nonce, bodySha256].join("\n");
}

export function signRequest(secret: Buffer, agentId: string, method: string, pathWithQuery: string, timestamp: string,
  nonce: string, body: Uint8Array | string): { bodySha256: string; signature: string } {
  const bodySha256 = sha256Hex(body);
  const signature = createHmac("sha256", secret)
    .update(canonicalRequest(agentId, method, pathWithQuery, timestamp, nonce, bodySha256))
    .digest("hex");
  return { bodySha256, signature };
}

export type HmacFailure =
  | "MISSING_HEADERS"
  | "MALFORMED_HEADERS"
  | "BODY_HASH_MISMATCH"
  | "CLOCK_SKEW"
  | "BAD_SIGNATURE"
  | "NOT_CONFIGURED";

export interface VerifyInput {
  secrets: Buffer[];
  method: string;
  pathWithQuery: string;
  headers: Partial<SignedHeaders>;
  body: Uint8Array;
  nowSeconds: number;
}

export type VerifyResult =
  | { ok: true; agentId: string; nonce: string; skewSeconds: number; secretIndex: number }
  | { ok: false; reason: HmacFailure; skewSeconds?: number };

const AGENT_RE = /^(agt|rel)_[a-z0-9]{8,32}$/;
const NONCE_RE = /^[A-Za-z0-9_-]{22,128}$/;
const HEX64 = /^[0-9a-f]{64}$/;

function safeEqualHex(a: string, b: string): boolean {
  if (!HEX64.test(a) || !HEX64.test(b)) return false;
  return timingSafeEqual(Buffer.from(a, "hex"), Buffer.from(b, "hex"));
}

/** Pure verification (no I/O). Nonce single-use and device status are checked by the caller afterwards. */
export function verifySignedRequest(input: VerifyInput): VerifyResult {
  const h = input.headers;
  if (!input.secrets.length) return { ok: false, reason: "NOT_CONFIGURED" };
  if (!h.agentId || !h.timestamp || !h.nonce || !h.bodySha256 || !h.signature) return { ok: false, reason: "MISSING_HEADERS" };
  if (!AGENT_RE.test(h.agentId) || !/^[0-9]{9,11}$/.test(h.timestamp) || !NONCE_RE.test(h.nonce) || !HEX64.test(h.bodySha256)
    || !HEX64.test(h.signature)) {
    return { ok: false, reason: "MALFORMED_HEADERS" };
  }
  const actual = sha256Hex(input.body);
  if (!safeEqualHex(actual, h.bodySha256)) return { ok: false, reason: "BODY_HASH_MISMATCH" };
  const skew = input.nowSeconds - Number.parseInt(h.timestamp, 10);
  if (Math.abs(skew) > CLOCK_TOLERANCE_SECONDS) return { ok: false, reason: "CLOCK_SKEW", skewSeconds: skew };
  const canonical = canonicalRequest(h.agentId, input.method, input.pathWithQuery, h.timestamp, h.nonce, actual);
  let matched = -1;
  input.secrets.forEach((secret, i) => {
    const expected = createHmac("sha256", secret).update(canonical).digest("hex");
    if (safeEqualHex(expected, h.signature!) && matched < 0) matched = i;
  });
  if (matched < 0) return { ok: false, reason: "BAD_SIGNATURE" };
  return { ok: true, agentId: h.agentId, nonce: h.nonce, skewSeconds: skew, secretIndex: matched };
}
