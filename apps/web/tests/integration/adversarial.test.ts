import { randomBytes } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";

/* Hostile-reviewer tests (Phase 12 threat scenarios) against the REAL route handlers and services on a migrated DB. */

process.env.APEX_MODE = "DEMO";
process.env.APEX_ALLOW_LOCAL_DEV_LOGIN = "1";
process.env.AGENT_HMAC_SECRET = Buffer.alloc(32, 7).toString("base64");
process.env.AGENT_HMAC_SECRET_PREVIOUS = Buffer.alloc(32, 9).toString("base64");
process.env.APEX_RELEASE_HMAC_SECRET = Buffer.alloc(32, 5).toString("base64");
process.env.APEX_CONFIG_ENCRYPTION_KEY = Buffer.alloc(32, 3).toString("base64");
process.env.APEX_SESSION_SECRET = Buffer.alloc(32, 4).toString("base64");
delete process.env.DATABASE_URL;

const { openTestDb } = await import("../support/test-db");
const { migrate } = await import("@/server/db/migrate");
const { __setDbForTests } = await import("@/server/db");
const { ensureDemoSeed } = await import("@/server/services/demo");
const { createOwnerSession } = await import("@/server/auth/owner");
const { resolveMember } = await import("@/server/services/members");
const { recordPositionEvent } = await import("@/server/services/positions");
const { saveIntegration } = await import("@/server/services/settings");
const { signRequest } = await import("@/server/security/hmac");
const { HttpError } = await import("@/server/http/responses");

type Db = Awaited<ReturnType<typeof openTestDb>>;
let db: Db;
let ownerId: string;
let cookie: string;

beforeAll(async () => {
  db = await openTestDb();
  await migrate(db);
  __setDbForTests(db);
  await ensureDemoSeed(db);
  ownerId = (await db.query<{ id: string }>("SELECT id FROM apex.app_users WHERE role='owner'"))[0]!.id;
  cookie = `apex_session=${await createOwnerSession(db, ownerId, "local_dev", "vitest")}`;
}, 240_000);

const BASE = "http://apex.test";
const owner = (path: string, init: RequestInit & { csrf?: boolean; site?: string } = {}) => {
  const headers = new Headers(init.headers);
  headers.set("cookie", cookie);
  headers.set("host", "apex.test");
  if (init.method && init.method !== "GET") {
    headers.set("sec-fetch-site", init.site ?? "same-origin");
    if (init.csrf !== false) headers.set("x-apex-csrf", "1");
    headers.set("content-type", "application/json");
  }
  return new Request(`${BASE}${path}`, { ...init, headers });
};
const params = <T extends Record<string, string>>(p: T) => ({ params: Promise.resolve(p) });

function signedAgent(path: string, body: string, secret: Buffer, agent = "agt_demodevice01", method = "POST") {
  const ts = String(Math.floor(Date.now() / 1000));
  const nonce = randomBytes(18).toString("base64url");
  const { bodySha256, signature } = signRequest(secret, agent, method, path, ts, nonce, body);
  return new Request(`${BASE}${path}`, { method, body: method === "GET" ? undefined : body, headers: {
    "x-apex-agent-id": agent, "x-apex-timestamp": ts, "x-apex-nonce": nonce, "x-apex-body-sha256": bodySha256, "x-apex-signature": signature } });
}

const guild = async () => (await db.query<{ discord_guild_id: string }>("SELECT discord_guild_id FROM apex.guilds WHERE allowlisted LIMIT 1"))[0]!.discord_guild_id;

async function status(p: Promise<Response>) {
  const r = await p;
  return r.status;
}

