#!/usr/bin/env node
/*
 * Outside-in deployment smoke test. Run from YOUR computer (not from Vercel):
 *
 *   node scripts/smoke-deployment.mjs https://<production-domain> [--preview https://<preview-url>]
 *
 * Optional environment (never printed):
 *   APEX_SMOKE_BYPASS        Vercel "Protection Bypass for Automation" secret (only valid for signed machine routes)
 *   APEX_SMOKE_AGENT_ID      a paired agent id (agt_...) and
 *   APEX_SMOKE_HMAC_SECRET   the deployment's AGENT_HMAC_SECRET (base64) to prove the signed path + database work
 *
 * Exit code 0 only if every check passes. Checks that need something you did not provide are reported as SKIP.
 */
import { createHash, createHmac, randomBytes } from "node:crypto";

const args = process.argv.slice(2);
const base = args.find((a) => /^https?:\/\//.test(a))?.replace(/\/$/, "");
const preview = args.includes("--preview") ? args[args.indexOf("--preview") + 1]?.replace(/\/$/, "") : null;
if (!base) {
  console.error("usage: node scripts/smoke-deployment.mjs https://<production-domain> [--preview https://<preview-url>]");
  process.exit(2);
}
const bypass = process.env.APEX_SMOKE_BYPASS || null;
const agentId = process.env.APEX_SMOKE_AGENT_ID || null;
const hmacSecret = process.env.APEX_SMOKE_HMAC_SECRET ? Buffer.from(process.env.APEX_SMOKE_HMAC_SECRET, "base64") : null;
const results = [];
const record = (name, status, detail) => {
  results.push({ name, status, detail });
  console.log(`${status === "PASS" ? "✔" : status === "SKIP" ? "-" : "✘"} [${status}] ${name}${detail ? ` — ${detail}` : ""}`);
};

async function get(url, init = {}) {
  try {
    return await fetch(url, { redirect: "manual", signal: AbortSignal.timeout(15000), ...init });
  } catch (e) {
    return { status: 0, headers: new Headers(), text: async () => String(e) };
  }
}

const vercelGate = (res) => {
  const loc = res.headers.get("location") ?? "";
  return res.status === 401 || res.status === 403 || ([302, 303, 307, 308].includes(res.status) && /vercel\.com|_vercel|sso/i.test(loc));
};

async function protection(label, origin) {
  const page = await get(`${origin}/`);
  const loc = page.headers.get("location") ?? "";
  if (vercelGate(page)) record(`${label}: dashboard requires Vercel Authentication`, "PASS", `HTTP ${page.status}`);
  else if ([302, 303, 307, 308].includes(page.status) && loc.includes("/login"))
    record(`${label}: dashboard requires Vercel Authentication`, "FAIL", "the app's own login answered: Vercel Authentication is OFF for this deployment (Settings -> Deployment Protection -> All Deployments)");
  else record(`${label}: dashboard requires Vercel Authentication`, "FAIL", `HTTP ${page.status} without credentials`);
  for (const path of ["/api/v1/admin/overview", "/api/v1/admin/positions", "/api/v1/agent/config"]) {
    const r = await get(`${origin}${path}`);
    record(`${label}: ${path} refuses anonymous requests`, r.status === 200 ? "FAIL" : r.status === 0 ? "FAIL" : "PASS", `HTTP ${r.status}`);
  }
}

function signed(path, method = "GET", body = "") {
  const ts = String(Math.floor(Date.now() / 1000));
  const nonce = randomBytes(18).toString("base64url");
  const bodySha = createHash("sha256").update(body).digest("hex");
  const canon = ["APEX-HMAC-SHA256-V1", agentId, method, path, ts, nonce, bodySha].join("\n");
  const sig = createHmac("sha256", hmacSecret).update(canon).digest("hex");
  return { "x-apex-agent-id": agentId, "x-apex-timestamp": ts, "x-apex-nonce": nonce, "x-apex-body-sha256": bodySha, "x-apex-signature": sig };
}

await protection("production", base);
if (preview) await protection("preview", preview);
else record("preview deployments protected", "SKIP", "pass --preview <url> to check a preview deployment too");

if (bypass) {
  const misuse = await get(`${base}/`, { headers: { "x-vercel-protection-bypass": bypass } });
  record("bypass secret cannot open the dashboard", misuse.status === 403 ? "PASS" : "FAIL", `HTTP ${misuse.status} (expected 403 from the app)`);
  const unsigned = await get(`${base}/api/v1/agent/config`, { headers: { "x-vercel-protection-bypass": bypass } });
  record("bypass alone is not enough for machine routes (HMAC required)", [401, 403].includes(unsigned.status) ? "PASS" : "FAIL", `HTTP ${unsigned.status}`);
  if (agentId && hmacSecret) {
    const r = await get(`${base}/api/v1/agent/config`, { headers: { "x-vercel-protection-bypass": bypass, ...signed("/api/v1/agent/config") } });
    const body = await r.text();
    let json = null;
    try { json = JSON.parse(body); } catch { /* not json */ }
    record("signed agent request reaches the API and database", r.status === 200 && json?.release_mode ? "PASS" : "FAIL",
      r.status === 200 ? `release_mode ${json?.release_mode}, config v${json?.config_version}` : `HTTP ${r.status}${json?.error?.code ? ` ${json.error.code}` : ""}`);
    if (r.status === 200) {
      const h = r.headers;
      const missing = ["strict-transport-security", "x-frame-options", "x-content-type-options", "content-security-policy"].filter((k) => !h.get(k));
      record("security headers present", missing.length ? "FAIL" : "PASS", missing.length ? `missing ${missing.join(", ")}` : "");
      record("compliance switches", "PASS", `personalized group guidance ${json?.compliance?.ALLOW_PERSONALIZED_GROUP_GUIDANCE ? "ON" : "OFF"}`);
    }
  } else {
    record("signed agent request reaches the API and database", "SKIP", "set APEX_SMOKE_AGENT_ID and APEX_SMOKE_HMAC_SECRET");
  }
} else {
  record("bypass misuse and signed-path checks", "SKIP", "set APEX_SMOKE_BYPASS (Vercel Protection Bypass for Automation) to run them");
}
record("Discord bot online", "SKIP", "run /apex_health in your server (the bot is on the Pi; nothing here can reach it)");

const failed = results.filter((r) => r.status === "FAIL").length;
console.log(`\n${failed ? `${failed} check(s) FAILED` : "all executed checks passed"} (${results.filter((r) => r.status === "SKIP").length} skipped)`);
process.exit(failed ? 1 : 0);
