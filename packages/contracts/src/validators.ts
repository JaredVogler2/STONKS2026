import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import common from "../schemas/common.schema.json" with { type: "json" };
import auditEvent from "../schemas/audit-event.schema.json" with { type: "json" };
import catalyst from "../schemas/catalyst.schema.json" with { type: "json" };
import featureSnapshot from "../schemas/feature-snapshot.schema.json" with { type: "json" };
import marketBar from "../schemas/market-bar.schema.json" with { type: "json" };
import marketObservation from "../schemas/market-observation.schema.json" with { type: "json" };
import notificationIntent from "../schemas/notification-intent.schema.json" with { type: "json" };
import positionAssessment from "../schemas/position-assessment.schema.json" with { type: "json" };
import positionEvent from "../schemas/position-event.schema.json" with { type: "json" };
import positionFill from "../schemas/position-fill.schema.json" with { type: "json" };
import positionSummary from "../schemas/position-summary.schema.json" with { type: "json" };
import serviceHealth from "../schemas/service-health.schema.json" with { type: "json" };
import signalCandidate from "../schemas/signal-candidate.schema.json" with { type: "json" };
import signalEvent from "../schemas/signal-event.schema.json" with { type: "json" };
import tradeSignal from "../schemas/trade-signal.schema.json" with { type: "json" };
import workerHeartbeat from "../schemas/worker-heartbeat.schema.json" with { type: "json" };

const SCHEMAS = {
  AuditEvent: auditEvent,
  Catalyst: catalyst,
  FeatureSnapshot: featureSnapshot,
  MarketBar: marketBar,
  MarketObservation: marketObservation,
  NotificationIntent: notificationIntent,
  PositionAssessment: positionAssessment,
  PositionEvent: positionEvent,
  PositionFill: positionFill,
  PositionSummary: positionSummary,
  ServiceHealth: serviceHealth,
  SignalCandidate: signalCandidate,
  SignalEvent: signalEvent,
  TradeSignal: tradeSignal,
  WorkerHeartbeat: workerHeartbeat,
} as const;

export type ContractName = keyof typeof SCHEMAS;

let ajv: Ajv2020 | null = null;
const compiled = new Map<ContractName, ValidateFunction>();

function instance(): Ajv2020 {
  if (ajv) return ajv;
  // Ajv's CJS/ESM interop varies by bundler; handle both default shapes.
  const AjvCtor = ((Ajv2020 as unknown as { default?: typeof Ajv2020 }).default ?? Ajv2020) as typeof Ajv2020;
  const add = ((addFormats as unknown as { default?: typeof addFormats }).default ?? addFormats) as typeof addFormats;
  ajv = new AjvCtor({ allErrors: true, strict: true, allowUnionTypes: true, validateFormats: true });
  add(ajv);
  ajv.addSchema(common, "common.schema.json");
  for (const [name, schema] of Object.entries(SCHEMAS)) {
    const id = (schema as { $id: string }).$id;
    const file = id.split("/").pop()!;
    ajv.addSchema(schema, file);
    void name;
  }
  return ajv;
}

function validatorFor(name: ContractName): ValidateFunction {
  let v = compiled.get(name);
  if (!v) {
    const file = (SCHEMAS[name] as { $id: string }).$id.split("/").pop()!;
    v = instance().getSchema(file)!;
    compiled.set(name, v);
  }
  return v;
}

export type ValidationResult<T> = { ok: true; value: T } | { ok: false; errors: string[] };

export function validateContract<T>(name: ContractName, data: unknown): ValidationResult<T> {
  const v = validatorFor(name);
  if (v(data)) return { ok: true, value: data as T };
  return { ok: false, errors: (v.errors ?? []).map((e: ErrorObject) => `${e.instancePath || "<root>"} ${e.message ?? "invalid"}`) };
}
