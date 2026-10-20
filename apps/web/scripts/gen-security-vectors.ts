// Generates packages/quant/vectors/{hmac,envelope}.json with fixed, test-only keys.
// These keys are PUBLIC TEST VECTORS — never use them for anything real.
import { writeFileSync } from "node:fs";
import { createPublicKey } from "node:crypto";
import { canonicalRequest, signRequest } from "../src/server/security/hmac";
import { sealToDevice, x25519PrivateFromRaw } from "../src/server/security/envelope";
import { b64url } from "../src/server/security/canonical";

const secret = Buffer.alloc(32, 7);
const cases = [
  { agent_id: "agt_testpi0001", method: "POST", path: "/api/v1/agent/heartbeat", timestamp: "1792512000", nonce: "AAAAAAAAAAAAAAAAAAAAAA", body: '{"hello":"world"}' },
  { agent_id: "agt_testpi0001", method: "GET", path: "/api/v1/agent/work?limit=20", timestamp: "1792512060", nonce: "nonce-with_url-safe-chars-0123", body: "" },
  { agent_id: "agt_testpi0002", method: "POST", path: "/api/v1/agent/positions/events", timestamp: "1792512120", nonce: "ZZZZZZZZZZZZZZZZZZZZZZ", body: '{"a":[1,2,3],"b":"ü"}' },
].map((c) => {
  const { bodySha256, signature } = signRequest(secret, c.agent_id, c.method, c.path, c.timestamp, c.nonce, Buffer.from(c.body, "utf8"));
  return { ...c, body_sha256: bodySha256, canonical: canonicalRequest(c.agent_id, c.method, c.path, c.timestamp, c.nonce, bodySha256), signature };
});
writeFileSync(new URL("../../../packages/quant/vectors/hmac.json", import.meta.url),
  JSON.stringify({ description: "TEST-ONLY HMAC vectors shared by web (TS) and pi-worker (Python).", secret_b64: secret.toString("base64"), cases }, null, 2) + "\n");

const devicePrivRaw = Buffer.alloc(32, 9);
const devicePub = b64url(Buffer.from(createPublicKey(x25519PrivateFromRaw(devicePrivRaw)).export({ format: "jwk" }).x as string, "base64url"));
const meta = { device_id: "agt_testpi0001", provider: "alpaca", secret_version: 3, issued_at: "2026-10-20T20:00:00Z", expires_at: "2026-10-20T20:05:00Z" };
const env = sealToDevice(devicePub, meta, '{"key_id":"TESTKEYID","secret":"TESTSECRET"}', {
  ephemeralPrivateRaw: Buffer.alloc(32, 5), salt: Buffer.alloc(16, 1), iv: Buffer.alloc(12, 2),
});
writeFileSync(new URL("../../../packages/quant/vectors/envelope.json", import.meta.url),
  JSON.stringify({ description: "TEST-ONLY X25519 envelope vector.", device_private_raw_b64url: b64url(devicePrivRaw), device_public_b64url: devicePub,
    plaintext: '{"key_id":"TESTKEYID","secret":"TESTSECRET"}', envelope: env }, null, 2) + "\n");
console.log("vectors written");
