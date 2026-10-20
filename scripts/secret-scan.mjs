#!/usr/bin/env node
// Dependency-free secret scanner (CI job `secret-scan`, `pnpm secret-scan`).
//
// Scans git-tracked files plus untracked-but-not-ignored files (so it also catches a secret before it is committed)
// for private keys, GitHub / Discord / Alpaca / AWS / Slack-looking tokens, JWTs, secret-named assignments with
// literal high-entropy values, and NEXT_PUBLIC_* variables whose names say they hold a secret (those are inlined into
// the browser bundle). Findings print as `path:line rule masked` (never the full value); exit 1 on findings, 0 when
// clean, 2 on usage errors.
//
// Allowlist for obviously fake test fixtures (all three are deliberate and reviewable):
//   1. values that are clearly fake: low entropy, a short repeating period (e.g. base64 of one repeated byte), a run of
//      sequential characters (abcdefgh / 01234567), an UPPER_SNAKE env-var name, or a marker such as EXAMPLE / FAKE /
//      DUMMY / TEST / SAMPLE / PLACEHOLDER / REPLACE / CHANGEME / REDACTED / XXXX;
//   2. an inline `secret-scan: allow` pragma on the same line;
//   3. scripts/secret-scan.allow.json: [{ "path": "<glob>", "rule": "<rule id or *>", "reason": "..." }].
// This is a tripwire for mistakes, not a guarantee; GitHub secret scanning / push protection should also be enabled.
import { execFileSync } from "node:child_process";
import { readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const MAX_BYTES = 2 * 1024 * 1024;
const SKIP_PATHS = [/(^|\/)node_modules\//, /(^|\/)\.git\//, /(^|\/)\.venv\//, /(^|\/)\.next\//, /(^|\/)pnpm-lock\.yaml$/, /(^|\/)uv\.lock$/,
  /\.(png|jpe?g|gif|webp|ico|pdf|woff2?|ttf|otf|zip|gz|tgz|parquet|sqlite3?|wasm)$/i];
const SECRET_NAME = "[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PRIVATE_KEY|SIGNING_KEY|SERVICE_ROLE_KEY|HMAC|API_KEY|ENCRYPTION_KEY)[A-Z0-9_]*";

/** Rule set. `value` (optional) selects the capture group whose content is checked against the fake-value allowlist. */
export const RULES = [
  { id: "private-key", re: /-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----/g },
  { id: "github-token", re: /\b(gh[pousr]_[A-Za-z0-9]{36,255})\b/g, value: 1 },
  { id: "github-pat", re: /\b(github_pat_[A-Za-z0-9_]{60,255})\b/g, value: 1 },
  { id: "discord-bot-token", re: /\b([MNO][A-Za-z0-9_-]{23,27}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,40})\b/g, value: 1 },
  { id: "discord-webhook", re: /https:\/\/(?:ptb\.|canary\.)?discord(?:app)?\.com\/api\/webhooks\/\d{5,}\/([A-Za-z0-9_-]{50,})/g, value: 1 },
  { id: "alpaca-key-id", re: /\b((?:PK|AK|CK)[A-Z0-9]{18})\b/g, value: 1 },
  { id: "aws-access-key", re: /\b((?:AKIA|ASIA)[A-Z0-9]{16})\b/g, value: 1 },
  { id: "slack-token", re: /\b(xox[abprs]-[A-Za-z0-9-]{10,})\b/g, value: 1 },
  { id: "jwt", re: /\b(eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{16,})\b/g, value: 1 },
  { id: "secret-assignment", re: new RegExp(`\\b(${SECRET_NAME})["']?\\s*[:=]\\s*["']?([A-Za-z0-9+/_.=-]{24,})`, "g"), value: 2 },
  { id: "next-public-secret", re: /\b(NEXT_PUBLIC_[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PRIVATE|SERVICE_ROLE|HMAC|SIGNING|API_KEY|ENCRYPTION)[A-Z0-9_]*)\b/g },
];

const FAKE_MARKERS = /(EXAMPLE|FAKE|DUMMY|PLACEHOLDER|SAMPLE|REPLACE|CHANGE_?ME|REDACTED|NOT_?A_?REAL|XXXX|YOUR_|TEST)/i;
const IDENTIFIER = /^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$/; // an env-var NAME (e.g. FOO_SECRET = "APEX_RELEASE_HMAC_SECRET")

/** True when s contains a run of >= n consecutive ascending/descending characters (abcdefgh, 01234567, zyxwvuts). */
export function hasSequentialRun(s, n = 8) {
  let up = 1;
  let down = 1;
  for (let i = 1; i < s.length; i++) {
    const d = s.charCodeAt(i) - s.charCodeAt(i - 1);
    up = d === 1 ? up + 1 : 1;
    down = d === -1 ? down + 1 : 1;
    if (up >= n || down >= n) return true;
  }
  return false;
}

export function entropy(s) {
  const counts = new Map();
  for (const ch of s) counts.set(ch, (counts.get(ch) ?? 0) + 1);
  let h = 0;
  for (const n of counts.values()) {
    const p = n / s.length;
    h -= p * Math.log2(p);
  }
  return h;
}

