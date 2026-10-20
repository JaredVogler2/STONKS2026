import "server-only";
import { createHash } from "node:crypto";
import type { Db, Sql } from "../db";
import { HttpError } from "../http/responses";
import { canonicalJson } from "../security/canonical";
import { verifyEd25519 } from "../security/ed25519";
import { audit } from "./audit";

/* Private signed model registry (laptop-free training amendment 2026-10-20).
 * - CI uploads a candidate (manifest + JSON artifact) through the release API; the server verifies the Ed25519
 *   signature against trusted public keys and the artifact sha256 BEFORE storing anything.
 * - Promotion re-applies the frozen policy server-side; synthetic models can only become DEMO champions.
 * - The Pi fetches champions with its agent identity and re-verifies everything itself. */

type Json = Record<string, unknown>;
const MANIFEST_PREFIX = "apex-model-manifest-v1\n";
const MAX_ARTIFACT = 2_000_000;
export const MIN_SKILL_GAIN = 0.005;

export function trustedModelKeys(): Record<string, string> {
  const raw = process.env.APEX_MODEL_TRUSTED_KEYS;
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as Record<string, string>;
    return Object.fromEntries(Object.entries(parsed).filter(([, v]) => /^[A-Za-z0-9_-]{43}$/.test(v)));
  } catch {
    return {};
  }
}

export function verifyManifest(manifest: Json, artifact: Buffer, keys: Record<string, string>): void {
  const sig = manifest.signature;
  if (typeof sig !== "string") throw new HttpError(422, "UNSIGNED_MANIFEST", "manifest is not signed");
  const key = keys[String(manifest.signer_key_version)];
  if (!key) throw new HttpError(422, "UNTRUSTED_SIGNER", "manifest signer key is not trusted by this deployment");
  const body = { ...manifest };
  delete body.signature;
  const message = Buffer.from(MANIFEST_PREFIX + canonicalJson(body), "utf8");
  if (!verifyEd25519(key, message, sig)) throw new HttpError(422, "BAD_SIGNATURE", "manifest signature invalid");
  if (artifact.length > MAX_ARTIFACT || artifact.length !== manifest.artifact_size_bytes) {
    throw new HttpError(422, "SIZE_MISMATCH", "artifact size mismatch or too large");
  }
  if (createHash("sha256").update(artifact).digest("hex") !== manifest.artifact_sha256) {
    throw new HttpError(422, "CHECKSUM_MISMATCH", "artifact sha256 does not match the signed manifest");
  }
  // structural guard: the artifact must be plain JSON of the supported kind (never executable formats)
  let parsed: Json;
  try {
    parsed = JSON.parse(artifact.toString("utf8")) as Json;
  } catch {
    throw new HttpError(422, "ARTIFACT_INVALID", "artifact is not JSON");
  }
  if (parsed.kind !== manifest.model_kind || manifest.model_kind !== "logistic_competing_v1") {
    throw new HttpError(422, "UNSUPPORTED_KIND", "unsupported model kind");
  }
}

export interface RegisterInput {
  manifest: Json;
  artifact_b64: string;
  training_run_id?: string | null;
}

export async function registerCandidate(db: Db, input: RegisterInput, keys = trustedModelKeys(), mode = "DEMO"): Promise<{ model_id: string; created: boolean }> {
  const m = input.manifest;
  const artifact = Buffer.from(input.artifact_b64, "base64");
  verifyManifest(m, artifact, keys);
  const isDemo = Boolean(m.is_demo);
  if (m.data_source === "synthetic_fixture" && !isDemo) throw new HttpError(422, "SYNTHETIC_NOT_DEMO", "synthetic models must be DEMO");
  if (mode !== "DEMO" && isDemo) throw new HttpError(422, "MODE_MISMATCH", "DEMO models are not accepted by a live deployment");
  const ev = (m.evaluation ?? {}) as Json;
  return db.tx(async (sql) => {
    const r = await sql.query<{ id: string }>(
      `INSERT INTO apex.model_versions(id, version_uuid, strategy_id, model_kind, feature_schema_version, features_hash, label_definition,
         training_window, validation_window, training_data_hash, code_sha, calibration_metrics, evaluation_json, decision_thresholds, drift_criteria,
         data_source, artifact_size_bytes, artifact_sha256, manifest_json, signature, signer_key_version, storage_uri, status, rollback_parent,
         validation_expires_at, training_run_id, created_at, is_demo)
       VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,$10,$11,$12::jsonb,$13::jsonb,$14::jsonb,$15::jsonb,$16,$17,$18,$19::jsonb,$20,$21,$22,
         'candidate',NULL,$23,$24,$25,$26)
       ON CONFLICT (id) DO NOTHING RETURNING id`,
      [m.model_id, m.version_uuid, m.strategy_id, m.model_kind, m.feature_schema_version, m.features_hash, JSON.stringify(m.label_definition),
        JSON.stringify(m.training_window), JSON.stringify(m.validation_window), m.training_data_hash, m.code_sha,
        JSON.stringify({ brier_skill: ev.brier_skill, ece: ev.ece, holdout_n: ev.holdout_n }), JSON.stringify(ev),
        JSON.stringify({ threshold_p_target: ev.threshold_p_target }), JSON.stringify(m.drift_criteria ?? {}), m.data_source, m.artifact_size_bytes,
        m.artifact_sha256, JSON.stringify(m), m.signature, m.signer_key_version, m.storage_uri, m.validation_expires_at,
        input.training_run_id ?? null, m.created_at, isDemo],
    );
    if (r.length) {
      await sql.query("INSERT INTO apex.model_artifacts(model_id, artifact) VALUES ($1, $2)", [m.model_id, artifact]);
      await audit(sql, { actorType: "agent", actorId: "release", entity: "model_versions", entityId: String(m.model_id), action: "model.candidate_registered",
        metadata: { strategy: m.strategy_id, status: ev.confidence_status } });
    }
    return { model_id: String(m.model_id), created: r.length > 0 };
  });
}

