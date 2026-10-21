// Cross-language connected mode (acceptance C/E/G): the REAL Pi worker code (agent client, pairing, worker jobs, outbox
// bridge, model sync, notice router; production wiring via `build_worker`) against the REAL agent route handlers and
// migrations. The Pi's own suite only ever talks to its Python fake of this API; this proves the two halves agree.
// DEMO mode, TEST-ONLY secrets. Opt-in because it needs the Python environment:
//   (cd apps/pi-worker && uv sync --all-extras) && APEX_PY_E2E=1 pnpm --filter @apex/web exec vitest run tests/integration/pi-agent.test.ts
import { execFile } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

const HMAC = Buffer.alloc(32, 7).toString("base64"); // test-only shared agent secret
process.env.APEX_MODE = "DEMO";
process.env.AGENT_HMAC_SECRET = HMAC;
process.env.APEX_CONFIG_ENCRYPTION_KEY = Buffer.alloc(32, 3).toString("base64");
process.env.APEX_SESSION_SECRET = Buffer.alloc(32, 4).toString("base64");

const { openTestDb } = await import("../support/test-db");
const { migrate } = await import("@/server/db/migrate");
const { __setDbForTests } = await import("@/server/db");
const { demoClock } = await import("@/server/context");
const { ensureDemoSeed } = await import("@/server/services/demo");
const { setDeviceStatus, startPairing } = await import("@/server/services/pairing");
const { modelsPanel } = await import("@/server/services/dashboard");
const { saveIntegration, secretVersions } = await import("@/server/services/settings");

type Handler = (req: Request, ctx: { params: Promise<Record<string, string>> }) => Promise<Response>;
const AGENT = "/api/v1/agent";
const simple: Record<string, Record<string, Handler>> = {
  pair: await import("@/app/api/v1/agent/pair/route"),
  heartbeat: await import("@/app/api/v1/agent/heartbeat/route"),
  config: await import("@/app/api/v1/agent/config/route"),
  work: await import("@/app/api/v1/agent/work/route"),
  notices: await import("@/app/api/v1/agent/notices/route"),
  "notifications/ack": await import("@/app/api/v1/agent/notifications/ack/route"),
  "models/manifest": await import("@/app/api/v1/agent/models/manifest/route"),
  "models/activation": await import("@/app/api/v1/agent/models/activation/route"),
  positions: await import("@/app/api/v1/agent/positions/route"),
  "positions/history": await import("@/app/api/v1/agent/positions/history/route"),
  "positions/events": await import("@/app/api/v1/agent/positions/events/route"),
  "positions/assessments": await import("@/app/api/v1/agent/positions/assessments/route"),
  members: await import("@/app/api/v1/agent/members/route"),
  secrets: await import("@/app/api/v1/agent/secrets/route"),
  "secrets/ack": await import("@/app/api/v1/agent/secrets/ack/route"),
  signals: await import("@/app/api/v1/agent/signals/route"),
  "signals/open": await import("@/app/api/v1/agent/signals/open/route"),
  "signals/events": await import("@/app/api/v1/agent/signals/events/route"),
  "scan-runs": await import("@/app/api/v1/agent/scan-runs/route"),
  observations: await import("@/app/api/v1/agent/observations/route"),
  catalysts: await import("@/app/api/v1/agent/catalysts/route"),
} as never;
const artifact = (await import("@/app/api/v1/agent/models/[id]/artifact/route")) as unknown as Record<string, Handler>;

function route(pathname: string, method: string): [Handler | undefined, Record<string, string>] {
  if (!pathname.startsWith(`${AGENT}/`)) return [undefined, {}];
  const rest = pathname.slice(AGENT.length + 1);
  const m = /^models\/([^/]+)\/artifact$/.exec(rest);
  if (m) return [artifact[method], { id: decodeURIComponent(m[1]!) }];
  return [simple[rest]?.[method], {}];
}

type Db = Awaited<ReturnType<typeof openTestDb>>;
let db: Db;
let server: Server;
let base = "";
let ownerId = "";
let demoNow = "";
const seed = JSON.parse(readFileSync(fileURLToPath(new URL("../../../../fixtures/demo/web-seed.json", import.meta.url)), "utf8")) as {
  trusted_model_keys: Record<string, string>; members: Record<string, string>; guild_id: string };
