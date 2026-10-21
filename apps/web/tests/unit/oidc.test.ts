import { generateKeyPairSync } from "node:crypto";
import { describe, expect, it } from "vitest";
import { JwksCache, OidcError, releaseOidcConfig, verifyGithubOidcToken } from "@/server/security/oidc";
import { claims, oidcToken, TEST_REPO, testJwk } from "../support/oidc-token";

const NOW = 1_800_000_000;
const cfg = releaseOidcConfig({ APEX_RELEASE_GITHUB_REPOSITORY: TEST_REPO });
const jwks = () => new JwksCache(async () => ({ keys: [testJwk] }));

async function reason(token: string, c = cfg, cache = jwks()): Promise<string> {
  try {
    await verifyGithubOidcToken(token, c, { jwks: cache, nowSeconds: NOW });
    return "OK";
  } catch (e) {
    if (e instanceof OidcError) return e.reason;
    throw e;
  }
}

describe("GitHub OIDC release token verification", () => {
  it("accepts a valid token and returns the run claims", async () => {
    const out = await verifyGithubOidcToken(oidcToken(claims(NOW)), cfg, { jwks: jwks(), nowSeconds: NOW });
    expect(out).toMatchObject({ repository: TEST_REPO, run_id: "777001", run_attempt: "2", sha: "c".repeat(40), event_name: "schedule",
      workflow_ref: `${TEST_REPO}/.github/workflows/apex-model-train-weekly.yml@refs/heads/main` });
    // repository is compared case-insensitively; a dispatched run and the reusable release workflow are accepted
    expect(await reason(oidcToken(claims(NOW, { repository: "Octo-Org/APEX", event_name: "workflow_dispatch",
      job_workflow_ref: "Octo-Org/APEX/.github/workflows/apex-model-release.yml@refs/heads/main" })))).toBe("OK");
    expect(await reason(oidcToken(claims(NOW, { aud: ["other", "apex-release"] })))).toBe("OK");
  });

  it.each([
    ["wrong audience", { aud: "https://github.com/octo-org" }, "AUDIENCE"],
    ["wrong issuer", { iss: "https://token.actions.example.com" }, "ISSUER"],
    ["another repository", { repository: "evil/apex" }, "REPOSITORY"],
    ["no environment (e.g. ci.yml)", { environment: undefined }, "ENVIRONMENT"],
    ["another environment", { environment: "production" }, "ENVIRONMENT"],
    ["a feature branch", { ref: "refs/heads/feature" }, "REF"],
    ["a pull request", { event_name: "pull_request", ref: "refs/heads/main" }, "EVENT"],
    ["a push", { event_name: "push" }, "EVENT"],
    ["a non apex-model workflow", { job_workflow_ref: `${TEST_REPO}/.github/workflows/ci.yml@refs/heads/main` }, "WORKFLOW"],
    ["a workflow from another repo", { job_workflow_ref: "evil/apex/.github/workflows/apex-model-release.yml@refs/heads/main" }, "WORKFLOW"],
    ["expired", { iat: NOW - 900, nbf: NOW - 900, exp: NOW - 61 }, "EXPIRED"],
    ["not yet valid", { iat: NOW + 120, nbf: NOW + 120, exp: NOW + 400 }, "NOT_YET_VALID"],
    ["too long-lived", { exp: NOW + 7200 }, "LIFETIME"],
    ["no jti", { jti: undefined }, "JTI"],
  ])("rejects %s", async (_label, over, want) => {
    expect(await reason(oidcToken(claims(NOW, over)))).toBe(want);
  });

  it("tolerates 60 s of clock skew", async () => {
    expect(await reason(oidcToken(claims(NOW, { iat: NOW - 400, nbf: NOW - 400, exp: NOW - 30 })))).toBe("OK");
    expect(await reason(oidcToken(claims(NOW, { iat: NOW + 30, nbf: NOW + 30, exp: NOW + 330 })))).toBe("OK");
  });

  it("rejects alg none, HMAC algorithms, bad signatures and malformed tokens", async () => {
    const good = oidcToken(claims(NOW));
    const [h, p] = good.split(".");
    const hdr = (o: object) => Buffer.from(JSON.stringify(o)).toString("base64url");
    expect(await reason(`${hdr({ alg: "none", kid: "test-k1" })}.${p}.`)).toBe("MALFORMED");
    expect(await reason(`${hdr({ alg: "none", kid: "test-k1" })}.${p}.AAAA`)).toBe("ALGORITHM");
    expect(await reason(`${hdr({ alg: "HS256", kid: "test-k1" })}.${p}.AAAA`)).toBe("ALGORITHM");
    const forged = Buffer.from(JSON.stringify(claims(NOW, { repository: TEST_REPO, run_id: "1" }))).toString("base64url");
    expect(await reason(`${h}.${forged}.${good.split(".")[2]}`)).toBe("SIGNATURE"); // payload swapped under the old signature
    const other = generateKeyPairSync("rsa", { modulusLength: 2048 }).privateKey;
    expect(await reason(oidcToken(claims(NOW), {}, other))).toBe("SIGNATURE");
    for (const bad of ["", "a.b", "a.b.c.d", "!!.??.--", `${h}.${p}.sig=`, "x".repeat(9000)]) expect(await reason(bad)).toBe("MALFORMED");
  });

  it("an unknown kid refetches the JWKS once (key rotation) and is rejected if still unknown", async () => {
    let calls = 0;
    const rotated = { ...testJwk, kid: "test-k2" };
    const cache = new JwksCache(async () => ({ keys: calls++ === 0 ? [testJwk] : [testJwk, rotated] }), 3_600_000, 0);
    expect(await reason(oidcToken(claims(NOW)), cfg, cache)).toBe("OK");
    expect(await reason(oidcToken(claims(NOW), { kid: "test-k2" }), cfg, cache)).toBe("OK");
    expect(calls).toBe(2);
    expect(await reason(oidcToken(claims(NOW), { kid: "nope" }), cfg, cache)).toBe("UNKNOWN_KEY");
    expect(calls).toBe(3);
    // refetches for unknown kids are rate limited
    const limited = new JwksCache(async () => { calls++; return { keys: [testJwk] }; });
    calls = 0;
    expect(await reason(oidcToken(claims(NOW), { kid: "a" }), cfg, limited)).toBe("UNKNOWN_KEY");
    expect(await reason(oidcToken(claims(NOW), { kid: "b" }), cfg, limited)).toBe("UNKNOWN_KEY");
    expect(calls).toBe(1);
  });

  it("a JWKS outage is an error, never a pass", async () => {
    const down = new JwksCache(async () => { throw new Error("ECONNRESET"); });
    await expect(verifyGithubOidcToken(oidcToken(claims(NOW)), cfg, { jwks: down, nowSeconds: NOW })).rejects.toThrow("ECONNRESET");
    // keys fetched earlier keep working through an outage for up to 24 h, then the outage surfaces
    let up = true;
    const flaky = new JwksCache(async () => {
      if (!up) throw new Error("ECONNRESET");
      return { keys: [testJwk] };
    }, 1000, 0);
    const at = (t: number) => verifyGithubOidcToken(oidcToken(claims(t)), cfg, { jwks: flaky, nowSeconds: t });
    await expect(at(NOW)).resolves.toMatchObject({ repository: TEST_REPO });
    up = false;
    await expect(at(NOW + 7200)).resolves.toMatchObject({ repository: TEST_REPO }); // TTL passed, refetch failed: stale keys used
    await expect(at(NOW + 25 * 3600)).rejects.toThrow("ECONNRESET");
    const junk = new JwksCache(async () => ({ keys: "nope" }));
    await expect(verifyGithubOidcToken(oidcToken(claims(NOW)), cfg, { jwks: junk, nowSeconds: NOW })).rejects.toThrow("JWKS malformed");
  });

  it("config: defaults, custom audience and ref, off switch", async () => {
    expect(releaseOidcConfig({})).toEqual({ repository: null, audience: "apex-release", ref: "main", off: false });
    const c = releaseOidcConfig({ APEX_RELEASE_GITHUB_REPOSITORY: ` ${TEST_REPO} `, APEX_RELEASE_OIDC_AUDIENCE: "apex-prod",
      APEX_RELEASE_GITHUB_REF: "refs/heads/release", APEX_RELEASE_OIDC: "OFF" });
    expect(c).toEqual({ repository: TEST_REPO, audience: "apex-prod", ref: "release", off: true });
    const on = { ...c, off: false };
    expect(await reason(oidcToken(claims(NOW)), on)).toBe("AUDIENCE");
    expect(await reason(oidcToken(claims(NOW, { aud: "apex-prod" })), on)).toBe("REF");
    expect(await reason(oidcToken(claims(NOW, { aud: "apex-prod", ref: "refs/heads/release",
      job_workflow_ref: `${TEST_REPO}/.github/workflows/apex-model-drift.yml@refs/heads/release` })), on)).toBe("OK");
  });
});
