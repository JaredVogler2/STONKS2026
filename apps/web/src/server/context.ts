import "server-only";
import { getDb, type Db } from "./db";
import { serverEnv, type ReleaseMode } from "./env";

export interface AppContext {
  db: Db;
  mode: ReleaseMode;
  isDemo: boolean;
  /** Real wall clock in live modes; the replay cursor in DEMO so ages and expiries stay coherent. */
  now(): Promise<Date>;
}

export async function demoClock(db: Db): Promise<Date | null> {
  const rows = await db.query<{ value_json: { at: string } }>("SELECT value_json FROM apex.settings WHERE key = 'demo.clock'");
  const at = rows[0]?.value_json?.at;
  return at ? new Date(at) : null;
}

export async function appContext(): Promise<AppContext> {
  const env = serverEnv();
  const db = await getDb();
  const isDemo = env.mode === "DEMO";
  return {
    db,
    mode: env.mode,
    isDemo,
    async now() {
      if (isDemo) return (await demoClock(db)) ?? new Date();
      return new Date();
    },
  };
}
