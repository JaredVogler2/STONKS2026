// Cross-language end-to-end (acceptance C/D/J): the Python `apex` CLI that the GitHub workflows run (release client, HMAC
// signer, train+sign, upload, promote, rollback, run report) against the real release route handlers and the real
// migrations. DEMO mode, TEST-ONLY keys. Opt-in because it needs the Python environment:
//   (cd apps/pi-worker && uv sync --all-extras) && APEX_PY_E2E=1 pnpm --filter @apex/web exec vitest run tests/integration/python-cli.test.ts
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtempSync, readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

const SECRET = Buffer.alloc(32, 9);
const TEST_SIGNING_SEED = Buffer.alloc(32, 7).toString("base64url"); // test-only Ed25519 seed; its public key is below
// public key of the committed DEMO seed's signer (fixtures/demo/web-seed.json); not a secret
const DEMO_SEED_SIGNER = JSON.stringify({ "1": "lAo5AhTtpALt-qV-PhYL4Y9i0WLaGjaA755JMP2bjJk" });
process.env.APEX_MODE = "DEMO";
process.env.AGENT_HMAC_SECRET = Buffer.alloc(32, 7).toString("base64");
process.env.APEX_RELEASE_HMAC_SECRET = SECRET.toString("base64");
process.env.APEX_CONFIG_ENCRYPTION_KEY = Buffer.alloc(32, 3).toString("base64");
process.env.APEX_SESSION_SECRET = Buffer.alloc(32, 4).toString("base64");
process.env.APEX_MODEL_TRUSTED_KEYS = JSON.stringify({ "1": "6kpsY-KcUgq-9VB7Ey7F-ZVHdq6-vnuSQh7qaRRG0iw" });

const { openTestDb } = await import("../support/test-db");
const { migrate } = await import("@/server/db/migrate");
const { __setDbForTests } = await import("@/server/db");
const { ensureDemoSeed } = await import("@/server/services/demo");
type Handler = (req: Request) => Promise<Response>;
const routes: Record<string, Record<string, unknown>> = {
  "/api/v1/release/policy": await import("@/app/api/v1/release/policy/route"),
  "/api/v1/release/promotions": await import("@/app/api/v1/release/promotions/route"),
  "/api/v1/release/champions": await import("@/app/api/v1/release/champions/route"),
  "/api/v1/release/training-runs": await import("@/app/api/v1/release/training-runs/route"),
  "/api/v1/release/models": await import("@/app/api/v1/release/models/route"),
};

type Db = Awaited<ReturnType<typeof openTestDb>>;
let db: Db;
let server: Server;
let base = "";
const worker = fileURLToPath(new URL("../../../pi-worker", import.meta.url));
const work = mkdtempSync(join(tmpdir(), "apex-e2e-"));

