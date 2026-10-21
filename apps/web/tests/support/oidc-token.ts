import { generateKeyPairSync, randomUUID, sign, type KeyObject } from "node:crypto";

/* TEST-ONLY GitHub-Actions-shaped OIDC tokens signed with a throwaway RSA key generated per test run. */
export const TEST_REPO = "octo-org/apex";
const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
export const testKeyPair = { privateKey, publicKey };
export const testJwk = { ...(publicKey.export({ format: "jwk" }) as Record<string, unknown>), kid: "test-k1", use: "sig", alg: "RS256" };

export function claims(now: number, over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    iss: "https://token.actions.githubusercontent.com",
    aud: "apex-release",
    sub: `repo:${TEST_REPO}:environment:model-release`,
    repository: TEST_REPO,
    repository_owner: "octo-org",
    environment: "model-release",
    ref: "refs/heads/main",
    event_name: "schedule",
    workflow_ref: `${TEST_REPO}/.github/workflows/apex-model-train-weekly.yml@refs/heads/main`,
    job_workflow_ref: `${TEST_REPO}/.github/workflows/apex-model-train-weekly.yml@refs/heads/main`,
    run_id: "777001",
    run_attempt: "2",
    sha: "c".repeat(40),
    jti: randomUUID(),
    nbf: now - 60,
    iat: now,
    exp: now + 300,
    ...over,
  };
}

export function oidcToken(payload: Record<string, unknown>, header: Record<string, unknown> = {}, key: KeyObject = privateKey): string {
  const h = Buffer.from(JSON.stringify({ alg: "RS256", typ: "JWT", kid: "test-k1", ...header })).toString("base64url");
  const p = Buffer.from(JSON.stringify(payload)).toString("base64url");
  const s = sign("sha256", Buffer.from(`${h}.${p}`), key).toString("base64url");
  return `${h}.${p}.${s}`;
}
