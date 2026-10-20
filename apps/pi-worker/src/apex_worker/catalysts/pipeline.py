"""Catalyst pipeline: capability-gated collection -> dedupe -> contract-valid dicts -> point-in-time view."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from apex_worker.catalysts.dedupe import Cluster, dedupe
from apex_worker.catalysts.http import QuarantineRecord, QuarantineStore, quarantine_record
from apex_worker.catalysts.models import AvailabilityMode, CatalystRecord, is_available
from apex_worker.catalysts.scoring import apply_score
from apex_worker.catalysts.sources import CatalystSource, CollectContext, SourceState
from apex_worker.catalysts.tickers import TickerMap
from apex_worker.contracts import ContractViolation
from apex_worker.timeutil import ET, Clock, SystemClock, ensure_utc, parse_utc


@dataclass
class PipelineResult:
    catalysts: list[dict[str, Any]]
    records: list[CatalystRecord]
    clusters: list[Cluster]
    quarantined: list[QuarantineRecord]
    capabilities: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)


class CatalystPipeline:
    def __init__(
        self,
        sources: Sequence[CatalystSource],
        *,
        mode: AvailabilityMode | str = AvailabilityMode.LIVE,
        is_demo: bool = False,
        ticker_map: TickerMap | None = None,
        clock: Clock | None = None,
        quarantine: QuarantineStore | None = None,
    ):
        self.sources = list(sources)
        self.mode = AvailabilityMode(mode)
        self.is_demo = is_demo
        self.ticker_map = ticker_map
        self.clock = clock or SystemClock()
        self.quarantine = quarantine or QuarantineStore()

    def capabilities(self) -> list[dict[str, Any]]:
        return [s.capability().to_dict() for s in self.sources]

    def run(
        self, *, first_seen_at: datetime | None = None, score_at: datetime | None = None
    ) -> PipelineResult:
        now = ensure_utc(first_seen_at or self.clock.now())
        q_start = len(self.quarantine.records)
        ctx = CollectContext(
            first_seen_at=now, mode=self.mode, is_demo=self.is_demo, ticker_map=self.ticker_map
        )
        caps: list[dict[str, Any]] = []
        records: list[CatalystRecord] = []
        notes: list[str] = []
        for src in self.sources:
            cap = src.capability()
            caps.append(cap.to_dict())
            if cap.status is not SourceState.ENABLED:
                continue
            try:
                batch = src.collect(ctx)
            except Exception as exc:  # a broken source must never take the pipeline down
                self.quarantine.add(
                    quarantine_record(
                        source=src.name,
                        url=f"source:{src.name}",
                        reason_code="SOURCE_FAILED",
                        error=exc,
                        now=now,
                    )
                )
                continue
            records.extend(batch.records)
            notes.extend(f"{src.name}:{n}" for n in batch.notes)
            for q in batch.quarantined:
                self.quarantine.add(q)
        result = dedupe(records)
        valid: list[CatalystRecord] = []
        payloads: list[dict[str, Any]] = []
        for rep in result.representatives:
            rec = rep
            if score_at is not None and rep.available_at <= ensure_utc(score_at):
                rec = apply_score(rep, decision_time=score_at)
            try:
                payloads.append(rec.to_contract())
            except ContractViolation as exc:
                self.quarantine.add(
                    quarantine_record(
                        source=rec.source.value,
                        url=rec.canonical_url or rec.id,
                        reason_code="CONTRACT_VIOLATION",
                        error=exc,
                        now=now,
                    )
                )
                continue
            valid.append(rec)
        return PipelineResult(
            payloads, valid, result.clusters, list(self.quarantine.records[q_start:]), caps, notes
        )


def _as_payload(item: CatalystRecord | Mapping[str, Any]) -> dict[str, Any]:
    return item.to_contract() if isinstance(item, CatalystRecord) else dict(item)


def _entity_cik(cat: Mapping[str, Any], ticker_map: TickerMap) -> str | None:
    if cat.get("cik"):
        return str(cat["cik"])
    sym = cat.get("symbol")
    if not sym:
        return None
    ref = cat.get("event_at") or cat.get("published_at") or cat["available_at"]
    return ticker_map.cik_for(sym, parse_utc(ref).astimezone(ET).date())


def point_in_time(
    catalysts: Iterable[CatalystRecord | Mapping[str, Any]],
    decision_time: datetime,
    symbol: str | None = None,
    *,
    ticker_map: TickerMap | None = None,
    include_market_wide: bool = False,
) -> list[dict[str, Any]]:
    """Catalysts usable at ``decision_time`` (``available_at <= decision_time``) for ``symbol``.

    With a ``ticker_map`` matching is by ENTITY (CIK as of the decision date), so a reused ticker never
    pulls in the previous company's catalysts, and a renamed company keeps its pre-rename history.
    Catalysts whose entity cannot be established are excluded in that mode.
    """
    decision_time = ensure_utc(decision_time)
    target_cik = None
    if symbol and ticker_map is not None:
        target_cik = ticker_map.cik_for(symbol, decision_time.astimezone(ET).date())
    out: list[dict[str, Any]] = []
    for item in catalysts:
        cat = _as_payload(item)
        if not is_available(cat, decision_time):
            continue
        if symbol is None:
            out.append(cat)
            continue
        if not cat.get("symbol") and not cat.get("cik"):
            if include_market_wide:
                out.append(cat)
            continue
        if ticker_map is None:
            if cat.get("symbol") == symbol:
                out.append(cat)
            continue
        if target_cik is not None and _entity_cik(cat, ticker_map) == target_cik:
            out.append(cat)
    out.sort(key=lambda c: (parse_utc(c["available_at"]), c["id"]))
    return out


__all__ = ["CatalystPipeline", "PipelineResult", "point_in_time"]
