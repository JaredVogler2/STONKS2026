/*
 * Apply (or check) database migrations against DATABASE_URL. Run from your computer, never from the browser:
 *   DATABASE_URL=... pnpm --filter @apex/web db:migrate          apply pending migrations
 *   DATABASE_URL=... pnpm --filter @apex/web db:migrate --check  report only (exit 1 if anything is pending)
 * Prints versions and counts only; the connection string is never echoed.
 */
import { PostgresDb } from "../src/server/db/postgres";
import { migrate, migrationStatus } from "../src/server/db/migrate";

const url = process.env.DATABASE_URL?.trim();
if (!url) {
  console.error("DATABASE_URL is not set (use the Supabase transaction pooler URI).");
  process.exit(2);
}
const check = process.argv.includes("--check");
const db = new PostgresDb(url);
try {
  const before = await migrationStatus(db);
  console.log(`applied: ${before.applied.join(", ") || "none"}`);
  if (before.checksumMismatch.length) {
    console.error(`checksum mismatch (migration files were edited after being applied): ${before.checksumMismatch.join(", ")}`);
    process.exit(1);
  }
  if (!before.pending.length) {
    console.log("up to date");
  } else if (check) {
    console.log(`pending: ${before.pending.join(", ")}`);
    process.exit(1);
  } else {
    const after = await migrate(db);
    console.log(`applied now: ${before.pending.join(", ")}; pending: ${after.pending.join(", ") || "none"}`);
  }
  const rls = await db.query<{ n: number }>("SELECT count(*)::int AS n FROM pg_tables WHERE schemaname='apex' AND NOT rowsecurity");
  if (rls[0]?.n) {
    console.error(`${rls[0].n} apex table(s) without row-level security`);
    process.exit(1);
  }
  console.log("row-level security enabled on every apex table");
} finally {
  await db.close();
}
