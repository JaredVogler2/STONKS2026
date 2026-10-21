# 0004 — JSON Schema contracts

**Decision.** `packages/contracts/schemas/*.schema.json` define every payload that crosses a boundary: market observations, signals, events, positions, assessments, notifications, heartbeats and catalysts.
- TypeScript types and Pydantic models are generated from them.
- `--check` mode fails CI on drift.
- Both sides validate at runtime: Ajv 2020 in TypeScript, jsonschema in Python.

**Alternatives.** Hand-written types on both sides, or protobuf. Hand-written types drift; protobuf adds a toolchain on the Pi and hurts human-readable audit payloads.

**Consequences.** Schema changes are deliberate and reviewed, and money and quantities travel as decimal strings.