beforeAll(async () => {
  if (!process.env.APEX_PY_E2E) return;
  db = await openTestDb();
  await migrate(db);
  __setDbForTests(db);
  await ensureDemoSeed(db);
  server = createServer(async (req, res) => {
    const chunks: Buffer[] = [];
    for await (const c of req) chunks.push(c as Buffer);
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    const handler = routes[url.pathname]?.[req.method ?? "GET"] as Handler | undefined;
    if (!handler) { res.writeHead(404).end(); return; }
    const headers = new Headers();
    for (const [k, v] of Object.entries(req.headers)) if (typeof v === "string") headers.set(k, v);
    const out = await handler(new Request(`http://127.0.0.1${req.url}`, { method: req.method, headers,
      body: ["GET", "HEAD"].includes(req.method ?? "GET") ? undefined : Buffer.concat(chunks) }));
    res.writeHead(out.status, { "content-type": "application/json" }).end(Buffer.from(await out.arrayBuffer()));
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  const addr = server.address();
  base = `http://127.0.0.1:${typeof addr === "object" && addr ? addr.port : 0}`;
}, 240_000);

afterAll(() => server?.close());

const run = promisify(execFile);
// async on purpose: the route handlers live in this process, so the event loop must keep serving while Python runs
async function apex(args: string[], extra: Record<string, string> = {}): Promise<{ code: number; out: string }> {
  const env = { ...process.env, APEX_RELEASE_URL: base, APEX_RELEASE_AGENT_ID: "rel_ci0000test", APEX_RELEASE_HMAC_SECRET: SECRET.toString("base64"),
    APEX_MODEL_SIGNING_KEY: TEST_SIGNING_SEED, GITHUB_RUN_ID: "9001", GITHUB_SHA: "f".repeat(40), ...extra };
  try {
    const { stdout, stderr } = await run("uv", ["run", "--no-sync", "apex", ...args], { cwd: worker, env, encoding: "utf8" });
    return { code: 0, out: `${stdout}\n${stderr}` };
  } catch (e) {
    const err = e as { code: number; stdout: string; stderr: string; message: string };
    return { code: err.code, out: `${err.message}\n${err.stdout}\n${err.stderr}` };
  }
}

describe.skipIf(!process.env.APEX_PY_E2E)("python CLI <-> release API", () => {
  it("policy, champions, train+sign, upload, promote, report-run, verify", async () => {
    let r = await apex(["release", "policy", "--out", join(work, "policy.json")]);
    expect(r.code, r.out).toBe(0);
    expect(JSON.parse(readFileSync(join(work, "policy.json"), "utf8")).mode).toBe("DEMO");
    r = await apex(["budget", "check", "--job", "drift", "--policy", join(work, "policy.json"), "--out", join(work, "budget.json")]);
    expect(r.code, r.out).toBe(0);
    r = await apex(["release", "champions", "--out", join(work, "champions.json"), "--verify"], { APEX_MODEL_TRUSTED_KEYS: "" });
    expect(r.code, r.out).toBe(0);
    r = await apex(["train", "--source", "redteam", "--sign", "--out", join(work, "run"), "--champions", join(work, "champions.json")]);
    expect(r.code, r.out).toBe(0);
    r = await apex(["release", "upload", "--dir", join(work, "run"), "--policy", join(work, "policy.json"), "--out", join(work, "upload.json")],
      { APEX_MODEL_TRUSTED_KEYS: process.env.APEX_MODEL_TRUSTED_KEYS! });
    expect(r.code, r.out).toBe(0);
    const up = JSON.parse(readFileSync(join(work, "upload.json"), "utf8"));
    // deterministic training reproduces the seeded DEMO champion exactly: no skill gain => not promotable => nothing
    // uploaded and the incumbent is untouched (acceptance D, end to end against the real server code)
    expect(up.outcome).toBe("champion_retained");
    const mid = up.skipped[0].model_id as string;
    const before = await db.query<{ id: string; status: string }>("SELECT id, status FROM apex.model_versions WHERE strategy_id='redteam_planted_alpha'");
    r = await apex(["release", "upload", "--dir", join(work, "run"), "--policy", join(work, "policy.json"), "--out", join(work, "upload2.json"),
      "--include-non-promotable"], { APEX_MODEL_TRUSTED_KEYS: process.env.APEX_MODEL_TRUSTED_KEYS! });
    expect(r.code, r.out).toBe(0);
    const up2 = JSON.parse(readFileSync(join(work, "upload2.json"), "utf8"));
    expect(up2.registered[0]).toMatchObject({ model_id: mid, created: false }); // server verified signature + checksum; idempotent
    // the stored DEMO champion was signed by the seed's key: verifying the registry with a different trusted key fails
    r = await apex(["release", "promote", "--model-ids", mid, "--out", join(work, "rel-badkey.json")],
      { APEX_MODEL_TRUSTED_KEYS: process.env.APEX_MODEL_TRUSTED_KEYS! });
    expect(r.code, r.out).toBe(1);
    expect(r.out).toMatch(/REGISTRY VERIFICATION FAILED/);
    r = await apex(["release", "promote", "--model-ids", mid, "--out", join(work, "rel.json"), "--fail-on-reject"],
      { APEX_MODEL_TRUSTED_KEYS: DEMO_SEED_SIGNER });
    expect(r.code, r.out).toBe(0);
    const rel = JSON.parse(readFileSync(join(work, "rel.json"), "utf8"));
    expect(rel.results[0]).toMatchObject({ ok: true, reason: "already champion", verified_champion: true });
    expect(rel.registry_verification[mid].ok).toBe(true);
    expect(rel.signature_checked).toBe(true);
    const after = await db.query<{ id: string; status: string }>("SELECT id, status FROM apex.model_versions WHERE strategy_id='redteam_planted_alpha'");
    expect(after).toEqual(before);
    r = await apex(["release", "report-run", "--workflow", "train_weekly", "--status", "success", "--started-at", String(Math.floor(Date.now() / 1000) - 120),
      "--dir", join(work, "run"), "--upload", join(work, "upload.json"), "--budget", join(work, "budget.json"), "--reason", "e2e"]);
    expect(r.code, r.out).toBe(0);
    const runs = await db.query<{ status: string; outcome: string; candidate_model_id: string; minutes_used: string; is_demo: boolean; trigger: string }>(
      "SELECT status, outcome, candidate_model_id, minutes_used::text, is_demo, trigger FROM apex.training_runs WHERE github_run_id='9001'");
    expect(runs[0]).toMatchObject({ status: "succeeded", outcome: "champion_retained", candidate_model_id: null, is_demo: true, trigger: "local" });
    expect(Number(runs[0]!.minutes_used)).toBeGreaterThanOrEqual(2);
    r = await apex(["release", "rollback", "--model-ids", mid, "--out", join(work, "rb.json")],
      { APEX_MODEL_TRUSTED_KEYS: DEMO_SEED_SIGNER });
    expect(r.code, r.out).toBe(0);
    expect(JSON.parse(readFileSync(join(work, "rb.json"), "utf8")).results[0].reason).toMatch(/no previous champion|previous champion restored/);
    r = await apex(["release", "sla", "--policy", join(work, "policy.json")]);
    expect(r.code, r.out).toBe(0);
  }, 240_000);

  it("a tampered HMAC secret is rejected by the server", async () => {
    const r = await apex(["release", "policy"], { APEX_RELEASE_HMAC_SECRET: Buffer.alloc(32, 1).toString("base64") });
    expect(r.code, r.out).toBe(1);
    expect(r.out).toMatch(/401 UNAUTHENTICATED/);
  });
});