describe("1. anonymous and spoofed identities", () => {
  it("every admin read route refuses a request without a session, even with identity-looking headers", async () => {
    const routes = await Promise.all([
      import("@/app/api/v1/admin/overview/route"), import("@/app/api/v1/admin/settings/route"), import("@/app/api/v1/admin/signals/route"),
      import("@/app/api/v1/admin/positions/route"), import("@/app/api/v1/admin/audit/route"), import("@/app/api/v1/admin/backtests/route"),
      import("@/app/api/v1/admin/health/route"),
    ]);
    for (const r of routes) {
      const req = new Request(`${BASE}/api/v1/admin/x`, { headers: { "x-user-email": "owner@example.com", "x-apex-owner": "1", authorization: "Bearer forged" } });
      expect(await status(r.GET(req))).toBe(401);
    }
  });

  it("a forged session cookie is rejected", async () => {
    const { GET } = await import("@/app/api/v1/admin/overview/route");
    expect(await status(GET(new Request(`${BASE}/x`, { headers: { cookie: "apex_session=eyJhbGciOiJIUzI1NiJ9.e30.forged" } })))).toBe(401);
  });

  it("owner mutations require same-origin and the CSRF header", async () => {
    const { PUT } = await import("@/app/api/v1/admin/settings/route");
    const body = JSON.stringify({ key: "market.slots", value: { max_slots: 20, reserve_for_positions: 5 } });
    expect(await status(PUT(owner("/api/v1/admin/settings", { method: "PUT", body, csrf: false })))).toBe(403);
    expect(await status(PUT(owner("/api/v1/admin/settings", { method: "PUT", body, site: "cross-site" })))).toBe(403);
    expect(await status(PUT(owner("/api/v1/admin/settings", { method: "PUT", body })))).toBe(200);
  });

  it("a revoked owner session stops working immediately", async () => {
    const token = await createOwnerSession(db, ownerId, "local_dev", "vitest-2");
    const { GET } = await import("@/app/api/v1/admin/overview/route");
    const req = () => new Request(`${BASE}/x`, { headers: { cookie: `apex_session=${token}` } });
    expect(await status(GET(req()))).toBe(200);
    await db.query("UPDATE apex.admin_sessions SET revoked_at = now() WHERE revoked_at IS NULL AND user_agent_hash IS NOT NULL AND id IN (SELECT id FROM apex.admin_sessions ORDER BY created_at DESC LIMIT 1)");
    expect(await status(GET(req()))).toBe(401);
  });
});

describe("6. hostile payloads", () => {
  it("rejects a 50 MB declared body before reading it, and a streamed oversize body", async () => {
    const { POST } = await import("@/app/api/v1/agent/heartbeat/route");
    const declared = new Request(`${BASE}/api/v1/agent/heartbeat`, { method: "POST", body: "{}", headers: { "content-length": String(50 * 1024 * 1024) } });
    expect(await status(POST(declared))).toBe(413);
    const big = new ReadableStream({ start(c) { for (let i = 0; i < 40; i++) c.enqueue(new Uint8Array(16 * 1024)); c.close(); } });
    const streamed = new Request(`${BASE}/api/v1/agent/heartbeat`, { method: "POST", body: big, duplex: "half" } as RequestInit);
    expect(await status(POST(streamed))).toBe(413);
  });

  it("rejects deeply nested JSON and non-UTF-8 bodies with 400, not a crash", async () => {
    const { PUT } = await import("@/app/api/v1/admin/settings/route");
    const deep = `${"[".repeat(2000)}${"]".repeat(2000)}`;
    const r = await PUT(owner("/api/v1/admin/settings", { method: "PUT", body: deep }));
    expect(r.status).toBe(400);
    expect(((await r.json()) as { error: { code: string } }).error.code).toBe("JSON_TOO_DEEP");
    const bad = await PUT(owner("/api/v1/admin/settings", { method: "PUT", body: new Uint8Array([0x7b, 0xff, 0xfe, 0x7d]) }));
    expect(bad.status).toBe(400);
  });

  it("malformed ids return 404 instead of reaching SQL", async () => {
    const pos = await import("@/app/api/v1/admin/positions/[id]/route");
    expect(await status(pos.GET(owner("/x"), params({ id: "1 OR 1=1" })))).toBe(404);
    const sig = await import("@/app/api/v1/admin/signals/[id]/route");
    expect(await status(sig.GET(owner("/x"), params({ id: "sig_'; DROP TABLE apex.signals;--" })))).toBe(404);
  });
});

describe("2/3/9. machine identities", () => {
  it("accepts the previous agent secret during rotation and rejects an unknown one", async () => {
    const { POST } = await import("@/app/api/v1/agent/heartbeat/route");
    const hb = JSON.stringify({ agent_id: "agt_demodevice01", sent_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"), worker_version: "0.1.0",
      release_mode: "DEMO", secret_version_ack: 0, components: {}, resources: {} });
    expect(await status(POST(signedAgent("/api/v1/agent/heartbeat", hb, Buffer.alloc(32, 9))))).toBe(200);
    expect(await status(POST(signedAgent("/api/v1/agent/heartbeat", hb, Buffer.alloc(32, 1))))).toBe(401);
  });

  it("a Pi identity cannot use release routes and a release identity cannot act as a Pi", async () => {
    const rel = await import("@/app/api/v1/release/champions/route");
    // the Pi's own secret does not verify on release routes, and even a valid release signature needs a rel_* identity
    expect(await status(rel.GET(signedAgent("/api/v1/release/champions", "", Buffer.alloc(32, 7), "agt_demodevice01", "GET")))).toBe(401);
    expect(await status(rel.GET(signedAgent("/api/v1/release/champions", "", Buffer.alloc(32, 5), "agt_demodevice01", "GET")))).toBe(403);
    const cfg = await import("@/app/api/v1/agent/config/route");
    expect([401, 403]).toContain(await status(cfg.GET(signedAgent("/api/v1/agent/config", "", Buffer.alloc(32, 5), "rel_github", "GET"))));
  });
});

