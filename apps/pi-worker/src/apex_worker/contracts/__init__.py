"""Runtime validation against the canonical JSON Schemas (packages/contracts/schemas).

`models.py` holds Pydantic types generated from the same schemas; this module validates raw
payloads at process boundaries (agent API requests/responses, fixtures, Discord payload data).
"""

from __future__ import annotations

import functools
import json
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

SCHEMA_NAMES = {
    "AuditEvent": "audit-event.schema.json",
    "Catalyst": "catalyst.schema.json",
    "FeatureSnapshot": "feature-snapshot.schema.json",
    "MarketBar": "market-bar.schema.json",
    "MarketObservation": "market-observation.schema.json",
    "NotificationIntent": "notification-intent.schema.json",
    "PositionAssessment": "position-assessment.schema.json",
    "PositionEvent": "position-event.schema.json",
    "PositionFill": "position-fill.schema.json",
    "PositionSummary": "position-summary.schema.json",
    "ServiceHealth": "service-health.schema.json",
    "SignalCandidate": "signal-candidate.schema.json",
    "SignalEvent": "signal-event.schema.json",
    "TradeSignal": "trade-signal.schema.json",
    "WorkerHeartbeat": "worker-heartbeat.schema.json",
}


class ContractViolation(ValueError):
    def __init__(self, name: str, errors: list[str]):
        super().__init__(f"{name} contract violation: " + "; ".join(errors[:5]))
        self.name = name
        self.errors = errors


@functools.cache
def _load_all() -> dict[str, dict]:
    pkg = resources.files("apex_worker.contracts") / "schemas"
    out = {}
    for entry in pkg.iterdir():
        if entry.name.endswith(".schema.json"):
            out[entry.name] = json.loads(entry.read_text(encoding="utf-8"))
    return out


@functools.cache
def _registry() -> Registry:
    resources_ = []
    for fname, schema in _load_all().items():
        res = Resource.from_contents(schema)
        resources_.append((fname, res))
        resources_.append((schema["$id"], res))
    return Registry().with_resources(resources_)


@functools.cache
def validator(name: str) -> Draft202012Validator:
    fname = SCHEMA_NAMES[name]
    schema = _load_all()[fname]
    return Draft202012Validator(schema, registry=_registry(), format_checker=FormatChecker())


def validate(name: str, payload: Any) -> Any:
    errors = sorted(validator(name).iter_errors(payload), key=lambda e: list(e.absolute_path))
    if errors:
        raise ContractViolation(
            name, [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in errors]
        )
    return payload


def is_valid(name: str, payload: Any) -> bool:
    return validator(name).is_valid(payload)
