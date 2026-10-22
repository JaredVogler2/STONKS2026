"""Forward-shadow records: canonical JSON, sha256 digests and the hash chain.

A record is a JSON object whose `digest` is the sha256 of the canonical encoding of every other field. Each record
carries the digest of its predecessor (`previous_digest`) and its position (`seq`), so inserting, deleting, editing or
reordering any record breaks `verify_chain`. One series = one chain; the two lanes never share a chain.

Record types:
* SERIES_GENESIS          seq 0: the frozen series configuration (protocol, selection lock, versions, fingerprint).
* DECISION                one per observed session: everything decided after the close of t, before any outcome.
* OBSERVATION_ATTEMPT_FAILED  at most one per session: a run inside the window could not decide (source pending,
                          invalid source, missing credentials); a DECISION may still follow before the deadline.
* MISSED_OBSERVATION      the session's window closed without a DECISION. Never followed by a DECISION for it.
* OUTCOME                 one per DECISION, appended only after its label window matured; it names the decision's
                          record id and digest and never edits it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable
from datetime import date, datetime

import numpy as np

SCHEMA = "apex-shadow-record-1"
GENESIS_PREVIOUS = "0" * 64
GENESIS = "SERIES_GENESIS"
DECISION = "DECISION"
ATTEMPT_FAILED = "OBSERVATION_ATTEMPT_FAILED"
MISSED = "MISSED_OBSERVATION"
OUTCOME = "OUTCOME"
RECORD_TYPES = (GENESIS, DECISION, ATTEMPT_FAILED, MISSED, OUTCOME)
TERMINAL = (DECISION, MISSED)  # exactly one per session, in session order
LANES = ("FROZEN_CONFIRMATORY", "EXPLORATORY", "FIXTURE_DEMO")
CLOCK_TOLERANCE_SECONDS = 300  # observed_at may not run backwards by more than this between records


class ChainViolation(Exception):
    """The ledger does not verify. Recording stops (fail closed) until an owner investigates."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems[:5]) + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""))
        self.problems = problems


