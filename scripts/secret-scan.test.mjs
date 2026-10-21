// Unit tests for scripts/secret-scan.mjs (node --test scripts/secret-scan.test.mjs). No dependencies.
// Token-shaped values are assembled at runtime from a deterministic generator, so this file itself contains no literal
// token and passes the scanner it tests.
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { entropy, hasSequentialRun, isObviouslyFake, loadAllowlist, mask, period, scanFiles, scanText } from "./secret-scan.mjs";

const B62 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
const UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

/** Deterministic, non-sequential, high-entropy string (looks like a real credential, is not one). */
function rnd(n, alphabet = B62, seed = 1234567) {
  let x = seed;
  let out = "";
  for (let i = 0; i < n; i++) {
    x = (x * 1103515245 + 12345) % 2147483648;
    out += alphabet[(x >>> 7) % alphabet.length];
  }
  return out;
}

const rules = (text) => scanText(text, "x.txt").map((f) => f.rule);

test("detects private key headers", () => {
  const header = "-----BEGIN " + "OPENSSH PRIVATE" + " KEY-----";
  assert.deepEqual(rules(header), ["private-key"]);
  assert.deepEqual(rules("-----BEGIN " + "PRIVATE KEY-----"), ["private-key"]);
  assert.deepEqual(rules("-----BEGIN " + "PUBLIC KEY-----"), []);
});

test("detects GitHub, Discord, Alpaca, AWS and Slack token shapes", () => {
  assert.ok(rules(`token = "${"gh" + "p_" + rnd(36)}"`).includes("github-token"));
  assert.ok(rules("github_" + "pat_" + rnd(82, B62 + "_", 99)).includes("github-pat"));
  const discord = "M" + rnd(25, B62, 7) + "." + rnd(6, B62, 8) + "." + rnd(38, B62, 9);
  assert.ok(rules(`client.run("${discord}")`).includes("discord-bot-token"));
  const hook = "https://discord.com/api/" + "webhooks/123456789012345678/" + rnd(68, B62, 11);
  assert.ok(rules(hook).includes("discord-webhook"));
  assert.ok(rules("key: " + "PK" + rnd(18, UPPER, 5)).includes("alpaca-key-id"));
  assert.ok(rules("AK" + "IA" + rnd(16, UPPER, 6)).includes("aws-access-key"));
  assert.ok(rules("xo" + "xb-" + rnd(30, B62, 12)).includes("slack-token"));
});

test("detects JWTs and literal secret assignments, ignores references", () => {
  const jwt = "ey" + "J" + rnd(20, B62, 21) + ".ey" + "J" + rnd(30, B62, 22) + "." + rnd(43, B62, 23);
  assert.ok(rules(jwt).includes("jwt"));
  assert.ok(rules("APEX_RELEASE_HMAC_" + "SECRET=" + rnd(44, B62, 31)).includes("secret-assignment"));
  assert.ok(rules('"DISCORD_BOT_' + 'TOKEN": "' + rnd(40, B62, 32) + '"').includes("secret-assignment"));
  assert.deepEqual(rules("APEX_RELEASE_HMAC_" + "SECRET: ${{ secrets.APEX_RELEASE_HMAC_SECRET }}"), []);
  assert.deepEqual(rules("APEX_RELEASE_HMAC_" + "SECRET = process.env.APEX_RELEASE_HMAC_SECRET"), []);
  assert.deepEqual(rules('ENV_SECRET = "APEX_RELEASE_HMAC_' + 'SECRET"'), []);
});

test("flags NEXT_PUBLIC_* names that carry secrets but not public config", () => {
  assert.deepEqual(rules("NEXT_PUBLIC_" + "SUPABASE_SERVICE_ROLE_KEY=abc"), ["next-public-secret"]);
  assert.deepEqual(rules("process.env.NEXT_PUBLIC_" + "DISCORD_TOKEN"), ["next-public-secret"]);
  assert.deepEqual(rules("NEXT_PUBLIC_" + "APP_NAME=APEX"), []);
  assert.deepEqual(rules("selector: /^NEXT_PUBLIC_" + ".*(KEY|SECRET)/"), []);
});

test("allowlist: obviously fake fixtures, inline pragma and allow file", () => {
  // base64 of a repeated byte (as in TEST-ONLY HMAC vectors), sequential alphabets and marker words are fake
  assert.ok(isObviouslyFake("Bwc" + "HBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc="));
  assert.ok(isObviouslyFake("abcdefghijklmnopqrstuvwxyz0123456789"));
  assert.ok(isObviouslyFake("REPLACE_WITH_PRIVATE_DISCORD_BOT_TOKEN"));
  assert.ok(isObviouslyFake("PK" + "TESTKEY1234567ABCD"));
  assert.ok(!isObviouslyFake(rnd(40)));
  assert.deepEqual(rules("APEX_RELEASE_HMAC_" + "SECRET=" + "AAAA".repeat(11)), []);
  assert.deepEqual(rules("gh" + "p_" + rnd(36) + "  # secret-scan: " + "allow (documented fixture)"), []);
  const dir = mkdtempSync(join(tmpdir(), "secret-scan-"));
  const allowFile = join(dir, "allow.json");
  writeFileSync(allowFile, JSON.stringify([{ path: "fixtures/**/*.txt", rule: "github-token", reason: "unit test" }]));
  const allow = loadAllowlist(allowFile);
  assert.deepEqual(scanText("gh" + "p_" + rnd(36), "fixtures/a/b.txt", allow), []);
  assert.equal(scanText("gh" + "p_" + rnd(36), "src/b.txt", allow).length, 1);
  assert.deepEqual(loadAllowlist(join(dir, "missing.json")), []);
});

test("findings are masked and carry path and line", () => {
  const token = "gh" + "p_" + rnd(36);
  const [f] = scanText("ok\n" + token, "a/b.py");
  assert.equal(f.path, "a/b.py");
  assert.equal(f.line, 2);
  assert.ok(!f.masked.includes(token.slice(8)), "full token must never be printed");
  assert.equal(mask("short"), "*****");
});

test("scanFiles skips binary and lock files and reports scanned count", () => {
  const dir = mkdtempSync(join(tmpdir(), "secret-scan-files-"));
  writeFileSync(join(dir, "clean.txt"), "nothing here\n");
  writeFileSync(join(dir, "leak.env.sample"), "APEX_MODEL_SIGNING_" + "KEY=" + rnd(43, B62 + "_-", 77) + "\n");
  writeFileSync(join(dir, "blob.bin"), Buffer.from([0, 1, 2, 3]));
  writeFileSync(join(dir, "pnpm-lock.yaml"), "gh" + "p_" + rnd(36) + "\n");
  const { findings, scanned } = scanFiles(dir, ["clean.txt", "leak.env.sample", "blob.bin", "pnpm-lock.yaml", "gone.txt"]);
  assert.equal(scanned, 2);
  assert.deepEqual(findings.map((f) => [f.path, f.rule]), [["leak.env.sample", "secret-assignment"]]);
});

test("helpers", () => {
  assert.equal(period("abcabcabc"), 3);
  assert.equal(period("abcd"), 4);
  assert.ok(entropy("aaaa") < 0.01);
  assert.ok(entropy(rnd(64)) > 4.5);
  assert.ok(hasSequentialRun("xx12345678yy"));
  assert.ok(!hasSequentialRun(rnd(64)));
});
