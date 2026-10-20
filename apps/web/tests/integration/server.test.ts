import { createHash, createPrivateKey, generateKeyPairSync, randomBytes, sign as edSign } from "node:crypto";
import { beforeAll, describe, expect, it } from "vitest";
import hmacVectors from "../../../../packages/quant/vectors/hmac.json";

process.env.APEX_MODE = "DEMO";
process.env.AGENT_HMAC_SECRET = Buffer.alloc(32, 7).toString("base64");
process.env.APEX_CONFIG_ENCRYPTION_KEY = Buffer.alloc(32, 3).toString("base64");
process.env.APEX_SESSION_SECRET = Buffer.alloc(32, 4).toString("base64");

const { openTestDb } = await import("../support/test-db");
const { migrate } = await import("@/server/db/migrate");
const { __setDbForTests } = await import("@/server/db");
const { authenticateAgent } = await import("@/server/services/agents");
const { ensureDemoSeed } = await import("@/server/services/demo");
const { resolveMember } = await import("@/server/services/members");
const { getPosition, listPositions, recordPositionEvent } = await import("@/server/services/positions");
const { listIntegrations, readIntegrationSecrets, saveIntegration } = await import("@/server/services/settings");
const { completePairing, serverNonceFor, setDeviceStatus, startPairing, normalizeCode } = await import("@/server/services/pairing");
const { ingestSignalEvents } = await import("@/server/services/ingest");
const { registerCandidate } = await import("@/server/services/models");
const { signRequest } = await import("@/server/security/hmac");
const { canonicalJson } = await import("@/server/security/canonical");
const { HttpError } = await import("@/server/http/responses");

type Db = Awaited<ReturnType<typeof openTestDb>>;
let db: Db;
let ownerId: string;

beforeAll(async () => {
  db = await openTestDb();
  await migrate(db);
  __setDbForTests(db);
  await ensureDemoSeed(db);
  ownerId = (await db.query<{ id: string }>("SELECT id FROM apex.app_users WHERE role='owner'"))[0]!.id;
}, 240_000);

const SECRET = Buffer.alloc(32, 7);

function signed(path: string, body: string, opts: { agent?: string; ts?: number; nonce?: string; secret?: Buffer; method?: string } = {}): Request {
  const agent = opts.agent ?? "agt_demodevice01";
  const ts = String(opts.ts ?? Math.floor(Date.now() / 1000));
  const nonce = opts.nonce ?? randomBytes(18).toString("base64url");
  const method = opts.method ?? "POST";
  const { bodySha256, signature } = signRequest(opts.secret ?? SECRET, agent, method, path, ts, nonce, body);
  return new Request(`http://localhost${path}`, { method, body: method === "GET" ? undefined : body, headers: {
    "x-apex-agent-id": agent, "x-apex-timestamp": ts, "x-apex-nonce": nonce, "x-apex-body-sha256": bodySha256, "x-apex-signature": signature } });
}

async function code(p: Promise<unknown>): Promise<string> {
  try {
    await p;
    return "OK";
  } catch (e) {
    return e instanceof HttpError ? `${e.status}:${e.code}` : `ERR:${(e as Error).message}`;
  }
}

describe("schema", () => {
  it("every apex table has RLS enabled", async () => {
    const r = await db.query<{ n: number }>("SELECT count(*)::int AS n FROM pg_tables WHERE schemaname='apex' AND NOT rowsecurity");
    expect(r[0]!.n).toBe(0);
  });
  it("event tables are append-only", async () => {
    expect(await code(db.query("UPDATE apex.signal_events SET cause='x'"))).toMatch(/append-only/);
    expect(await code(db.query("UPDATE apex.audit_events SET action='x'"))).toMatch(/append-only/);
    expect(await code(db.query("DELETE FROM apex.audit_events"))).toMatch(/retained/);
  });
  it("JSON columns hold objects, never double-encoded JSON strings (driver regression)", async () => {
    const r = await db.query<{ n: number }>(
      `SELECT (SELECT count(*) FROM apex.signals WHERE jsonb_typeof(immutable_snapshot) <> 'object')
            + (SELECT count(*) FROM apex.notification_outbox WHERE jsonb_typeof(payload_json) <> 'object')
            + (SELECT count(*) FROM apex.scan_runs WHERE jsonb_typeof(metrics_json) <> 'object')
            + (SELECT count(*) FROM apex.model_versions WHERE jsonb_typeof(manifest_json) <> 'object') AS n`);
    expect(Number(r[0]!.n)).toBe(0);
  });
  it("UNVALIDATED signals cannot carry a probability (DB constraint)", async () => {
    const r = await db.query<{ n: number }>("SELECT count(*)::int AS n FROM apex.signals WHERE confidence_status='UNVALIDATED' AND confidence_probability IS NOT NULL");
    expect(r[0]!.n).toBe(0);
  });
});

