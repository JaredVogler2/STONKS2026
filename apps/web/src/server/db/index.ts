import "server-only";
import { serverEnv } from "../env";
import { migrate, migrationStatus, type MigrationStatus } from "./migrate";
import type { Db } from "./types";

export type { Db, Sql, Row } from "./types";

interface DbState {
  db: Db;
  migrations: MigrationStatus;
  bootedAt: Date;
}

const g = globalThis as unknown as { __apexDb?: Promise<DbState> };

async function boot(): Promise<DbState> {
  const env = serverEnv();
  let db: Db;
  if (env.databaseUrl) {
    const { PostgresDb } = await import("./postgres");
    db = new PostgresDb(env.databaseUrl);
  } else {
    const { PgliteDb } = await import("./pglite");
    db = await PgliteDb.open(env.pgliteDir);
  }
  // Embedded DEMO databases migrate themselves. A real database is migrated explicitly by the operator
  // (`pnpm --filter @apex/web db:migrate`) unless APEX_AUTO_MIGRATE=1 is set deliberately.
  const auto = db.kind === "pglite" || process.env.APEX_AUTO_MIGRATE === "1";
  const migrations = auto ? await migrate(db) : await migrationStatus(db);
  if (db.kind === "pglite" && env.demoAutoSeed) {
    const { ensureDemoSeed } = await import("../services/demo");
    await ensureDemoSeed(db);
  }
  return { db, migrations, bootedAt: new Date() };
}

export async function getDbState(): Promise<DbState> {
  if (!g.__apexDb) {
    g.__apexDb = boot().catch((err) => {
      g.__apexDb = undefined;
      throw err;
    });
  }
  return g.__apexDb;
}

export async function getDb(): Promise<Db> {
  const state = await getDbState();
  if (state.migrations.pending.length || state.migrations.checksumMismatch.length) {
    throw new DatabaseNotReady(state.migrations);
  }
  return state.db;
}

export class DatabaseNotReady extends Error {
  constructor(public readonly status: MigrationStatus) {
    super(`database schema not ready: ${status.pending.length} pending migration(s)`);
  }
}

/** Test hook: replace the singleton (integration tests inject their own PGlite/Postgres). */
export function __setDbForTests(db: Db): void {
  g.__apexDb = Promise.resolve({ db, migrations: { applied: [], pending: [], checksumMismatch: [] }, bootedAt: new Date() });
}
