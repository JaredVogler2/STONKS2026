import "server-only";
import postgres from "postgres";
import { enterMemberScope, type Db, type Row, type Sql } from "./types";

type Client = ReturnType<typeof postgres>;

/* Call sites pass json/jsonb parameters as JSON TEXT (JSON.stringify), as PGlite expects. postgres.js would JSON-encode
 * that text a second time and store a JSON string instead of an object, so strings pass through unchanged here. */
const jsonText = (v: unknown): string => (typeof v === "string" ? v : JSON.stringify(v));
const JSON_TYPES = {
  json: { to: 114, from: [114], serialize: jsonText, parse: JSON.parse },
  jsonb: { to: 3802, from: [3802], serialize: jsonText, parse: JSON.parse },
};

class PgSql implements Sql {
  constructor(private readonly c: Client | postgres.TransactionSql) {}
  async query<T extends Row = Row>(text: string, params: readonly unknown[] = []): Promise<T[]> {
    const rows = await this.c.unsafe(text, params as postgres.ParameterOrJSON<never>[]);
    return rows as unknown as T[];
  }
  async exec(text: string): Promise<void> {
    await this.c.unsafe(text);
  }
}

export class PostgresDb implements Db {
  readonly kind = "postgres" as const;
  private readonly client: Client;

  constructor(url: string) {
    this.client = postgres(url, {
      max: 3,
      idle_timeout: 20,
      connect_timeout: 10,
      prepare: false, // required for Supabase transaction pooler (Supavisor)
      ssl: /localhost|127\.0\.0\.1/.test(url) ? false : "require",
      transform: { undefined: null },
      types: JSON_TYPES,
      onnotice: () => undefined,
    });
  }

  query<T extends Row = Row>(text: string, params: readonly unknown[] = []): Promise<T[]> {
    return new PgSql(this.client).query<T>(text, params);
  }

  exec(text: string): Promise<void> {
    return new PgSql(this.client).exec(text);
  }

  async tx<T>(fn: (sql: Sql) => Promise<T>): Promise<T> {
    return (await this.client.begin((t) => fn(new PgSql(t)))) as T;
  }

  async asMember<T>(userId: string, fn: (sql: Sql) => Promise<T>): Promise<T> {
    return (await this.client.begin(async (t) => {
      const sql = new PgSql(t);
      await enterMemberScope(sql, userId);
      return fn(sql);
    })) as T;
  }

  async close(): Promise<void> {
    await this.client.end({ timeout: 5 });
  }
}
