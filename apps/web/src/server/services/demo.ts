import "server-only";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Db } from "../db";
import { serverEnv } from "../env";
import { HttpError } from "../http/responses";
import { audit } from "./audit";
import {
  ackOutboxByEventKey, ingestAssessments, ingestCatalysts, ingestHeartbeat, ingestObservations, ingestScanRun, ingestSignal,
  ingestSignalEvents,
} from "./ingest";
import { resolveMember } from "./members";
import { promote, recordTrainingRun, registerCandidate } from "./models";
import { recordPositionEvent, type PositionEventInput } from "./positions";

/* DEMO mode: the deterministic seed (fixtures/demo/web-seed.json, produced by `apex export-web-seed`) is a timeline of
 * agent-API calls. They are replayed through the SAME ingestion services that serve the real Pi, so the DEMO dashboard
 * exercises production code paths. Nothing here runs outside DEMO mode. */

type Json = Record<string, unknown>;
interface SeedCall { op: string; body: Json }
interface SeedStep { session: string; at: string; calls: SeedCall[] }
interface Seed {
  version: number;
  agent_id: string;
  guild_id: string;
  owner_discord_id: string;
  members: Record<string, string>;
  trusted_model_keys: Record<string, string>;
  initial_steps: number;
  steps: SeedStep[];
}

let seedCache: Seed | null = null;

export function seedPath(): string {
  const candidates = [
    process.env.APEX_DEMO_SEED_PATH,
    join(process.cwd(), "../../fixtures/demo/web-seed.json"),
    join(process.cwd(), "fixtures/demo/web-seed.json"),
    join(process.cwd(), "apps/web/../../fixtures/demo/web-seed.json"),
  ].filter((p): p is string => Boolean(p));
  const found = candidates.find((p) => existsSync(p));
  if (!found) throw new Error("DEMO seed not found (fixtures/demo/web-seed.json); run `apex export-web-seed`");
  return found;
}

async function loadSeed(): Promise<Seed> {
  if (!seedCache) seedCache = JSON.parse(readFileSync(seedPath(), "utf8")) as Seed;
  return seedCache;
}

async function getState(db: Db): Promise<{ cursor: number; map: Record<string, string> } | null> {
  const r = await db.query<{ value_json: { cursor: number; map: Record<string, string> } }>("SELECT value_json FROM apex.settings WHERE key='demo.state'");
  return r[0]?.value_json ?? null;
}

async function setState(db: Db, cursor: number, map: Record<string, string>, at: string): Promise<void> {
  await db.query(
    `INSERT INTO apex.settings(key, value_json) VALUES ('demo.state', $1::jsonb)
     ON CONFLICT (key) DO UPDATE SET value_json = EXCLUDED.value_json, version = apex.settings.version + 1, updated_at = now()`,
    [JSON.stringify({ cursor, map })],
  );
  await db.query(
    `INSERT INTO apex.settings(key, value_json) VALUES ('demo.clock', $1::jsonb)
     ON CONFLICT (key) DO UPDATE SET value_json = EXCLUDED.value_json, updated_at = now()`,
    [JSON.stringify({ at })],
  );
}

async function bootstrap(db: Db, seed: Seed): Promise<void> {
  await db.query(
    `INSERT INTO apex.agent_devices(id, device_name, status, fingerprint, approved_at) VALUES ($1, 'DEMO replay device', 'active', 'DEMO-DEMO-DEMO-DEMO', now())
     ON CONFLICT (id) DO NOTHING`, [seed.agent_id]);
  await db.query("INSERT INTO apex.guilds(discord_guild_id, allowlisted, config_json) VALUES ($1, true, '{\"demo\":true}'::jsonb) ON CONFLICT DO NOTHING",
    [seed.guild_id]);
  await db.query(
    `INSERT INTO apex.app_users(discord_user_id, role, is_demo, dm_opt_in) VALUES ($1, 'owner', true, true)
     ON CONFLICT (discord_user_id) DO UPDATE SET role = 'owner'`, [seed.owner_discord_id]);
  await db.query(
    `INSERT INTO apex.settings(key, value_json) VALUES ('demo.trusted_model_keys', $1::jsonb) ON CONFLICT (key) DO NOTHING`,
    [JSON.stringify(seed.trusted_model_keys)]);
}