function num(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Frozen server-side promotion policy (mirrors research/evaluate.challenger_beats_champion). */
export function promotionDecision(candidate: Json, champion: Json | null): { ok: boolean; reason: string } {
  const ce = (candidate.evaluation ?? {}) as Json;
  if (ce.confidence_status === "UNVALIDATED") return { ok: false, reason: "candidate failed validation gates" };
  if (!champion) return { ok: true, reason: "no incumbent champion" };
  const pe = (champion.evaluation ?? {}) as Json;
  const cs = num(ce.brier_skill);
  const ps = num(pe.brier_skill);
  if (cs === null || ps === null) return { ok: false, reason: "missing comparable holdout skill" };
  if (cs < ps + MIN_SKILL_GAIN) return { ok: false, reason: `skill gain ${(cs - ps).toFixed(4)} below required ${MIN_SKILL_GAIN}` };
  const cl = num(ce.selected_mean_lower_p10);
  const pl = num(pe.selected_mean_lower_p10);
  if (cl !== null && pl !== null && cl < pl) return { ok: false, reason: "economic lower bound worse than champion" };
  return { ok: true, reason: "improves calibrated skill without weaker economics" };
}

async function currentChampion(sql: Sql, strategyId: string, isDemo: boolean): Promise<{ id: string; manifest_json: Json } | null> {
  const r = await sql.query<{ id: string; manifest_json: Json }>(
    "SELECT id, manifest_json FROM apex.model_versions WHERE strategy_id = $1 AND is_demo = $2 AND status = 'champion' FOR UPDATE",
    [strategyId, isDemo],
  );
  return r[0] ?? null;
}

export async function promote(db: Db, modelId: string, actor: { type: "owner" | "system"; id: string }, force = false, now: Date = new Date()): Promise<{ promoted: boolean; reason: string }> {
  return db.tx(async (sql) => {
    const rows = await sql.query<{ id: string; strategy_id: string; is_demo: boolean; status: string; manifest_json: Json; validation_expires_at: Date }>(
      "SELECT id, strategy_id, is_demo, status, manifest_json, validation_expires_at FROM apex.model_versions WHERE id = $1 FOR UPDATE", [modelId]);
    const m = rows[0];
    if (!m) throw new HttpError(404, "NOT_FOUND", "model not found");
    if (m.status === "champion") return { promoted: false, reason: "already champion" };
    if (new Date(m.validation_expires_at).getTime() < now.getTime()) return { promoted: false, reason: "validation expired" };
    const champ = await currentChampion(sql, m.strategy_id, m.is_demo);
    const decision = promotionDecision(m.manifest_json, champ?.manifest_json ?? null);
    if (!decision.ok && !force) {
      await sql.query("UPDATE apex.model_versions SET promotion_report = $2::jsonb WHERE id = $1", [modelId, JSON.stringify(decision)]);
      return { promoted: false, reason: decision.reason };
    }
    if ((m.manifest_json.evaluation as Json)?.confidence_status === "UNVALIDATED") return { promoted: false, reason: "UNVALIDATED models are never promoted" };
    if (champ) await sql.query("UPDATE apex.model_versions SET status = 'retired' WHERE id = $1", [champ.id]);
    await sql.query(
      "UPDATE apex.model_versions SET status='champion', promoted_at=$5, promoted_by=$2, rollback_parent=$3, promotion_report=$4::jsonb WHERE id=$1",
      [modelId, `${actor.type}:${actor.id}`, champ?.id ?? null, JSON.stringify({ ...decision, forced: force && !decision.ok }), now],
    );
    await audit(sql, { actorType: actor.type, actorId: actor.id, entity: "model_versions", entityId: modelId, action: "model.promoted",
      metadata: { strategy: m.strategy_id, replaced: champ?.id ?? null, forced: force && !decision.ok } });
    await pruneRegistry(sql, m.strategy_id, m.is_demo);
    return { promoted: true, reason: decision.reason };
  });
}

export async function rollback(db: Db, strategyId: string, isDemo: boolean, actor: { type: "owner" | "system"; id: string }, now: Date = new Date()): Promise<{ rolled_back: boolean; model_id?: string; reason: string }> {
  return db.tx(async (sql) => {
    const champ = await sql.query<{ id: string; rollback_parent: string | null }>(
      "SELECT id, rollback_parent FROM apex.model_versions WHERE strategy_id=$1 AND is_demo=$2 AND status='champion' FOR UPDATE", [strategyId, isDemo]);
    const c = champ[0];
    if (!c?.rollback_parent) return { rolled_back: false, reason: "no previous champion to roll back to" };
    await sql.query("UPDATE apex.model_versions SET status='rolled_back' WHERE id=$1", [c.id]);
    await sql.query("UPDATE apex.model_versions SET status='champion', promoted_at=$3, promoted_by=$2 WHERE id=$1", [c.rollback_parent,
      `${actor.type}:${actor.id}`, now]);
    await audit(sql, { actorType: actor.type, actorId: actor.id, entity: "model_versions", entityId: c.rollback_parent, action: "model.rolled_back",
      metadata: { from: c.id } });
    return { rolled_back: true, model_id: c.rollback_parent, reason: "previous champion restored" };
  });
}

/** Keep only champion + 2 most recent other versions per strategy (Supabase Free storage budget). */
async function pruneRegistry(sql: Sql, strategyId: string, isDemo: boolean): Promise<void> {
  await sql.query(
    `DELETE FROM apex.model_versions WHERE id IN (
       SELECT id FROM apex.model_versions WHERE strategy_id = $1 AND is_demo = $2 AND status <> 'champion'
         AND id NOT IN (SELECT rollback_parent FROM apex.model_versions WHERE status='champion' AND rollback_parent IS NOT NULL)
       ORDER BY registered_at DESC OFFSET 2)`,
    [strategyId, isDemo],
  );
}

export async function championsForAgent(db: Db, isDemo: boolean): Promise<Json[]> {
  const rows = await db.query<{ manifest_json: Json }>(
    "SELECT manifest_json FROM apex.model_versions WHERE status='champion' AND is_demo=$1 ORDER BY strategy_id", [isDemo]);
  return rows.map((r) => r.manifest_json);
}

export async function artifactForAgent(db: Db, modelId: string): Promise<string> {
  const rows = await db.query<{ artifact: Uint8Array; status: string }>(
    "SELECT a.artifact, v.status FROM apex.model_artifacts a JOIN apex.model_versions v ON v.id = a.model_id WHERE a.model_id = $1", [modelId]);
  const r = rows[0];
  if (!r || !["champion", "retired", "rolled_back"].includes(r.status)) throw new HttpError(404, "NOT_FOUND", "model artifact not available");
  return Buffer.from(r.artifact).toString("base64");
}

export async function recordActivation(db: Db, agentId: string, body: { model_id: string; status: string; detail?: string }): Promise<void> {
  if (!["activated", "rejected", "rolled_back", "expired"].includes(body.status)) throw new HttpError(422, "INVALID_STATUS", "bad activation status");
  await db.query("INSERT INTO apex.model_activations(agent_id, model_id, status, detail) VALUES ($1,$2,$3,$4)", [
    agentId, String(body.model_id).slice(0, 60), body.status, body.detail?.slice(0, 240) ?? null]);
}

export async function recordTrainingRun(db: Db, run: Json): Promise<string> {
  const rows = await db.query<{ id: string }>(
    `INSERT INTO apex.training_runs(workflow, trigger, github_run_id, github_run_attempt, code_sha, started_at, finished_at, status, outcome,
       candidate_model_id, minutes_used, minutes_budget_month, minutes_used_month_estimate, report_json, is_demo)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,$15)
     ON CONFLICT (workflow, github_run_id, github_run_attempt) DO UPDATE SET finished_at = EXCLUDED.finished_at, status = EXCLUDED.status,
       outcome = EXCLUDED.outcome, minutes_used = EXCLUDED.minutes_used, report_json = EXCLUDED.report_json
     RETURNING id`,
    [run.workflow, run.trigger, run.github_run_id ?? null, run.github_run_attempt ?? 1, run.code_sha ?? null, run.started_at, run.finished_at ?? null,
      run.status, run.outcome ?? null, run.candidate_model_id ?? null, run.minutes_used ?? null, run.minutes_budget_month ?? null,
      run.minutes_used_month_estimate ?? null, JSON.stringify(run.report ?? {}), Boolean(run.is_demo)],
  );
  return rows[0]!.id;
}

export async function championSummaries(db: Db, isDemo: boolean): Promise<Record<string, Json>> {
  const rows = await db.query<{ strategy_id: string; manifest_json: Json; id: string }>(
    "SELECT strategy_id, manifest_json, id FROM apex.model_versions WHERE status='champion' AND is_demo=$1", [isDemo]);
  return Object.fromEntries(rows.map((r) => [r.strategy_id, { model_id: r.id, evaluation: r.manifest_json.evaluation,
    holdout: { brier_skill: num((r.manifest_json.evaluation as Json)?.brier_skill) },
    gates: { selected_mean_lower_p10: num((r.manifest_json.evaluation as Json)?.selected_mean_lower_p10) },
    confidence_status: (r.manifest_json.evaluation as Json)?.confidence_status }]));
}
