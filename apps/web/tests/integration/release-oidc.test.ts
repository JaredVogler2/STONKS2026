import { randomBytes } from "node:crypto";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { claims, oidcToken, TEST_REPO, testJwk } from "../support/oidc-token";

const SECRET = Buffer.alloc(32, 9);
process.env.APEX_MODE = "DEMO";
process.env.AGENT_HMAC_SECRET = Buffer.alloc(32, 7).toString("base64");
process.env.APEX_RELEASE_HMAC_SECRET = SECRET.toString("base64");
process.env.APEX_CONFIG_ENCRYPTION_KEY = Buffer.alloc(32, 3).toString("base64");
process.env.APEX_SESSION_SECRET = Buffer.alloc(32, 4).toString("base64");

const { openTestDb } = await import("../support/test-db");
const { migrate } = await import("@/server/db/migrate");
const { __setDbForTests } = await import("@/server/db");
const { HttpError } = await import("@/server/http/responses");
const { signRequest } = await import("@/server/security/hmac");
const { JwksCache, __setJwksForTests, requireReleaseOidc } = await import("@/server/security/oidc");
const runsRoute = await import("@/app/api/v1/release/training-runs/route");
const policyRoute = await import("@/app/api/v1/release/policy/route");

type Db = Awaited<ReturnType<typeof openTestDb>>;
let db: Db;
const jwks = new JwksCache(async () => ({ keys: [testJwk] }));
const now = () => Math.floor(Date.now() / 1000);
const CONFIGURED = { APEX_RELEASE_GITHUB_REPOSITORY: TEST_REPO };

beforeAll(async () => {
  db = await openTestDb();
  await migrate(db);
  __setDbForTests(db);
  __setJwksForTests(jwks);
}, 240_000);
afterAll(() => {
  __setJwksForTests(null);
  delete process.env.APEX_RELEASE_GITHUB_REPOSITORY;
});

function req(token?: string, method = "POST"): Request {
  return new Request("http://localhost/api/v1/release/training-runs", { method, headers: token ? { "x-apex-oidc": token } : {} });
}

async function code(p: Promise<unknown>): Promise<string> {
  try {
    await p;
    return "OK";
  } catch (e) {
    if (e instanceof HttpError) return `${e.status} ${e.code}`;
    throw e;
  }
}

describe("release OIDC enforcement", () => {
  it("outside DEMO: required, verified, single use, audited", async () => {
    const opts = { isDemo: false, env: CONFIGURED, jwks };
    expect(await code(requireReleaseOidc(db, req(), "rel_ci0000test", opts))).toBe("401 RELEASE_OIDC_REQUIRED");
    const token = oidcToken(claims(now()));
    const got = await requireReleaseOidc(db, req(token), "rel_ci0000test", opts);
    expect(got).toMatchObject({ repository: TEST_REPO, run_id: "777001", sha: "c".repeat(40) });
    expect(await code(requireReleaseOidc(db, req(token), "rel_ci0000test", opts))).toBe("409 OIDC_REPLAYED");
    expect(await code(requireReleaseOidc(db, req(oidcToken(claims(now(), { repository: "evil/apex" }))), "rel_ci0000test", opts)))
      .toBe("401 RELEASE_OIDC_INVALID");
    expect(await code(requireReleaseOidc(db, req(oidcToken(claims(now(), { aud: "nope" }))), "rel_ci0000test", opts)))
      .toBe("401 RELEASE_OIDC_INVALID");
    const audit = await db.query<{ metadata: Record<string, unknown> }>(
      "SELECT redacted_metadata_json AS metadata FROM apex.audit_events WHERE action = 'release.oidc_verified' ORDER BY at DESC LIMIT 1");
    expect(audit[0]!.metadata).toMatchObject({ run_id: "777001", sha: "c".repeat(40), event_name: "schedule" });
    const sec = await db.query<{ kind: string }>("SELECT kind FROM apex.security_events WHERE kind LIKE 'release_oidc_%' ORDER BY id");
    expect(sec.map((r) => r.kind)).toEqual(["release_oidc_missing", "release_oidc_replayed", "release_oidc_repository", "release_oidc_audience"]);
    // a GET is verified and burnt too, but not audited (reads are frequent and change nothing)
    const before = (await db.query<{ n: number }>("SELECT count(*)::int AS n FROM apex.audit_events WHERE action='release.oidc_verified'"))[0]!.n;
    expect(await code(requireReleaseOidc(db, req(oidcToken(claims(now())), "GET"), "rel_ci0000test", opts))).toBe("OK");
    expect((await db.query<{ n: number }>("SELECT count(*)::int AS n FROM apex.audit_events WHERE action='release.oidc_verified'"))[0]!.n).toBe(before);
  });

  it("outside DEMO without APEX_RELEASE_GITHUB_REPOSITORY: 503 with instructions, even with a valid token", async () => {
    for (const r of [req(), req(oidcToken(claims(now())))]) {
      try {
        await requireReleaseOidc(db, r, "rel_ci0000test", { isDemo: false, env: {}, jwks });
        throw new Error("expected 503");
      } catch (e) {
        expect(e).toBeInstanceOf(HttpError);
        expect((e as InstanceType<typeof HttpError>).status).toBe(503);
        expect((e as Error).message).toMatch(/APEX_RELEASE_GITHUB_REPOSITORY=owner\/repo/);
      }
    }
    expect(await code(requireReleaseOidc(db, req(), "rel_ci0000test", { isDemo: false, env: { APEX_RELEASE_GITHUB_REPOSITORY: "not a repo" }, jwks })))
      .toBe("503 RELEASE_OIDC_NOT_CONFIGURED");
  });

  it("APEX_RELEASE_OIDC=off is an explicit HMAC-only escape hatch", async () => {
    expect(await requireReleaseOidc(db, req(), "rel_ci0000test", { isDemo: false, env: { APEX_RELEASE_OIDC: "off" }, jwks })).toBeNull();
  });

  it("DEMO: optional, but a presented token must verify (repository checked only when configured)", async () => {
    expect(await requireReleaseOidc(db, req(), "rel_ci0000test", { isDemo: true, env: {}, jwks })).toBeNull();
    expect(await code(requireReleaseOidc(db, req("garbage"), "rel_ci0000test", { isDemo: true, env: {}, jwks }))).toBe("401 RELEASE_OIDC_INVALID");
    expect(await code(requireReleaseOidc(db, req(oidcToken(claims(now(), { repository: "any/repo",
      job_workflow_ref: "any/repo/.github/workflows/apex-model-drift.yml@refs/heads/main" }))), "rel_ci0000test", { isDemo: true, env: {}, jwks })))
      .toBe("OK");
    expect(await code(requireReleaseOidc(db, req(oidcToken(claims(now(), { environment: "dev" }))), "rel_ci0000test", { isDemo: true, env: {}, jwks })))
      .toBe("401 RELEASE_OIDC_INVALID");
  });

  it("a JWKS outage with nothing cached is a 503, never an accept", async () => {
    const down = new JwksCache(async () => { throw new Error("ECONNRESET"); });
    expect(await code(requireReleaseOidc(db, req(oidcToken(claims(now()))), "rel_ci0000test", { isDemo: false, env: CONFIGURED, jwks: down })))
      .toBe("503 RELEASE_OIDC_UNAVAILABLE");
  });
});

