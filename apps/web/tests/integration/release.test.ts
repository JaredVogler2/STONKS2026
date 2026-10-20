import { randomBytes } from "node:crypto";
import { beforeAll, describe, expect, it } from "vitest";

const SECRET = Buffer.alloc(32, 9);
process.env.APEX_MODE = "DEMO";
process.env.AGENT_HMAC_SECRET = Buffer.alloc(32, 7).toString("base64");
process.env.APEX_RELEASE_HMAC_SECRET = SECRET.toString("base64");
process.env.APEX_CONFIG_ENCRYPTION_KEY = Buffer.alloc(32, 3).toString("base64");
process.env.APEX_SESSION_SECRET = Buffer.alloc(32, 4).toString("base64");

const { openTestDb } = await import("../support/test-db");
const { migrate } = await import("@/server/db/migrate");
const { __setDbForTests } = await import("@/server/db");
const { ensureDemoSeed } = await import("@/server/services/demo");
const { signRequest } = await import("@/server/security/hmac");
const policyRoute = await import("@/app/api/v1/release/policy/route");
const promotionsRoute = await import("@/app/api/v1/release/promotions/route");
const championsRoute = await import("@/app/api/v1/release/champions/route");
const runsRoute = await import("@/app/api/v1/release/training-runs/route");

type Db = Awaited<ReturnType<typeof openTestDb>>;
let db: Db;

beforeAll(async () => {
  db = await openTestDb();
  await migrate(db);
  __setDbForTests(db);
  await ensureDemoSeed(db);
}, 240_000);

function signed(path: string, body: string, method = "POST", agent = "rel_ci0000test"): Request {
  const ts = String(Math.floor(Date.now() / 1000));
  const nonce = randomBytes(18).toString("base64url");
  const { bodySha256, signature } = signRequest(SECRET, agent, method, path, ts, nonce, body);
  return new Request(`http://localhost${path}`, { method, body: method === "GET" ? undefined : body, headers: {
    "x-apex-agent-id": agent, "x-apex-timestamp": ts, "x-apex-nonce": nonce, "x-apex-body-sha256": bodySha256, "x-apex-signature": signature } });
}

/* Release identity routes used by the GitHub model workflows (policy, champions with artifacts, promotions, run reports). */
describe("release routes", () => {
  it("policy reports mode, policy, budgets, month usage and last runs", async () => {
    const run = { workflow: "train_weekly", trigger: "schedule", github_run_id: "42", github_run_attempt: 1, code_sha: "abc", started_at: new Date().toISOString(),
      finished_at: new Date().toISOString(), status: "succeeded", outcome: "champion_retained", minutes_used: 7, is_demo: true, report: { a: 1 } };
    const r1 = await runsRoute.POST(signed("/api/v1/release/training-runs", JSON.stringify(run)));
    expect(r1.status).toBe(200);
    const res = await policyRoute.GET(signed("/api/v1/release/policy", "", "GET"));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.mode).toBe("DEMO");
    expect(body.policy.promotion_mode).toBe("manual");
    expect(body.budgets.actions_monthly_minutes).toBe(1500);
    expect(body.usage_month.minutes_used).toBeGreaterThanOrEqual(7);
    expect(body.last_runs.train_weekly.status).toBe("succeeded");
  });
  it("agent identities are refused on release routes", async () => {
    const res = await policyRoute.GET(signed("/api/v1/release/policy", "", "GET", "agt_demodevice01"));
    expect(res.status).toBe(403); // release identity required
  });
  it("champions include signed manifests + artifacts on request", async () => {
    const plain = await (await championsRoute.GET(signed("/api/v1/release/champions", "", "GET"))).json();
    expect(plain.manifests).toBeUndefined();
    const full = await (await championsRoute.GET(signed("/api/v1/release/champions?include=artifacts", "", "GET"))).json();
    const ids = Object.values(full.champions as Record<string, { model_id: string }>).map((c) => c.model_id).sort();
    expect((full.manifests as { model_id: string }[]).map((m) => m.model_id).sort()).toEqual(ids);
    for (const id of ids) expect(typeof full.artifacts[id]).toBe("string");
  });
  it("promotions: validation, frozen policy, rollback of non-champion refused", async () => {
    expect((await promotionsRoute.POST(signed("/api/v1/release/promotions", JSON.stringify({ action: "promote", model_id: "../x" })))).status).toBe(422);
    expect((await promotionsRoute.POST(signed("/api/v1/release/promotions", JSON.stringify({ action: "delete", model_id: "mdl_abc" })))).status).toBe(422);
    expect((await promotionsRoute.POST(signed("/api/v1/release/promotions", JSON.stringify({ action: "promote", model_id: "mdl_doesnotexist" })))).status).toBe(404);
    const champ = await db.query<{ id: string }>("SELECT id FROM apex.model_versions WHERE status='champion' LIMIT 1");
    const again = await (await promotionsRoute.POST(signed("/api/v1/release/promotions", JSON.stringify({ action: "promote", model_id: champ[0]!.id })))).json();
    expect(again).toMatchObject({ action: "promote", promoted: false, reason: "already champion" });
    const other = await db.query<{ id: string }>("SELECT id FROM apex.model_versions WHERE status<>'champion' LIMIT 1");
    if (other[0]) {
      const rb = await promotionsRoute.POST(signed("/api/v1/release/promotions", JSON.stringify({ action: "rollback", model_id: other[0].id })));
      expect(rb.status).toBe(409);
    }
    const rb2 = await (await promotionsRoute.POST(signed("/api/v1/release/promotions", JSON.stringify({ action: "rollback", model_id: champ[0]!.id })))).json();
    expect(rb2.action).toBe("rollback");
    expect(typeof rb2.rolled_back).toBe("boolean");
    const audit = await db.query<{ n: number }>("SELECT count(*)::int AS n FROM apex.audit_events WHERE actor_id = 'rel_ci0000test'");
    expect(audit[0]!.n).toBeGreaterThanOrEqual(rb2.rolled_back ? 1 : 0);
  });
});