def clean(x):
    """JSON-safe plain values: numpy scalars to Python, non-finite floats to None, tuples to lists, dates to ISO."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        v = float(x)
        return v if math.isfinite(v) else None
    if isinstance(x, datetime):
        return x.isoformat()
    if isinstance(x, date):
        return x.isoformat()
    return x


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_of(rec: dict) -> str:
    return sha256_hex(canonical({k: v for k, v in rec.items() if k != "digest"}))


def record_key(series_id: str, session: str | None, record_type: str) -> str:
    """The uniqueness key: one record per (series, session, type)."""
    return f"{series_id}|{session or '-'}|{record_type}"


def make_record(*, series_id: str, lane: str, seq: int, record_type: str, session: str | None,
                previous_digest: str, observed_at: str, producer: dict, body: dict) -> dict:
    if record_type not in RECORD_TYPES:
        raise ValueError(f"unknown record type {record_type}")
    if lane not in LANES:
        raise ValueError(f"unknown lane {lane}")
    rec = {
        "schema": SCHEMA, "series_id": series_id, "lane": lane, "seq": int(seq), "record_type": record_type,
        "session": session, "key": record_key(series_id, session, record_type),
        "record_id": f"{series_id}:{int(seq):06d}", "previous_digest": previous_digest, "observed_at": observed_at,
        "producer": clean(producer), "body": clean(body),
    }
    canonical(rec)  # raises on NaN/inf or unserialisable content before anything is persisted
    rec["digest"] = digest_of(rec)
    return rec


RAW_BAR_KEYS = frozenset({"open", "high", "low", "close", "volume", "vw", "vwap", "bars", "ohlcv"})
SECRET_PATTERNS = (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
                   re.compile(r"\b(?:AK|PK)[A-Z0-9]{16,}\b"), re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"))
SECRET_ENV = ("ALPACA_KEY_ID", "ALPACA_SECRET_KEY", "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "GITHUB_TOKEN",
              "APEX_RELEASE_HMAC_SECRET", "DISCORD_TOKEN")


def publication_problems(rec: dict, env: dict | None = None) -> list[str]:
    """Why a record may NOT be published to GitHub: raw vendor bar fields (prices and volumes of bars are licensed data;
    plans, returns and hashes are derived), a configured secret value, or anything shaped like a credential."""
    import os

    env = os.environ if env is None else env
    secrets = {v for k in SECRET_ENV if len(v := (env.get(k) or "").strip()) >= 8}
    out: list[str] = []

    def walk(x, path: str) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if str(k) in RAW_BAR_KEYS:  # case-sensitive: upper-case keys are tickers (LOW is Lowe's)
                    out.append(f"{path}.{k}: raw bar field")
                walk(v, f"{path}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")
        elif isinstance(x, str):
            if any(sv in x for sv in secrets):
                out.append(f"{path}: contains a configured secret value")
            elif any(pt.search(x) for pt in SECRET_PATTERNS):
                out.append(f"{path}: looks like a credential")

    walk(rec, "record")
    return out


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def verify_chain(records: list[dict], *, series_id: str | None = None, lane: str | None = None,
                 expected_sessions: Iterable[str] | None = None) -> list[str]:
    """Every problem found (empty = the chain verifies). `expected_sessions`: sessions that must each have exactly one
    terminal record (DECISION or MISSED_OBSERVATION); a gap means a record was dropped or never written."""
    problems: list[str] = []
    if not records:
        return problems
    sid = series_id or records[0].get("series_id")
    ln = lane or records[0].get("lane")
    prev = GENESIS_PREVIOUS
    keys: set[str] = set()
    terminal: dict[str, str] = {}
    last_terminal: str | None = None
    decisions: dict[str, dict] = {}
    outcomes: set[str] = set()
    last_obs: datetime | None = None
    for i, r in enumerate(records):
        where = f"record {i} ({r.get('record_id')})"
        if r.get("schema") != SCHEMA:
            problems.append(f"{where}: schema {r.get('schema')!r}")
        if r.get("digest") != digest_of(r):
            problems.append(f"{where}: digest does not match its content (edited)")
        if r.get("series_id") != sid or r.get("lane") != ln:
            problems.append(f"{where}: belongs to series {r.get('series_id')}/{r.get('lane')}, not {sid}/{ln}")
        if r.get("seq") != i:
            problems.append(f"{where}: seq {r.get('seq')} at position {i} (inserted, deleted or reordered)")
        if r.get("previous_digest") != prev:
            problems.append(f"{where}: previous_digest does not match the preceding record")
        prev = r.get("digest")
        rt, sess = r.get("record_type"), r.get("session")
        if (i == 0) != (rt == GENESIS):
            problems.append(f"{where}: {'first record must be' if i == 0 else 'only the first record may be'} {GENESIS}")
        key = record_key(str(r.get("series_id")), sess, str(rt))
        if r.get("key") != key:
            problems.append(f"{where}: key {r.get('key')!r} != {key!r}")
        if key in keys:
            problems.append(f"{where}: duplicate {rt} for session {sess}")
        keys.add(key)
        try:
            obs = _ts(str(r.get("observed_at")))
            if last_obs is not None and (last_obs - obs).total_seconds() > CLOCK_TOLERANCE_SECONDS:
                problems.append(f"{where}: observed_at runs backwards")
            last_obs = obs if last_obs is None else max(last_obs, obs)
        except ValueError:
            problems.append(f"{where}: observed_at {r.get('observed_at')!r} is not an ISO instant")
        if rt in TERMINAL:
            if sess in terminal:
                problems.append(f"{where}: session {sess} already has {terminal[sess]}")
            elif last_terminal is not None and str(sess) <= last_terminal:
                problems.append(f"{where}: terminal record for {sess} after {last_terminal} (out of session order)")
            terminal[str(sess)] = str(rt)
            last_terminal = max(last_terminal or "", str(sess))
            if rt == DECISION:
                decisions[str(sess)] = r
        elif rt == ATTEMPT_FAILED and sess in terminal:
            problems.append(f"{where}: attempt record after the session's {terminal[sess]}")
        elif rt == OUTCOME:
            ref = (r.get("body") or {}).get("decision") or {}
            d = decisions.get(str(sess))
            if d is None:
                problems.append(f"{where}: OUTCOME for {sess} without an earlier DECISION")
            elif ref.get("record_id") != d.get("record_id") or ref.get("digest") != d.get("digest"):
                problems.append(f"{where}: OUTCOME does not name its DECISION's record id and digest")
            if str(sess) in outcomes:
                problems.append(f"{where}: second OUTCOME for {sess}")
            outcomes.add(str(sess))
    if expected_sessions is not None:
        exp = [s for s in expected_sessions if last_terminal is not None and s <= last_terminal]
        gaps = [s for s in exp if s not in terminal]
        if gaps:
            problems.append(f"sessions without a DECISION or MISSED_OBSERVATION record: {gaps[:10]}")
    return problems


class Ledger:
    """One series' records in memory, with the indexes the recorder needs. `append` builds the next record; the caller
    persists it (store) and only then calls `commit`."""

    def __init__(self, series_id: str, lane: str, records: list[dict] | None = None):
        self.series_id, self.lane = series_id, lane
        self.records: list[dict] = []
        for r in records or []:
            self.commit(r)

    @property
    def head(self) -> str:
        return self.records[-1]["digest"] if self.records else GENESIS_PREVIOUS

    @property
    def genesis(self) -> dict | None:
        return self.records[0] if self.records and self.records[0]["record_type"] == GENESIS else None

    def has(self, session: str | None, record_type: str) -> bool:
        key = record_key(self.series_id, session, record_type)
        return any(r["key"] == key for r in self.records)

    def terminal(self, session: str) -> dict | None:
        return next((r for r in self.records if r["session"] == session and r["record_type"] in TERMINAL), None)

    def of_type(self, record_type: str) -> list[dict]:
        return [r for r in self.records if r["record_type"] == record_type]

    def build(self, record_type: str, session: str | None, body: dict, observed_at: str, producer: dict) -> dict:
        key = record_key(self.series_id, session, record_type)
        if any(r["key"] == key for r in self.records):
            raise ValueError(f"duplicate record {key}")
        if record_type in TERMINAL and session is not None and self.terminal(session) is not None:
            raise ValueError(f"session {session} already has a terminal record")
        return make_record(series_id=self.series_id, lane=self.lane, seq=len(self.records), record_type=record_type,
                           session=session, previous_digest=self.head, observed_at=observed_at, producer=producer,
                           body=body)

    def commit(self, rec: dict) -> None:
        if rec.get("seq") != len(self.records) or rec.get("previous_digest") != self.head:
            raise ChainViolation([f"record {rec.get('record_id')} does not extend the head"])
        if rec.get("series_id") != self.series_id or rec.get("lane") != self.lane:
            raise ChainViolation([f"record {rec.get('record_id')} belongs to another series or lane"])
        self.records.append(rec)
