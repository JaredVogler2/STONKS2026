import "server-only";
import { MIGRATIONS } from "./migrations.generated";
import type { Db } from "./types";

export interface MigrationStatus {
  applied: string[];
  pending: string[];
  checksumMismatch: string[];
}

async function ensureTracking(db: Db): Promise<void> {
  await db.exec("CREATE SCHEMA IF NOT EXISTS apex");
  await db.exec(
    "CREATE TABLE IF NOT EXISTS apex.schema_migrations (version text PRIMARY KEY, name text NOT NULL, checksum text NOT NULL, " +
      "applied_at timestamptz NOT NULL DEFAULT now()); ALTER TABLE apex.schema_migrations ENABLE ROW LEVEL SECURITY;",
  );
}

export async function migrationStatus(db: Db): Promise<MigrationStatus> {
  const exists = await db.query<{ ok: boolean }>(
    "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='apex' AND tablename='schema_migrations') AS ok",
  );
  const rows = exists[0]?.ok
    ? await db.query<{ version: string; checksum: string }>("SELECT version, checksum FROM apex.schema_migrations")
    : [];
  const applied = new Map(rows.map((r) => [r.version, r.checksum]));
  return {
    applied: [...applied.keys()].sort(),
    pending: MIGRATIONS.filter((m) => !applied.has(m.version)).map((m) => m.version),
    checksumMismatch: MIGRATIONS.filter((m) => applied.has(m.version) && applied.get(m.version) !== m.checksum).map(
      (m) => m.version,
    ),
  };
}

/** Apply pending migrations in order, each in its own transaction. Refuses to run over edited history. */
export async function migrate(db: Db): Promise<MigrationStatus> {
  await ensureTracking(db);
  const status = await migrationStatus(db);
  if (status.checksumMismatch.length) {
    throw new Error(`applied migrations were edited after deployment: ${status.checksumMismatch.join(", ")}`);
  }
  for (const m of MIGRATIONS) {
    if (!status.pending.includes(m.version)) continue;
    await db.tx(async (sql) => {
      await sql.exec(m.sql);
      await sql.query("INSERT INTO apex.schema_migrations(version, name, checksum) VALUES ($1, $2, $3)", [
        m.version,
        m.name,
        m.checksum,
      ]);
    });
  }
  return migrationStatus(db);
}
