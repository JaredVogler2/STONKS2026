import type { Db } from "@/server/db";

/**
 * Integration tests run on embedded PGlite by default. Set APEX_TEST_DATABASE_URL to a SCRATCH Postgres (for example
 * `scripts/local-postgres.sh url`, which connects as a non-superuser like Supabase) to run the same suites there.
 * The apex schema in that database is dropped and recreated: never point this at real data.
 */
export async function openTestDb(): Promise<Db> {
  const url = process.env.APEX_TEST_DATABASE_URL;
  if (!url) {
    const { PgliteDb } = await import("@/server/db/pglite");
    return PgliteDb.open(null);
  }
  if (/supabase\.(co|com)/.test(url)) throw new Error("refusing to run destructive tests against a Supabase URL");
  const { PostgresDb } = await import("@/server/db/postgres");
  const db = new PostgresDb(url);
  await db.exec("DROP SCHEMA IF EXISTS apex CASCADE");
  return db;
}