async function applyCall(db: Db, seed: Seed, call: SeedCall, at: string, map: Record<string, string>): Promise<void> {
  const b = call.body;
  switch (call.op) {
    case "heartbeat":
      await ingestHeartbeat(db, seed.agent_id, b, new Date(at));
      return;
    case "observations":
      await ingestObservations(db, b as { observations?: Json[]; bars?: Json[] });
      return;
    case "scan":
      await ingestScanRun(db, seed.agent_id, b);
      return;
    case "signal":
      await ingestSignal(db, seed.agent_id, b as never, "DEMO");
      return;
    case "signal_events":
      await ingestSignalEvents(db, b as { events: Json[]; intents?: Json[] });
      return;
    case "catalysts":
      await ingestCatalysts(db, (b.items as Json[]) ?? []);
      return;
    case "position_event": {
      const member = await resolveMember(db, String(b.discord_user_id), { create: true, guildId: String(b.guild_id) });
      await db.query("UPDATE apex.app_users SET is_demo = true, dm_opt_in = $2 WHERE id = $1", [member.id,
        String(b.discord_user_id) !== seed.members.C]);
      const ev = { ...(b.event as PositionEventInput) };
      if (ev.position_id) ev.position_id = map[ev.position_id] ?? ev.position_id;
      try {
        const res = await recordPositionEvent(db, member.id, ev, new Date(String(b.at)), true);
        if (b.py_position_id) map[String(b.py_position_id)] = res.position.position_id;
      } catch (err) {
        // intentionally rejected demo inputs (ledger rule violations) are expected and skipped; anything else is a bug
        if (!(err instanceof HttpError) || err.status >= 500) throw err;
      }
      return;
    }
    case "assessment": {
      const member = await resolveMember(db, String(b.discord_user_id), { create: true, guildId: seed.guild_id });
      const a = { ...(b.assessment as Json) };
      const pid = map[String(a.position_id)];
      if (!pid) return;
      a.position_id = pid;
      const intent = b.intent ? structuredClone(b.intent as Json) : undefined;
      if (intent) {
        intent.route = { type: "DM", user_id: member.id, discord_user_id: b.discord_user_id };
        (intent.payload as Json).assessment = a;
        intent.event_key = String(intent.event_key).replace(/^POS:[^:]+:[^:]+:/, `POS:${member.id}:${pid}:`);
      }
      await ingestAssessments(db, [{ user_id: member.id, assessment: a, assessment_key: String(b.assessment_key), intent }]);
      return;
    }
    case "outbox_ack":
      await ackOutboxByEventKey(db, seed.agent_id, String(b.event_key), b.status as "delivered" | "dm_blocked", b.rendered as Json | null);
      return;
    case "backtest":
      await db.query(
        `INSERT INTO apex.backtest_runs(id, strategy_id, config_hash, data_hash, code_hash, data_source, status, started_at, completed_at, metrics_json,
           bias_warnings, is_demo) VALUES ($1,$2,$3,$4,$5,$6,'completed',$7,$8,$9::jsonb,$10,true) ON CONFLICT (id) DO NOTHING`,
        [b.id, b.strategy_id, b.config_hash, b.data_hash, b.code_hash, b.data_source, b.started_at, b.completed_at, JSON.stringify(b.report),
          ((b.report as Json).bias_warnings as string[]) ?? []],
      );
      return;
    case "training_run":
      await recordTrainingRun(db, b);
      return;
    case "model_release": {
      const res = await registerCandidate(db, { manifest: b.manifest as Json, artifact_b64: String(b.artifact_b64) }, seed.trusted_model_keys, "DEMO");
      if (b.promote) await promote(db, res.model_id, { type: "system", id: "demo-seed" }, false, new Date(at));
      return;
    }
    default:
      return;
  }
}

async function applySteps(db: Db, seed: Seed, from: number, to: number, map: Record<string, string>): Promise<void> {
  for (let i = from; i < to && i < seed.steps.length; i++) {
    const step = seed.steps[i]!;
    for (const call of step.calls) await applyCall(db, seed, call, step.at, map);
    await setState(db, i + 1, map, step.at);
  }
}

export async function ensureDemoSeed(db: Db): Promise<void> {
  if (serverEnv().mode !== "DEMO") return;
  const state = await getState(db);
  if (state) return;
  const seed = await loadSeed();
  await bootstrap(db, seed);
  await applySteps(db, seed, 0, seed.initial_steps, {});
  await audit(db, { actorType: "system", entity: "settings", entityId: "demo.state", action: "demo.seeded",
    metadata: { steps: seed.initial_steps, total: seed.steps.length } });
}

export async function demoStatus(db: Db): Promise<{ cursor: number; total: number; session: string | null; next_session: string | null }> {
  const seed = await loadSeed();
  const st = await getState(db);
  const cursor = st?.cursor ?? 0;
  return { cursor, total: seed.steps.length, session: seed.steps[cursor - 1]?.session ?? null, next_session: seed.steps[cursor]?.session ?? null };
}

/** Advance the deterministic replay by `sessions` trading sessions (owner action in DEMO only). */
export async function advanceDemo(db: Db, ownerId: string, sessions = 1): Promise<{ cursor: number; total: number }> {
  if (serverEnv().mode !== "DEMO") throw new Error("replay is DEMO-only");
  const seed = await loadSeed();
  const st = (await getState(db)) ?? { cursor: 0, map: {} };
  const to = Math.min(seed.steps.length, st.cursor + Math.max(1, Math.min(10, sessions)));
  await applySteps(db, seed, st.cursor, to, st.map);
  await audit(db, { actorType: "owner", actorId: ownerId, entity: "settings", entityId: "demo.state", action: "demo.advanced", metadata: { to } });
  return { cursor: to, total: seed.steps.length };
}

export async function demoMembers(): Promise<Record<string, string>> {
  return (await loadSeed()).members;
}
