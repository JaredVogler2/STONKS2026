export type Row = Record<string, unknown>;

export interface Sql {
  query<T extends Row = Row>(text: string, params?: readonly unknown[]): Promise<T[]>;
  /** Multi-statement, parameterless SQL (migrations only). */
  exec(text: string): Promise<void>;
}

export interface Db extends Sql {
  readonly kind: "pglite" | "postgres";
  exec(text: string): Promise<void>;
  /** Run fn in one transaction with the connection's (privileged) role. */
  tx<T>(fn: (sql: Sql) => Promise<T>): Promise<T>;
  /**
   * Run fn in one transaction as the NOLOGIN `apex_member` role with apex.actor_user_id set.
   * Row-level security then restricts every position/notification table to that member.
   */
  asMember<T>(userId: string, fn: (sql: Sql) => Promise<T>): Promise<T>;
  close(): Promise<void>;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export async function enterMemberScope(sql: Sql, userId: string): Promise<void> {
  if (!UUID_RE.test(userId)) throw new Error("invalid actor id");
  await sql.query("SELECT set_config('apex.actor_user_id', $1, true)", [userId]);
  await sql.query("SET LOCAL ROLE apex_member");
}