describe("4/5. members cannot reach each other or forge signals", () => {
  it("only the allowlisted Discord server can create members", async () => {
    await expect(resolveMember(db, "900000000000000199", { create: true, guildId: "999999999999999999" })).rejects.toMatchObject({ status: 403 });
  });

  it("rejects a fake signal id and another member's position id", async () => {
    const a = await resolveMember(db, "900000000000000101", { create: true, guildId: await guild() });
    const b = await resolveMember(db, "900000000000000102", { create: true, guildId: await guild() });
    const now = new Date();
    const fill = { price: "100", quantity: "5", fees: "0", fill_at: now.toISOString().replace(/\.\d{3}Z$/, "Z") };
    const code = async (p: Promise<unknown>) => p.then(() => "OK", (e) => (e instanceof HttpError ? `${e.status}:${e.code}` : String(e)));
    expect(await code(recordPositionEvent(db, a.id, { client_idempotency_key: "adv-fake-signal-1", event_type: "OPEN", signal_id: "sig_000000000000000000000000",
      fill, source: "discord" }, now, true))).toBe("422:UNKNOWN_SIGNAL");
    const mine = await recordPositionEvent(db, a.id, { client_idempotency_key: "adv-a-open-1", event_type: "OPEN", symbol: "DMSOFT1", fill, source: "discord" }, now, true);
    expect(await code(recordPositionEvent(db, b.id, { client_idempotency_key: "adv-b-sell-1", event_type: "PARTIAL_EXIT", position_id: mine.position.position_id,
      fill: { ...fill, quantity: "1" }, source: "discord" }, now, true))).toBe("404:NOT_FOUND");
  });

  it("concurrent duplicate submissions persist exactly one event", async () => {
    const m = await resolveMember(db, "900000000000000103", { create: true, guildId: await guild() });
    const now = new Date();
    const ev = { client_idempotency_key: "adv-concurrent-1", event_type: "OPEN" as const, symbol: "DMTEC1",
      fill: { price: "50", quantity: "3", fees: "0", fill_at: now.toISOString().replace(/\.\d{3}Z$/, "Z") }, source: "discord" as const };
    const results = await Promise.allSettled(Array.from({ length: 8 }, () => recordPositionEvent(db, m.id, ev, now, true)));
    const ok = results.filter((r) => r.status === "fulfilled") as PromiseFulfilledResult<{ status: string }>[];
    expect(ok.length).toBe(8);
    expect(ok.filter((r) => r.value.status === "persisted").length).toBe(1);
    const n = await db.asMember(m.id, (sql) => sql.query<{ n: number }>("SELECT count(*)::int AS n FROM apex.position_events"));
    expect(n[0]!.n).toBe(1);
  });
});

describe("8/13. SSRF and secret echo", () => {
  it("only accepts discord.com webhook URLs (no internal hosts) and never echoes stored secrets", async () => {
    for (const url of ["https://127.0.0.1/api/webhooks/1/x", "http://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz0123456789",
      "https://discord.com.evil.example/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz0123456789", "https://169.254.169.254/latest/meta-data"]) {
      await expect(saveIntegration(db, ownerId, "webhook", { url })).rejects.toMatchObject({ status: 422 });
    }
    await saveIntegration(db, ownerId, "fred", { api_key: "0123456789abcdef0123456789abcdef" });
    const { GET } = await import("@/app/api/v1/admin/settings/route");
    const text = await (await GET(owner("/api/v1/admin/settings"))).text();
    expect(text).not.toContain("0123456789abcdef0123456789abcdef");
    expect(JSON.parse(text).integrations.find((i: { provider: string }) => i.provider === "fred").fields[0].configured).toBe(true);
    const audit = JSON.stringify(await db.query("SELECT * FROM apex.audit_events"));
    expect(audit).not.toContain("0123456789abcdef0123456789abcdef");
  });
});

describe("7. rendering of untrusted text", () => {
  it("the dashboard never injects raw HTML", () => {
    const files: string[] = [];
    const walk = (d: string) => {
      for (const f of readdirSync(d)) {
        const p = join(d, f);
        if (statSync(p).isDirectory()) walk(p);
        else if (/\.(tsx?|jsx?)$/.test(f)) files.push(p);
      }
    };
    walk(join(__dirname, "../../src"));
    const offenders = files.filter((f) => /dangerouslySetInnerHTML|innerHTML\s*=|eval\(|new Function\(/.test(readFileSync(f, "utf8")));
    expect(offenders).toEqual([]);
  });
});