describe("DEMO seed through production ingestion", () => {
  it("loaded signals, scans, catalysts, research and a signed DEMO champion", async () => {
    const q = async (sql: string) => (await db.query<{ n: number }>(sql))[0]!.n;
    expect(await q("SELECT count(*)::int AS n FROM apex.signal_heads")).toBeGreaterThan(5);
    expect(await q("SELECT count(*)::int AS n FROM apex.scan_runs")).toBeGreaterThan(10);
    expect(await q("SELECT count(*)::int AS n FROM apex.catalysts")).toBeGreaterThan(10);
    expect(await q("SELECT count(*)::int AS n FROM apex.backtest_runs")).toBeGreaterThan(3);
    expect(await q("SELECT count(*)::int AS n FROM apex.model_versions WHERE status='champion' AND is_demo")).toBe(1);
    expect(await q("SELECT count(*)::int AS n FROM apex.signals WHERE NOT is_demo")).toBe(0);
  });
  it("owner sees only their own positions; other members are aggregate-only", async () => {
    const mine = await listPositions(db, ownerId, true);
    expect(mine.length).toBeGreaterThan(0);
    const all = await db.query<{ n: number }>("SELECT count(*)::int AS n FROM apex.positions");
    expect(all[0]!.n).toBeGreaterThan(mine.length);
  });
});

describe("member privacy (RLS + service checks)", () => {
  it("member B cannot read, list or mutate member A's position", async () => {
    const a = await resolveMember(db, "200000000000000001", { create: true });
    const b = await resolveMember(db, "200000000000000002", { create: true });
    const now = new Date();
    const fill = { price: "180", quantity: "20", fees: "0", fill_at: new Date(now.getTime() - 3600_000).toISOString().replace(/\.\d{3}Z$/, "Z") };
    const res = await recordPositionEvent(db, a.id, { client_idempotency_key: "test-a-open-0001", event_type: "OPEN", symbol: "DMSEMI1", fill, source: "dashboard" }, now, true);
    const pid = res.position.position_id;
    expect(await code(getPosition(db, b.id, pid))).toBe("404:NOT_FOUND");
    expect((await listPositions(db, b.id, true)).map((p) => p.position_id)).not.toContain(pid);
    expect(await code(recordPositionEvent(db, b.id, { client_idempotency_key: "test-b-close-0001", event_type: "CLOSE", position_id: pid,
      fill: { ...fill, price: "1" }, source: "dashboard" }, now, true))).toBe("404:NOT_FOUND");
    // raw SQL as member B through RLS
    const rows = await db.asMember(b.id, (sql) => sql.query("SELECT id FROM apex.positions WHERE id = $1", [pid]));
    expect(rows).toHaveLength(0);
    expect(await code(db.asMember(b.id, (sql) => sql.query(
      "INSERT INTO apex.position_events(user_id, position_id, seq, event_type, source, client_idempotency_key) VALUES ($1,$2,99,'WATCH','dashboard','forged-0001')",
      [a.id, pid])))).toMatch(/row-level security|violates/);
    // duplicate submission (double click) does not create a second fill
    const again = await recordPositionEvent(db, a.id, { client_idempotency_key: "test-a-open-0001", event_type: "OPEN", symbol: "DMSEMI1", fill, source: "dashboard" }, now, true);
    expect(again.status).toBe("duplicate");
    expect((await getPosition(db, a.id, pid)).summary.total_quantity).toBe("20");
    // oversell is refused by the ledger
    expect(await code(recordPositionEvent(db, a.id, { client_idempotency_key: "test-a-sell-0001", event_type: "PARTIAL_EXIT", position_id: pid,
      fill: { ...fill, quantity: "25" }, source: "dashboard" }, now, true))).toBe("422:OVERSELL");
  });
});

