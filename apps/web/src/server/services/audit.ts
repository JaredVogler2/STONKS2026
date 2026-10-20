import "server-only";
import type { Sql } from "../db";

const SENSITIVE = /(secret|token|password|passphrase|ciphertext|private|authorization|cookie|api_?key|signature)/i;
const PERSONAL = /(price|quantity|qty|fees|pnl|cost|fill|notes)/i;

/** Redact secret-looking and personal-financial keys recursively. Audit rows must never hold either. */
export function redact(value: unknown, depth = 0): unknown {
  if (depth > 4) return "[TRUNCATED]";
  if (Array.isArray(value)) return value.slice(0, 20).map((v) => redact(v, depth + 1));
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      if (SENSITIVE.test(k)) out[k] = "[REDACTED]";
      else if (PERSONAL.test(k)) out[k] = "[PRIVATE]";
      else out[k] = redact(v, depth + 1);
    }
    return out;
  }
  if (typeof value === "string") return value.length > 200 ? `${value.slice(0, 200)}…` : value;
  return value;
}

export interface AuditInput {
  actorType: "owner" | "agent" | "member" | "system";
  actorId?: string | null;
  entity: string;
  entityId?: string | null;
  action: string;
  metadata?: Record<string, unknown>;
  correlationId?: string | null;
}

export async function audit(sql: Sql, a: AuditInput): Promise<void> {
  await sql.query(
    `INSERT INTO apex.audit_events(actor_type, actor_id, entity, entity_id, action, redacted_metadata_json, correlation_id)
     VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)`,
    [a.actorType, a.actorId ?? null, a.entity, a.entityId ?? null, a.action, JSON.stringify(redact(a.metadata ?? {})),
      a.correlationId ?? null],
  );
}

export async function securityEvent(sql: Sql, kind: string, detail: { agentId?: string | null; route?: string; detail?: string }) {
  await sql.query("INSERT INTO apex.security_events(kind, agent_id, route, detail) VALUES ($1, $2, $3, $4)", [
    kind,
    detail.agentId ?? null,
    detail.route ?? null,
    (detail.detail ?? "").slice(0, 200) || null,
  ]);
}
