"""Vintage-aware macro series (FRED/ALFRED) and scheduled macro-release catalysts.

ALFRED observations carry ``realtime_start``/``realtime_end`` (the vintage window during which a value
was the published one). :meth:`VintageSeries.value_as_of` returns the value *as known* at a decision
time - never a later revision. A vintage dated D counts as known from D's scheduled release time (ET)
when supplied, otherwise only from 00:00 ET of D+1 (conservative). If the data has no vintage history
covering the decision date (e.g. a plain FRED pull of today's revised values) the feature is
UNAVAILABLE (``value=None`` + reason) rather than silently using revised data.

FRED requires an API key; without one the client reports ``DISABLED``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

from apex_worker.catalysts.http import PoliteHttpClient, host_of, redact_url
from apex_worker.catalysts.models import (
    AvailabilityMode,
    CatalystRecord,
    CatalystType,
    Direction,
    LegalClassification,
    Source,
    SourceGrade,
    classify_actionable_age,
    et_next_day_start,
    make_catalyst_id,
    metadata_hash,
    resolve_available_at,
)
from apex_worker.timeutil import ET, ensure_utc, parse_utc

PARSER_VERSION = "macro/1.0.0"
FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
ALFRED_ALL_START = "1776-07-04"
ALFRED_ALL_END = "9999-12-31"
FRED_KEY_ENV = "APEX_FRED_API_KEY"
OFFICIAL_RELEASE_HOSTS = frozenset({"www.bls.gov", "www.bea.gov", "www.federalreserve.gov", "www.census.gov"})


class SourceDisabledError(RuntimeError):
    pass


class MacroParseError(ValueError):
    pass


@dataclass(frozen=True)
class VintageObservation:
    observation_date: date
    value: Decimal | None
    realtime_start: date
    realtime_end: date


@dataclass(frozen=True)
class AsOfValue:
    value: Decimal | None
    available: bool
    reason: str
    observation_date: date | None = None
    vintage_start: date | None = None
    known_since: datetime | None = None


def _d(value: str, what: str) -> date:
    try:
        return date.max if value == ALFRED_ALL_END else date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise MacroParseError(f"bad {what} {value!r}") from exc


class VintageSeries:
    def __init__(
        self,
        series_id: str,
        observations: Iterable[VintageObservation],
        *,
        coverage_start: date,
        release_time_et: time | None = None,
    ):
        self.series_id = series_id
        self.observations = sorted(observations, key=lambda o: (o.observation_date, o.realtime_start))
        self.coverage_start = coverage_start
        self.release_time_et = release_time_et

    @classmethod
    def from_alfred_json(
        cls, data: Mapping[str, Any] | bytes | str, series_id: str, *, release_time_et: time | None = None
    ) -> VintageSeries:
        raw = json.loads(data) if isinstance(data, bytes | str) else data
        if "observations" not in raw:
            raise MacroParseError("not a FRED/ALFRED observations document")
        obs: list[VintageObservation] = []
        for row in raw["observations"]:
            value_text = str(row.get("value", "."))
            try:
                value = None if value_text.strip() in {".", ""} else Decimal(value_text)
            except InvalidOperation as exc:
                raise MacroParseError(f"bad value {value_text!r}") from exc
            obs.append(
                VintageObservation(
                    observation_date=_d(row["date"], "date"),
                    value=value,
                    realtime_start=_d(row["realtime_start"], "realtime_start"),
                    realtime_end=_d(row["realtime_end"], "realtime_end"),
                )
            )
        top_start = raw.get("realtime_start")
        if top_start:
            coverage = date.min if top_start == ALFRED_ALL_START else _d(top_start, "realtime_start")
        else:
            coverage = min((o.realtime_start for o in obs), default=date.max)
        return cls(series_id, obs, coverage_start=coverage, release_time_et=release_time_et)

    def vintage_known_at(self, realtime_start: date) -> datetime:
        if self.release_time_et is not None:
            return datetime.combine(realtime_start, self.release_time_et, ET).astimezone(UTC)
        return et_next_day_start(realtime_start)

    def value_as_of(self, observation_date: date, decision_time: datetime) -> AsOfValue:
        decision_time = ensure_utc(decision_time)
        if decision_time.astimezone(ET).date() < self.coverage_start or self.coverage_start == date.max:
            return AsOfValue(None, False, "VINTAGE_DATA_ABSENT", observation_date)
        rows = [o for o in self.observations if o.observation_date == observation_date]
        if not rows:
            return AsOfValue(None, False, "OBSERVATION_NOT_IN_SERIES", observation_date)
        known = [o for o in rows if self.vintage_known_at(o.realtime_start) <= decision_time]
        if not known:
            return AsOfValue(None, False, "NOT_YET_RELEASED", observation_date)
        best = max(known, key=lambda o: o.realtime_start)
        since = self.vintage_known_at(best.realtime_start)
        if best.value is None:
            return AsOfValue(
                None, False, "VALUE_MISSING_IN_VINTAGE", observation_date, best.realtime_start, since
            )
        reason = (
            "FIRST_RELEASE"
            if best.realtime_start == min(o.realtime_start for o in rows)
            else "REVISED_VINTAGE"
        )
        return AsOfValue(best.value, True, reason, observation_date, best.realtime_start, since)

    def latest_as_of(self, decision_time: datetime) -> AsOfValue:
        """Most recent observation period whose first release was known at ``decision_time``."""
        decision_time = ensure_utc(decision_time)
        if decision_time.astimezone(ET).date() < self.coverage_start or self.coverage_start == date.max:
            return AsOfValue(None, False, "VINTAGE_DATA_ABSENT")
        dates = sorted(
            {
                o.observation_date
                for o in self.observations
                if self.vintage_known_at(o.realtime_start) <= decision_time
            },
            reverse=True,
        )
        if not dates:
            return AsOfValue(None, False, "NOT_YET_RELEASED")
        return self.value_as_of(dates[0], decision_time)


# ---------------------------------------------------------------------------------------------
# FRED client (DISABLED without key; tested with httpx.MockTransport only)
# ---------------------------------------------------------------------------------------------
class FredClient:
    def __init__(self, api_key: str | None, http: PoliteHttpClient | None = None):
        self._api_key = (api_key or "").strip() or None
        self.http = http

    @classmethod
    def from_env(cls, http: PoliteHttpClient | None = None) -> FredClient:
        return cls(os.environ.get(FRED_KEY_ENV), http)

    def capability(self) -> dict[str, Any]:
        if self._api_key is None:
            return {
                "source": "fred",
                "status": "DISABLED",
                "automatic": False,
                "reason": (
                    f"FRED/ALFRED requires a free API key; none configured ({FRED_KEY_ENV}). "
                    "Vintage-aware macro features are unavailable."
                ),
            }
        if self.http is None:
            return {
                "source": "fred",
                "status": "DISABLED",
                "automatic": False,
                "reason": "no HTTP client configured",
            }
        return {
            "source": "fred",
            "status": "ENABLED",
            "automatic": True,
            "reason": "API key configured (value hidden)",
        }

    def observations_url(self, series_id: str) -> str:
        if self._api_key is None:
            raise SourceDisabledError(self.capability()["reason"])
        query = {
            "series_id": series_id,
            "realtime_start": ALFRED_ALL_START,
            "realtime_end": ALFRED_ALL_END,
            "file_type": "json",
            "api_key": self._api_key,
        }
        return f"{FRED_OBSERVATIONS_URL}?{urlencode(query)}"

    def fetch_vintages(self, series_id: str, *, release_time_et: time | None = None) -> VintageSeries:
        if self._api_key is None or self.http is None:
            raise SourceDisabledError(self.capability()["reason"])
        result = self.http.get(self.observations_url(series_id), accept="application/json")
        return VintageSeries.from_alfred_json(result.content, series_id, release_time_et=release_time_et)

    def __repr__(self) -> str:
        return f"FredClient(api_key={'<set>' if self._api_key else None})"


def safe_log_url(url: str) -> str:
    return redact_url(url)


# ---------------------------------------------------------------------------------------------
# release calendar -> macro_release catalysts
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class MacroReleaseEntry:
    release_key: str
    name: str
    scheduled_date: date
    scheduled_time_et: time | None
    source_url: str
    series_ids: tuple[str, ...]
    known_since: datetime | None
    confirmed: bool

    @property
    def scheduled_at(self) -> datetime:
        """Scheduled instant; date-only entries use 00:00 ET (time unknown, flagged by reason code)."""
        t = self.scheduled_time_et or time(0, 0)
        return datetime.combine(self.scheduled_date, t, ET).astimezone(UTC)


def load_release_calendar(data: Mapping[str, Any] | bytes | str) -> list[MacroReleaseEntry]:
    raw = json.loads(data) if isinstance(data, bytes | str) else data
    out = []
    for r in raw["releases"]:
        t = r.get("scheduled_time_et")
        out.append(
            MacroReleaseEntry(
                release_key=r["release_key"],
                name=r["name"],
                scheduled_date=date.fromisoformat(r["scheduled_date"]),
                scheduled_time_et=time.fromisoformat(t) if t else None,
                source_url=r["source_url"],
                series_ids=tuple(r.get("series_ids", ())),
                known_since=parse_utc(r["known_since"]) if r.get("known_since") else None,
                confirmed=bool(r.get("confirmed", False)),
            )
        )
    return out


def release_to_catalyst(
    entry: MacroReleaseEntry,
    *,
    first_seen_at: datetime,
    mode: AvailabilityMode | str = AvailabilityMode.BACKFILL,
    is_demo: bool = False,
) -> CatalystRecord:
    """A scheduled macro release. The *schedule* is the catalyst; its value arrives with the release."""
    if not entry.source_url.startswith("https://"):
        raise MacroParseError("release calendar source_url must be https")
    published = entry.known_since
    available = resolve_available_at(
        mode, first_seen_at=first_seen_at, published_at=published, lag=timedelta(0)
    )
    reasons = [
        "SCHEDULED_MACRO_RELEASE",
        "CONFIRMED_SCHEDULE" if entry.confirmed else "EXPECTED_SCHEDULE_UNCONFIRMED",
    ]
    if entry.scheduled_time_et is None:
        reasons.append("TIME_UNKNOWN_DATE_ONLY")
    if published is None:
        reasons.append("SCHEDULE_KNOWN_SINCE_UNRECORDED")
    official = host_of(entry.source_url) in OFFICIAL_RELEASE_HOSTS
    when = entry.scheduled_date.isoformat() + (
        f" {entry.scheduled_time_et.strftime('%H:%M')} ET" if entry.scheduled_time_et else " (time TBD)"
    )
    return CatalystRecord(
        id=make_catalyst_id(
            Source.MANUAL,
            f"{entry.release_key}|{entry.scheduled_date.isoformat()}",
            CatalystType.MACRO_RELEASE,
        ),
        catalyst_type=CatalystType.MACRO_RELEASE,
        source=Source.MANUAL,
        headline=f"Scheduled macro release: {entry.name} on {when}",
        source_grade=SourceGrade.B_OFFICIAL_RELEASE if official else SourceGrade.D_OTHER,
        direction=Direction.UNKNOWN,
        entity_confidence=1.0,
        extraction_confidence=0.9 if entry.confirmed else 0.6,
        first_seen_at=first_seen_at,
        available_at=available,
        source_hash=metadata_hash(
            {
                "key": entry.release_key,
                "date": entry.scheduled_date,
                "time": str(entry.scheduled_time_et),
                "url": entry.source_url,
            }
        ),
        parser_version=PARSER_VERSION,
        legal_classification=LegalClassification.PUBLIC_DOMAIN_GOVERNMENT
        if official
        else LegalClassification.MANUAL_ENTRY,
        is_demo=is_demo,
        event_at=entry.scheduled_at,
        published_at=published,
        canonical_url=entry.source_url,
        publisher="Official release calendar (manual)" if official else "Release calendar (manual)",
        actionable_age=classify_actionable_age(
            available_at=available, published_at=published, event_at=entry.scheduled_at
        ),
        reason_codes=tuple(reasons),
        parsed={
            "release_key": entry.release_key,
            "series_ids": list(entry.series_ids),
            "confirmed": entry.confirmed,
            "scheduled_time_known": entry.scheduled_time_et is not None,
        },
    )


__all__ = [
    "FRED_KEY_ENV",
    "PARSER_VERSION",
    "AsOfValue",
    "FredClient",
    "MacroParseError",
    "MacroReleaseEntry",
    "SourceDisabledError",
    "VintageObservation",
    "VintageSeries",
    "load_release_calendar",
    "release_to_catalyst",
    "safe_log_url",
]