/** Smallest period p such that s is (a prefix of) a repetition of s.slice(0, p); s.length if none. */
export function period(s) {
  for (let p = 1; p <= s.length / 2; p++) {
    let ok = true;
    for (let i = p; i < s.length; i++) {
      if (s[i] !== s[i - p]) {
        ok = false;
        break;
      }
    }
    if (ok) return p;
  }
  return s.length;
}

export function isObviouslyFake(value) {
  if (!value) return true;
  if (FAKE_MARKERS.test(value) || IDENTIFIER.test(value) || hasSequentialRun(value)) return true;
  const core = value.replace(/[=._-]+$/g, "");
  if (core.length >= 8 && period(core) <= 8) return true; // e.g. base64 of a repeated byte, "abcabcabc..."
  if (entropy(core) < 3.0) return true;
  if (/^\$\{|^<|^%|process\.env|secrets\./.test(value)) return true; // references / templates, not literals
  return false;
}

export function mask(value) {
  if (value.length <= 8) return "*".repeat(value.length);
  return `${value.slice(0, 4)}…(${value.length} chars)`;
}

function globToRegExp(glob) {
  let re = "";
  for (let i = 0; i < glob.length; i++) {
    const ch = glob[i];
    if (ch === "*" && glob[i + 1] === "*") {
      const slash = glob[i + 2] === "/";
      re += slash ? "(?:.*/)?" : ".*";
      i += slash ? 2 : 1;
    } else if (ch === "*") re += "[^/]*";
    else if (ch === "?") re += "[^/]";
    else re += ch.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`^${re}$`);
}

export function loadAllowlist(file) {
  try {
    const entries = JSON.parse(readFileSync(file, "utf8"));
    if (!Array.isArray(entries)) throw new Error("allowlist must be a JSON array");
    return entries.map((e) => {
      if (!e.path || !e.reason) throw new Error("allowlist entries need path and reason");
      return { path: globToRegExp(String(e.path)), rule: String(e.rule ?? "*"), reason: String(e.reason) };
    });
  } catch (err) {
    if (err && err.code === "ENOENT") return [];
    throw err;
  }
}

/** Scan one file's text. Returns findings [{path, line, rule, masked}]. */
export function scanText(text, path = "<text>", allow = []) {
  const findings = [];
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.length > 20_000 || /secret-scan:\s*allow/.test(line)) continue;
    for (const rule of RULES) {
      rule.re.lastIndex = 0;
      for (const m of line.matchAll(rule.re)) {
        const value = rule.value ? m[rule.value] : m[0];
        if (rule.value && isObviouslyFake(value)) continue;
        if (allow.some((a) => a.path.test(path) && (a.rule === "*" || a.rule === rule.id))) continue;
        findings.push({ path, line: i + 1, rule: rule.id, masked: mask(value) });
      }
    }
  }
  return findings;
}

function isBinary(buf) {
  const n = Math.min(buf.length, 8192);
  for (let i = 0; i < n; i++) if (buf[i] === 0) return true;
  return false;
}

export function listFiles(root) {
  const out = execFileSync("git", ["ls-files", "-z", "--cached", "--others", "--exclude-standard"], { cwd: root, maxBuffer: 64 * 1024 * 1024 });
  return [...new Set(out.toString("utf8").split("\0").filter(Boolean))];
}

export function scanFiles(root, files, allow = []) {
  const findings = [];
  let scanned = 0;
  for (const rel of files) {
    if (SKIP_PATHS.some((re) => re.test(rel))) continue;
    const abs = join(root, rel);
    let st;
    try {
      st = statSync(abs);
    } catch {
      continue; // deleted in the working tree
    }
    if (!st.isFile() || st.size > MAX_BYTES) continue;
    const buf = readFileSync(abs);
    if (isBinary(buf)) continue;
    scanned++;
    findings.push(...scanText(buf.toString("utf8"), rel, allow));
  }
  return { findings, scanned };
}

function main(argv) {
  const here = dirname(fileURLToPath(import.meta.url));
  const root = resolve(here, "..");
  const json = argv.includes("--json");
  const explicit = argv.filter((a) => !a.startsWith("--"));
  if (argv.some((a) => a.startsWith("--") && a !== "--json")) {
    console.error("usage: node scripts/secret-scan.mjs [--json] [file ...]");
    return 2;
  }
  const allow = loadAllowlist(join(here, "secret-scan.allow.json"));
  const files = explicit.length ? explicit.map((f) => relative(root, resolve(f))) : listFiles(root);
  const { findings, scanned } = scanFiles(root, files, allow);
  if (json) console.log(JSON.stringify({ scanned, findings }, null, 2));
  else {
    for (const f of findings) console.log(`${f.path}:${f.line} ${f.rule} ${f.masked}`);
    console.log(findings.length ? `secret-scan: ${findings.length} finding(s) in ${scanned} files - rotate anything real, then remove it`
      : `secret-scan: clean (${scanned} files)`);
  }
  return findings.length ? 1 : 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  process.exitCode = main(process.argv.slice(2));
}