function signed(path: string, body: string, method: string, token?: string): Request {
  const ts = String(now());
  const nonce = randomBytes(18).toString("base64url");
  const { bodySha256, signature } = signRequest(SECRET, "rel_ci0000test", method, path, ts, nonce, body);
  return new Request(`http://localhost${path}`, { method, body: method === "GET" ? undefined : body, headers: {
    "x-apex-agent-id": "rel_ci0000test", "x-apex-timestamp": ts, "x-apex-nonce": nonce, "x-apex-body-sha256": bodySha256,
    "x-apex-signature": signature, ...(token ? { "x-apex-oidc": token } : {}) } });
}

describe("release routes with OIDC (route level, DEMO deployment configured with a repository)", () => {
  it("run id, attempt and commit come from the signed claims; a bad token is refused after a good HMAC", async () => {
    process.env.APEX_RELEASE_GITHUB_REPOSITORY = TEST_REPO;
    const run = { workflow: "train_weekly", trigger: "schedule", github_run_id: "1", github_run_attempt: 1, code_sha: "spoofed",
      started_at: new Date().toISOString(), status: "running", is_demo: true, report: { note: "x" } };
    const res = await runsRoute.POST(signed("/api/v1/release/training-runs", JSON.stringify(run), "POST", oidcToken(claims(now()))));
    expect(res.status).toBe(200);
    const rows = await db.query<{ github_run_id: string; github_run_attempt: number; code_sha: string; report_json: Record<string, unknown> }>(
      "SELECT github_run_id, github_run_attempt, code_sha, report_json FROM apex.training_runs WHERE github_run_id = '777001'");
    expect(rows[0]).toMatchObject({ github_run_id: "777001", github_run_attempt: 2, code_sha: "c".repeat(40) });
    expect(rows[0]!.report_json).toMatchObject({ note: "x", github_oidc: { repository: TEST_REPO, event_name: "schedule" } });
    const bad = await policyRoute.GET(signed("/api/v1/release/policy", "", "GET", oidcToken(claims(now(), { ref: "refs/heads/dev" }))));
    expect(bad.status).toBe(401);
    expect((await bad.json()).error.code).toBe("RELEASE_OIDC_INVALID");
    // DEMO without a token still works (local replays, the DEMO seed and the opt-in python-cli test)
    expect((await policyRoute.GET(signed("/api/v1/release/policy", "", "GET"))).status).toBe(200);
  });
});