describe("agent request signing", () => {
  it("TS signer matches the shared vectors", () => {
    const secret = Buffer.from(hmacVectors.secret_b64, "base64");
    for (const c of hmacVectors.cases) {
      expect(signRequest(secret, c.agent_id, c.method, c.path, c.timestamp, c.nonce, c.body).signature).toBe(c.signature);
    }
  });
  it("accepts a valid request once and rejects replay, tamper, skew, wrong secret and revoked devices", async () => {
    const body = '{"ping":1}';
    const req = signed("/api/v1/agent/heartbeat", body, { nonce: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1" });
    expect(await code(authenticateAgent(db, req, new TextEncoder().encode(body)))).toBe("OK");
    const replay = signed("/api/v1/agent/heartbeat", body, { nonce: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1" });
    expect(await code(authenticateAgent(db, replay, new TextEncoder().encode(body)))).toBe("409:NONCE_REPLAYED");
    const tampered = signed("/api/v1/agent/heartbeat", body);
    expect(await code(authenticateAgent(db, tampered, new TextEncoder().encode('{"ping":2}')))).toBe("401:UNAUTHENTICATED");
    const skewed = signed("/api/v1/agent/heartbeat", body, { ts: Math.floor(Date.now() / 1000) - 180 });
    expect(await code(authenticateAgent(db, skewed, new TextEncoder().encode(body)))).toBe("401:CLOCK_SKEW");
    const wrong = signed("/api/v1/agent/heartbeat", body, { secret: Buffer.alloc(32, 9) });
    expect(await code(authenticateAgent(db, wrong, new TextEncoder().encode(body)))).toBe("401:UNAUTHENTICATED");
    const otherPath = signed("/api/v1/agent/heartbeat", body);
    const moved = new Request("http://localhost/api/v1/agent/signals", { method: "POST", body, headers: otherPath.headers });
    expect(await code(authenticateAgent(db, moved, new TextEncoder().encode(body)))).toBe("401:UNAUTHENTICATED");
    const unknown = signed("/api/v1/agent/heartbeat", body, { agent: "agt_unknowndev99" });
    expect(await code(authenticateAgent(db, unknown, new TextEncoder().encode(body)))).toBe("403:DEVICE_NOT_PAIRED");
    const missing = new Request("http://localhost/api/v1/agent/heartbeat", { method: "POST", body });
    expect(await code(authenticateAgent(db, missing, new TextEncoder().encode(body)))).toBe("401:UNAUTHENTICATED");
    await db.query("INSERT INTO apex.agent_devices(id, device_name, status, revoked_at) VALUES ('agt_revokeddev01','old pi','revoked',now())");
    const revoked = signed("/api/v1/agent/heartbeat", body, { agent: "agt_revokeddev01" });
    expect(await code(authenticateAgent(db, revoked, new TextEncoder().encode(body)))).toBe("403:DEVICE_REVOKED");
    const ev = await db.query<{ n: number }>("SELECT count(*)::int AS n FROM apex.security_events");
    expect(ev[0]!.n).toBeGreaterThanOrEqual(5);
  });
});

describe("encrypted settings", () => {
  it("stores ciphertext only, returns hints, and fails closed on corruption", async () => {
    await saveIntegration(db, ownerId, "alpaca", { key_id: "PKTESTKEY1234567ABCD", secret_key: "TestSecretValueDoNotUse0123456789abcdef" });
    const raw = await db.query<{ ciphertext: Uint8Array }>("SELECT ciphertext FROM apex.encrypted_integrations WHERE provider='alpaca'");
    for (const r of raw) expect(Buffer.from(r.ciphertext).toString("utf8")).not.toContain("TestSecretValue");
    const dump = JSON.stringify(await db.query("SELECT * FROM apex.settings_history")) + JSON.stringify(await db.query("SELECT * FROM apex.audit_events"));
    expect(dump).not.toContain("TestSecretValue");
    expect(dump).not.toContain("PKTESTKEY1234567ABCD");
    const view = JSON.stringify(await listIntegrations(db));
    expect(view).not.toContain("TestSecretValue");
    expect(view).toContain("…ABCD");
    expect((await readIntegrationSecrets(db, "alpaca"))!.fields.secret_key).toBe("TestSecretValueDoNotUse0123456789abcdef");
    await db.query("UPDATE apex.encrypted_integrations SET ciphertext = decode('00','hex') || ciphertext WHERE provider='alpaca' AND field='secret_key'");
    expect(await code(readIntegrationSecrets(db, "alpaca"))).toMatch(/could not be decrypted/);
    expect(await code(saveIntegration(db, ownerId, "alpaca", { key_id: "bad key with spaces" }))).toBe("422:INVALID_FIELD");
  });
});

describe("pairing", () => {
  it("requires a valid one-time code and proof of key possession", async () => {
    const { code: pairCode } = await startPairing(db, ownerId);
    const ed = generateKeyPairSync("ed25519");
    const x = generateKeyPairSync("x25519");
    const pubB64 = (k: ReturnType<typeof generateKeyPairSync>["publicKey"]) => k.export({ format: "jwk" }).x as string;
    const agentId = "agt_testpairpi01";
    const message = Buffer.from("apex-pairing-proof-v1\n" + canonicalJson({ agent_id: agentId, code: normalizeCode(pairCode),
      server_nonce: serverNonceFor(pairCode), x25519: pubB64(x.publicKey) }));
    const proof = edSign(null, message, ed.privateKey).toString("base64url");
    const req = { code: pairCode, device_name: "test pi", x25519_public_key: pubB64(x.publicKey), ed25519_public_key: pubB64(ed.publicKey), proof };
    expect(await code(completePairing(db, agentId, { ...req, proof: edSign(null, Buffer.from("other"), ed.privateKey).toString("base64url") })))
      .toBe("403:PAIRING_PROOF_INVALID");
    const ok = await completePairing(db, agentId, req);
    expect(ok.status).toBe("pending_approval");
    expect(await code(completePairing(db, agentId, req))).toBe("403:PAIRING_CODE_INVALID"); // single use
    await setDeviceStatus(db, ownerId, agentId, "approve");
    const st = await db.query<{ status: string }>("SELECT status FROM apex.agent_devices WHERE id=$1", [agentId]);
    expect(st[0]!.status).toBe("active");
    void createPrivateKey;
  });
});

describe("signal lifecycle ingestion", () => {
  it("rejects illegal transitions and edits to terminal signals", async () => {
    const closed = await db.query<{ signal_id: string; state: string }>("SELECT signal_id, state FROM apex.signal_heads WHERE state IN ('CLOSED','EXPIRED') LIMIT 1");
    if (!closed[0]) return;
    const ev = { signal_id: closed[0].signal_id, event_type: "ACTIVATED", event_at: "2026-10-01T20:15:00Z", dedupe_key: `test:${randomBytes(6).toString("hex")}`,
      from_state: "PUBLISHED", to_state: "ACTIVE", cause: "forged", payload: {} };
    expect(await code(ingestSignalEvents(db, { events: [ev] }))).toBe("409:STATE_CONFLICT");
  });
});

describe("model registry", () => {
  it("rejects tampered artifacts and untrusted signers", async () => {
    const m = await db.query<{ manifest_json: Record<string, unknown>; artifact: Uint8Array }>(
      "SELECT v.manifest_json, a.artifact FROM apex.model_versions v JOIN apex.model_artifacts a ON a.model_id = v.id LIMIT 1");
    const manifest = m[0]!.manifest_json;
    const artifact = Buffer.from(m[0]!.artifact);
    const keys = (await db.query<{ value_json: Record<string, string> }>("SELECT value_json FROM apex.settings WHERE key='demo.trusted_model_keys'"))[0]!.value_json;
    const tampered = Buffer.from(artifact.toString("utf8").replace('"kind"', '"kind" '));
    expect(await code(registerCandidate(db, { manifest, artifact_b64: tampered.toString("base64") }, keys))).toMatch(/SIZE_MISMATCH|CHECKSUM_MISMATCH/);
    expect(await code(registerCandidate(db, { manifest, artifact_b64: artifact.toString("base64") }, {}))).toBe("422:UNTRUSTED_SIGNER");
    expect(await code(registerCandidate(db, { manifest: { ...manifest, evaluation: { confidence_status: "CALIBRATED" } }, artifact_b64: artifact.toString("base64") }, keys)))
      .toBe("422:BAD_SIGNATURE");
    expect(createHash("sha256").update(artifact).digest("hex")).toBe(manifest.artifact_sha256);
  });
});

describe("notification outbox controls", () => {
  it("DEMO private updates are acknowledged (sent or held privately), never left pending", async () => {
    const dm = await db.query<{ state: string; n: number }>(
      "SELECT state, count(*)::int AS n FROM apex.notification_outbox WHERE route_json->>'type' = 'DM' GROUP BY state");
    expect(dm.length).toBeGreaterThan(0);
    expect(dm.find((r) => r.state === "pending")?.n ?? 0).toBe(0);
    const held = await db.query<{ n: number }>(
      "SELECT count(*)::int AS n FROM apex.notification_outbox WHERE route_json->>'type' = 'DM' AND state = 'deferred' AND rendered_json IS NOT NULL");
    expect(held[0]!.n).toBe(0); // personal content is never stored as a rendered channel post
  });

  it("the STOP switch hands out only system alerts, and owner suppression removes a signal's queued notices", async () => {
    const { leaseOutbox } = await import("@/server/services/ingest");
    const { putSetting } = await import("@/server/services/settings");
    const { reviewSignal } = await import("@/server/services/explorer");
    const open = await db.query<{ signal_id: string }>("SELECT signal_id FROM apex.signal_heads WHERE state IN ('PUBLISHED','ACTIVE') LIMIT 1");
    const sid = open[0]!.signal_id;
    const mk = (key: string, kind: string) => db.query(
      `INSERT INTO apex.notification_outbox(event_key, route_json, kind, priority, payload_json, is_demo) VALUES ($1, '{"type":"CHANNEL","channel_key":"system_status"}'::jsonb, $2, 'NORMAL', '{}'::jsonb, true)`,
      [key, kind]);
    await mk(`TEST:SIGEVT:${sid}:x1`, "SIGNAL_UPDATE");
    await mk(`TEST:SYS:${randomBytes(4).toString("hex")}`, "SYSTEM_ALERT");
    const v = (await db.query<{ version: number }>("SELECT version FROM apex.settings WHERE key = 'alerts.kill_switch'"))[0]?.version ?? 0;
    await putSetting(db, ownerId, "alerts.kill_switch", { stopped: true, reason: "test" }, v);
    const leased = await leaseOutbox(db, "agt_demodevice01", 50);
    expect(leased.length).toBeGreaterThan(0);
    const kinds = await db.query<{ kind: string }>("SELECT kind FROM apex.notification_outbox WHERE id = ANY($1)", [leased.map((l) => l.outbox_id)]);
    expect(new Set(kinds.map((k) => k.kind))).toEqual(new Set(["SYSTEM_ALERT"]));
    await putSetting(db, ownerId, "alerts.kill_switch", { stopped: false, reason: "" }, v + 1);
    expect(await code(reviewSignal(db, ownerId, sid, { note: "x", suppress: true }, new Date()))).toBe("422:NOTE_REQUIRED");
    const r = await reviewSignal(db, ownerId, sid, { note: "test suppression", suppress: true }, new Date());
    expect(r.suppressed_pending).toBeGreaterThanOrEqual(1);
    const st = await db.query<{ state: string }>("SELECT state FROM apex.notification_outbox WHERE event_key = $1", [`TEST:SIGEVT:${sid}:x1`]);
    expect(st[0]!.state).toBe("suppressed");
    const head = await db.query<{ state: string }>("SELECT state FROM apex.signal_heads WHERE signal_id = $1", [sid]);
    expect(["PUBLISHED", "ACTIVE"]).toContain(head[0]!.state); // review never changes lifecycle state
  });
});
