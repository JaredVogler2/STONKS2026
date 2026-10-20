import "server-only";
import { PGlite, type Transaction } from "@electric-sql/pglite";
import { mkdirSync } from "node:fs";
import { enterMemberScope, type Db, type Row, type Sql } from "./types";

class TxSql implements Sql {
  constructor(private readonly tx: Transaction) {}
  async query<T extends Row = Row>(text: string, params: readonly unknown[] = []): Promise<T[]> {
    const res = await this.tx.query<T>(text, params as unknown[]);
    return res.rows;
  }
  async exec(text: string): Promise<void> {
    await this.tx.exec(text);
  }
}

export class PgliteDb implements Db {
  readonly kind = "pglite" as const;
  private chain: Promise<unknown> = Promise.resolve();

  private constructor(private readonly pg: PGlite) {}

  static async open(dir: string | null): Promise<PgliteDb> {
    if (dir) mkdirSync(dir, { recursive: true });
    const pg = dir ? new PGlite(dir) : new PGlite();
    await pg.waitReady;
    return new PgliteDb(pg);
  }

  // PGlite is single-connection: serialize work so transactions never interleave.
  private serial<T>(fn: () => Promise<T>): Promise<T> {
    const run = this.chain.then(fn, fn);
    this.chain = run.catch(() => undefined);
    return run;
  }

  query<T extends Row = Row>(text: string, params: readonly unknown[] = []): Promise<T[]> {
    return this.serial(async () => (await this.pg.query<T>(text, params as unknown[])).rows);
  }

  exec(text: string): Promise<void> {
    return this.serial(async () => {
      await this.pg.exec(text);
    });
  }

  tx<T>(fn: (sql: Sql) => Promise<T>): Promise<T> {
    return this.serial(() => this.pg.transaction((t) => fn(new TxSql(t))));
  }

  asMember<T>(userId: string, fn: (sql: Sql) => Promise<T>): Promise<T> {
    return this.serial(() =>
      this.pg.transaction(async (t) => {
        const sql = new TxSql(t);
        await enterMemberScope(sql, userId);
        return fn(sql);
      }),
    );
  }

  async close(): Promise<void> {
    await this.pg.close();
  }
}