const worker = fileURLToPath(new URL("../../../pi-worker", import.meta.url));
const driver = join(worker, "tests", "support", "real_server_driver.py");
const work = mkdtempSync(join(tmpdir(), "apex-pi-agent-"));
const stateA = join(work, "pi-a");
/** every request the Pi sent, as the server received it (path, status, parsed body) */
const seen: { method: string; path: string; status: number; body: Record<string, unknown> | null }[] = [];

beforeAll(async () => {
  if (!process.env.APEX_PY_E2E) return;
  db = await openTestDb();
  await migrate(db);
  __setDbForTests(db);
  await ensureDemoSeed(db);
  ownerId = (await db.query<{ id: string }>("SELECT id FROM apex.app_users WHERE role='owner'"))[0]!.id;
  demoNow = ((await demoClock(db)) ?? new Date()).toISOString().replace(/\.\d{3}Z$/, "Z");
  server = createServer(async (req, res) => {
    const chunks: Buffer[] = [];
    for await (const c of req) chunks.push(c as Buffer);
    const raw = Buffer.concat(chunks);
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    const [handler, params] = route(url.pathname, req.method ?? "GET");
    if (!handler) { res.writeHead(404, { "content-type": "application/json" }).end('{"error":{"code":"NO_ROUTE"}}'); return; }
    const headers = new Headers();
    for (const [k, v] of Object.entries(req.headers)) if (typeof v === "string") headers.set(k, v);
    const out = await handler(new Request(`http://127.0.0.1${req.url}`, { method: req.method, headers,
      body: ["GET", "HEAD"].includes(req.method ?? "GET") ? undefined : raw }), { params: Promise.resolve(params) });
    let body: Record<string, unknown> | null = null;
    try { body = raw.length ? JSON.parse(raw.toString("utf8")) : null; } catch { body = null; }
    seen.push({ method: req.method ?? "GET", path: url.pathname.slice(AGENT.length + 1), status: out.status, body });
    res.writeHead(out.status, { "content-type": "application/json" }).end(Buffer.from(await out.arrayBuffer()));
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  const addr = server.address();
  base = `http://127.0.0.1:${typeof addr === "object" && addr ? addr.port : 0}`;
}, 240_000);

afterAll(() => server?.close());

const run = promisify(execFile);
// async on purpose: the route handlers live in this process, so the event loop must keep serving while Python runs
async function py(args: string[], state = stateA): Promise<{ code: number; out: string }> {
  const env = { ...process.env, APEX_MODE: "DEMO", APEX_STATE_DIR: state, APEX_ENV_FILE: join(work, "no.env"),
    AGENT_HMAC_SECRET: HMAC, APEX_MODEL_TRUSTED_KEYS: JSON.stringify(seed.trusted_model_keys), APEX_API_BASE_URL: "" };
  try {
    const { stdout, stderr } = await run("uv", ["run", "--no-sync", ...args], { cwd: worker, env, encoding: "utf8" });
    return { code: 0, out: `${stdout}\n${stderr}` };
  } catch (e) {
    const err = e as { code: number; stdout: string; stderr: string; message: string };
    return { code: err.code, out: `${err.message}\n${err.stdout}\n${err.stderr}` };
  }
}

async function step(name: string, state = stateA, extra: string[] = []): Promise<Record<string, unknown>> {
  const r = await py(["python", "-I", driver, name, "--now", demoNow, ...extra], state);
  expect(r.code, r.out).toBe(0);
  const line = r.out.split("\n").find((l) => l.startsWith("{\"")) ?? "";
  expect(line, r.out).not.toBe("");
  const out = JSON.parse(line) as Record<string, unknown>;
  expect(out.error, r.out).toBeUndefined();
  return out;
}

const sent = (path: string) => seen.filter((s) => s.path === path);
let agentId = "";

describe.skipIf(!process.env.APEX_PY_E2E)("Pi connected mode <-> real agent API", () => {
  it("pairs with an Ed25519 proof; heartbeat is pending until the owner approves", async () => {
    const { code } = await startPairing(db, ownerId);
    const r = await py(["apex", "pair", code, "--server", base, "--name", "itest-pi"]);
    expect(r.code, r.out).toBe(0);
    agentId = /Agent id:\s+(agt_[A-Za-z0-9]+)/.exec(r.out)?.[1] ?? "";
    expect(agentId, r.out).not.toBe("");
    const fp = /Device fingerprint:\s+(\S+)/.exec(r.out)?.[1];
    const dev = await db.query<{ status: string; fingerprint: string; device_name: string }>(
      "SELECT status, fingerprint, device_name FROM apex.agent_devices WHERE id=$1", [agentId]);
    expect(dev[0]).toEqual({ status: "pending_approval", fingerprint: fp, device_name: "itest-pi" });
    const pending = await step("heartbeat");
    expect(pending.device_status).toBe("pending_approval");
    // the code is single use
    const again = await py(["apex", "pair", code, "--server", base, "--name", "itest-pi"]);
    expect(again.code).toBe(1);
    expect(again.out).toMatch(/PAIRING_CODE_INVALID/);
  }, 120_000);

  it("after approval: config, heartbeat and work parse on the Pi", async () => {
    await setDeviceStatus(db, ownerId, agentId, "approve");
    const hb = await step("heartbeat");
    expect(hb.device_status).toBe("active");
    const cfg = hb.config as Record<string, unknown>;
    expect(cfg.raw_keys).toEqual(expect.arrayContaining(["alerts_stopped", "budgets", "secret_versions", "sla", "strategies", "training"]));
    expect(cfg.budgets).toMatchObject({ actions_monthly_minutes: 1500 });
    expect((cfg.training as Record<string, unknown>).champions).toEqual(expect.arrayContaining([expect.objectContaining({ strategy_id: "redteam_planted_alpha" })]));
    expect(cfg.risk_min_quality).toBe(70);
    expect(hb.secret_versions).toEqual(expect.any(Object));
    const beats = await db.query<{ n: number }>("SELECT count(*)::int AS n FROM apex.agent_heartbeats WHERE agent_id=$1", [agentId]);
    expect(beats[0]!.n).toBeGreaterThanOrEqual(2);
    const w = await step("work");
    const positions = w.work_positions as { user_id: string; position_id: string }[];
    expect(positions.length).toBeGreaterThan(0);
    for (const p of positions) expect(p.user_id).toMatch(/^[0-9a-f-]{36}$/);
    const monitored = w.monitored as { user_id: string; position_id: string; state: string }[];
    expect(w.missing_user_ids).toBe(0);
    expect(monitored.length).toBeGreaterThan(0); // server PositionSummary -> Pi ledger projection
  }, 120_000);

  it("market credentials reach the Pi only as an X25519 envelope for this device, and are acked by version", async () => {
    await saveIntegration(db, ownerId, "alpaca", { key_id: "PKTESTKEY1234567ABCD", secret_key: "TestSecretValueDoNotUse0123456789abcdef" });
    const version = (await secretVersions(db)).alpaca!;
    expect(version).toBeGreaterThan(0);
    const hb = await step("heartbeat");
    expect(hb.secret_versions).toMatchObject({ alpaca: version });
    expect(hb.market_credentials).toEqual({ status: "OK", version, key_id_suffix: "ABCD" });
    const env = sent("secrets").at(-1)!;
    expect(env.status).toBe(200);
    const delivery = await db.query<{ acked_at: Date | null; secret_version: number }>(
      "SELECT acked_at, secret_version FROM apex.secret_deliveries WHERE device_id=$1 ORDER BY issued_at DESC LIMIT 1", [agentId]);
    expect(delivery[0]!.acked_at).not.toBeNull();
    const dev = await db.query<{ v: number }>("SELECT secret_version_ack AS v FROM apex.agent_devices WHERE id=$1", [agentId]);
    expect(dev[0]!.v).toBe(version);
    // the plaintext never travelled: no request or response body the Pi exchanged contains it
    expect(JSON.stringify(seen)).not.toContain("TestSecretValueDoNotUse");
  }, 120_000);

  it("model sync: fetches, verifies (Pi-held key), installs and acks the DEMO champion", async () => {
    const m = await step("models");
    const rep = m.report as { activated: string[]; rejected: Record<string, string> };
    const champs = await db.query<{ id: string; strategy_id: string }>(
      "SELECT id, strategy_id FROM apex.model_versions WHERE status='champion' AND is_demo ORDER BY strategy_id");
    expect(rep.rejected).toEqual({});
    expect(rep.activated.sort()).toEqual(champs.map((c) => c.id).sort());
    expect(m.active).toEqual(Object.fromEntries(champs.map((c) => [c.strategy_id, c.id])));
    const acts = await db.query<{ model_id: string; status: string }>("SELECT model_id, status FROM apex.model_activations WHERE agent_id=$1", [agentId]);
    expect(acts.map((a) => a.status)).toEqual(champs.map(() => "activated"));
    const panel = await modelsPanel(db, true, new Date(demoNow)) as { activations: { agent_id: string; status: string }[] };
    expect(panel.activations.find((a) => a.agent_id === agentId)).toMatchObject({ status: "activated" });
    const again = await step("models"); // restart: re-verified from disk, nothing downloaded twice
    expect((again.report as { unchanged: string[] }).unchanged.sort()).toEqual(champs.map((c) => c.id).sort());
    expect(sent("models/activation")).toHaveLength(champs.length);
  }, 180_000);

  it("a tampered registry artifact is refused, acked as rejected and alerted privately; nothing is loaded", async () => {
    const champ = (await db.query<{ id: string }>("SELECT id FROM apex.model_versions WHERE status='champion' AND is_demo LIMIT 1"))[0]!;
    const orig = (await db.query<{ artifact: Uint8Array }>("SELECT artifact FROM apex.model_artifacts WHERE model_id=$1", [champ.id]))[0]!.artifact;
    const bad = Buffer.from(orig);
    const mid = Math.floor(bad.length / 2);
    bad[mid] = bad[mid]! ^ 0x01; // same size, different bytes: only the signed sha256 can tell
    await db.query("UPDATE apex.model_artifacts SET artifact=$2 WHERE model_id=$1", [champ.id, bad]);
    try {
      // a second Pi state dir with the same identity but no installed model (e.g. a fresh SD card)
      const stateB = join(work, "pi-b");
      mkdirSync(stateB, { mode: 0o700 });
      for (const f of ["device_keys.json", "agent.json", "component_secret"]) cpSync(join(stateA, f), join(stateB, f));
      const m = await step("models", stateB);
      const rep = m.report as { activated: string[]; rejected: Record<string, string> };
      expect(rep.activated).toEqual([]);
      expect(rep.rejected[champ.id]).toMatch(/CHECKSUM|SHA|INTEGRITY|SIGNATURE/);
      expect(m.active).toEqual({});
      const ack = await db.query<{ status: string; detail: string }>(
        "SELECT status, detail FROM apex.model_activations WHERE agent_id=$1 AND model_id=$2 ORDER BY reported_at DESC LIMIT 1", [agentId, champ.id]);
      expect(ack[0]!.status).toBe("rejected");
      // the alert went to the server outbox (#system-status only), not to a local fallback
      expect(m.local_outbox).toEqual([]);
      const alert = await db.query<{ kind: string; route_json: { type: string; channel_key: string } }>(
        "SELECT kind, route_json FROM apex.notification_outbox WHERE payload_json->'payload'->>'alert' = 'MODEL_REJECTED' ORDER BY created_at DESC LIMIT 1");
      expect(alert[0]).toEqual({ kind: "SYSTEM_ALERT", route_json: { type: "CHANNEL", channel_key: "system_status" } });
    } finally {
      await db.query("UPDATE apex.model_artifacts SET artifact=$2 WHERE model_id=$1", [champ.id, Buffer.from(orig)]);
    }
  }, 180_000);

  it("notices go to the server (#system-status only); outbox rows are leased, delivered and acked per item", async () => {
    const n = await step("notice");
    const noticeKey = n.event_key as string;
    expect(n).toMatchObject({ queued: true, route: "server", local_outbox: [] });
    expect(n.refused).toEqual({ status: 422, code: "NOT_A_SYSTEM_NOTICE" });
    const notice = await db.query<{ id: string; kind: string; state: string; route_json: Record<string, string> }>(
      "SELECT id, kind, state, route_json FROM apex.notification_outbox WHERE event_key=$1", [noticeKey]);
    expect(notice[0]).toMatchObject({ kind: "SYSTEM_ALERT", state: "pending", route_json: { type: "CHANNEL", channel_key: "system_status" } });

    // a private DM for a member, as the assessment route queues it (a real, renderable assessment from the seed), so the
    // channel/DM distinction below is about the route, not about whether the payload happens to render
    const snap = (await db.query<{ user_id: string; discord_user_id: string; assessment_json: Record<string, unknown> }>(
      `SELECT s.user_id, u.discord_user_id, s.assessment_json FROM apex.position_snapshots s JOIN apex.app_users u ON u.id = s.user_id
       ORDER BY s.asof_at DESC LIMIT 1`))[0]!;
    const member = { id: snap.user_id, discord_user_id: snap.discord_user_id };
    const dmKey = `itest:dm:${Date.now()}`;
    const dmIntent = { event_key: dmKey, kind: "POSITION_UPDATE", priority: "NORMAL", is_demo: true, created_at: demoNow,
      route: { type: "DM", user_id: member.id }, payload: { assessment: snap.assessment_json } };
    await db.query(
      `INSERT INTO apex.notification_outbox(event_key, user_id, route_json, kind, priority, payload_json, is_demo)
       VALUES ($1,$2,$3::jsonb,'POSITION_UPDATE','NORMAL',$4::jsonb,true)`, [dmKey, member.id, JSON.stringify(dmIntent.route), JSON.stringify(dmIntent)]);

    const w = await step("work");
    const bridge = w.bridge as { event_key: string; route_type: string; acked_state: string | null }[];
    expect(bridge.find((b) => b.event_key === noticeKey)).toMatchObject({ route_type: "CHANNEL", acked_state: null });
    expect(bridge.find((b) => b.event_key === dmKey)).toMatchObject({ route_type: "DM", acked_state: null });
    const localDm = (w.local_outbox as { event_key: string; route: { type: string; user_id: string; discord_user_id: string } }[])
      .find((r) => r.event_key === dmKey)!;
    expect(localDm.route.type).toBe("DM"); // stays private
    expect(localDm.route.discord_user_id).toBe(member.discord_user_id);
    expect(localDm.route.user_id).not.toBe(member.id); // rewritten to the Pi's local member id

    const d = await step("deliver");
    expect(d.delivered).toEqual(expect.arrayContaining([noticeKey, dmKey]));
    expect(d.renderable).toMatchObject({ [noticeKey]: true, [dmKey]: true }); // both render: only the route decides
    const rows = await db.query<{ event_key: string; state: string; discord_message_id: string | null; rendered_json: unknown }>(
      "SELECT event_key, state, discord_message_id, rendered_json FROM apex.notification_outbox WHERE event_key = ANY($1)", [[noticeKey, dmKey]]);
    const byKey = Object.fromEntries(rows.map((r) => [r.event_key, r]));
    expect(byKey[noticeKey]).toMatchObject({ state: "sent", discord_message_id: expect.stringMatching(/^13\d+$/) });
    expect(byKey[noticeKey]!.rendered_json).toMatchObject({ embeds: expect.any(Array) });
    expect(byKey[dmKey]).toMatchObject({ state: "sent", rendered_json: null });
    // what the Pi actually sent: `rendered` on the channel ack, never on the DM ack
    const acks = sent("notifications/ack").flatMap((s) => (s.body?.acks as { outbox_id: string; rendered?: unknown }[]) ?? []);
    const ids = Object.fromEntries((await db.query<{ id: string; event_key: string }>(
      "SELECT id, event_key FROM apex.notification_outbox WHERE event_key = ANY($1)", [[noticeKey, dmKey]])).map((r) => [r.event_key, r.id]));
    expect(acks.find((a) => a.outbox_id === ids[noticeKey])?.rendered).toBeDefined();
    const dmAck = acks.find((a) => a.outbox_id === ids[dmKey]);
    expect(dmAck).toBeDefined();
    expect(dmAck!.rendered).toBeUndefined();
    // a re-run acks nothing twice
    const before = sent("notifications/ack").length;
    const again = await step("deliver");
    expect(again.delivered).toEqual([]);
    expect(sent("notifications/ack").length).toBe(before);
  }, 180_000);

  it("the real engine publishes scans, signals, events, quotes and assessments through RemoteSink", async () => {
    const count = async (sql: string) => (await db.query<{ n: number }>(sql))[0]!.n;
    const scansBefore = await count("SELECT count(*)::int AS n FROM apex.scan_runs");
    const sigBefore = await count("SELECT count(*)::int AS n FROM apex.signals");
    const evBefore = await count("SELECT count(*)::int AS n FROM apex.signal_events");
    const snapBefore = await count("SELECT count(*)::int AS n FROM apex.position_snapshots");
    const e = await step("engine", stateA, ["--sessions", "3"]);
    expect((e.published as string[]).length).toBeGreaterThan(0); // deterministic DEMO market: the 2026-10-06..08 setups
    expect(e.spool).toEqual({ pending: 0, rejected: 0 }); // nothing refused by the server, nothing stuck
    const posts = seen.filter((s) => s.method === "POST" && ["scan-runs", "signals", "signals/events", "observations", "positions/assessments"].includes(s.path));
    expect(posts.filter((p) => p.status !== 200)).toEqual([]);
    expect(await count("SELECT count(*)::int AS n FROM apex.scan_runs")).toBe(scansBefore + 3);
    expect(sent("observations").length).toBeGreaterThan(0);
    const firstDay = (e.sessions as string[])[0]!;
    const fresh = await db.query<{ n: number }>("SELECT count(*)::int AS n FROM apex.latest_quotes WHERE ts_received >= $1 AND is_demo", [firstDay]);
    expect(fresh[0]!.n).toBeGreaterThan(0); // the Pi's quotes replaced the seed's (latest-quote upsert)
    for (const id of e.published as string[]) {
      const rows = await db.query<{ id: string }>("SELECT id FROM apex.signals WHERE id=$1", [id]);
      expect(rows, id).toHaveLength(1);
    }
    expect(await count("SELECT count(*)::int AS n FROM apex.signals")).toBe(sigBefore + (e.published as string[]).length);
    expect(await count("SELECT count(*)::int AS n FROM apex.signal_events")).toBeGreaterThan(evBefore);
    expect(sent("positions/assessments").length).toBeGreaterThan(0);
    expect(await count("SELECT count(*)::int AS n FROM apex.position_snapshots")).toBeGreaterThan(snapBefore); // stored, RLS-scoped
  }, 600_000);

  it("connected bot backends: ledger writes, corrections, owner-scoped history, prefs and deletion", async () => {
    const fresh = "100000000000000077";
    const b = await step("bot", stateA, ["--members", `${seed.members.A},${seed.members.B},${fresh}`, "--guild", seed.guild_id]);
    expect(b.seeded_positions).toBeGreaterThan(0);
    expect((b.seeded_events as unknown[]).length).toBeGreaterThan(0);
    expect(b.cross_member).toBe("not_found"); // RLS: another member's position id is simply not found
    expect(b.record).toEqual({ first: "persisted", again: "duplicate", same_position: true });
    expect(b.position_for_key).toBe(true);
    const corrected = b.corrected as { status: string; avg_cost: string; events: { event_type: string; fill?: { price: string };
      correction?: { void: boolean; replacement?: { price: string } } }[] };
    expect(corrected.status).toBe("persisted");
    expect(Number(corrected.avg_cost)).toBeCloseTo((100.75 * 4 + 0.5) / 4, 4);
    expect(corrected.events.map((e) => e.event_type)).toEqual(["OPEN", "CORRECT"]);
    expect(corrected.events[0]!.fill!.price).toBe("101.25"); // the original stays in the history
    expect(corrected.events[1]!.correction).toMatchObject({ void: false, replacement: { price: "100.75" } });
    expect(b.deleted).toBeGreaterThanOrEqual(1);
    expect(b.after_delete).toEqual([]);
    expect(b.member_spool).toBe(0); // prefs reached the server directly (nothing spooled)
    const u = await db.query<{ dm_opt_in: boolean }>("SELECT dm_opt_in FROM apex.app_users WHERE discord_user_id=$1", [fresh]);
    expect(u[0]!.dm_opt_in).toBe(false); // deletion turned DMs off again in the cloud copy
    const left = await db.query<{ n: number }>(
      "SELECT count(*)::int AS n FROM apex.positions p JOIN apex.app_users u ON u.id = p.user_id WHERE u.discord_user_id=$1", [fresh]);
    expect(left[0]!.n).toBe(0);
  }, 180_000);

  it("a revoked device is cut off (403) and every Pi request was signed correctly until then", async () => {
    await setDeviceStatus(db, ownerId, agentId, "revoke");
    const hb = await step("heartbeat");
    expect(hb.server_component).toMatchObject({ detail: "DEVICE_REVOKED" });
    expect(sent("heartbeat").at(-1)!.status).toBe(403);
    // no request before the revocation failed authentication: signing (including query strings) matches the server
    const authFailures = seen.filter((s) => s.status === 401);
    expect(authFailures).toEqual([]);
  }, 120_000);
});
