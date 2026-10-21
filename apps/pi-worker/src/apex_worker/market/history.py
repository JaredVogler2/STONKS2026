"""Bounded, incremental, checksummed daily-bar history for CI training (GitHub-hosted runner).

GitHub runners are ephemeral and never see the Pi's local Parquet, so training fetches its own history from the
configured no-cost provider (Alpaca Basic) into a small cache directory that the workflow persists with the Actions
cache between runs:

    <cache>/manifest.json                       schema, feed, adjustment and per-symbol {file, sha256, rows, first, last}
    <cache>/bars/<SYMBOL>.<feed>.<adj>.csv.gz   deterministic gzip(CSV) (mtime=0, fixed column order, 6 dp prices)

* Bounded: at most `max_symbols` symbols, `years` of history and `max_requests` provider calls per run (remaining
  symbols keep their cached, possibly stale, bars and are listed as such).
* Incremental: a valid cached symbol only fetches from `overlap_sessions` before its last cached session. If the
  overlapping bars disagree (a split re-adjusted history, or the vendor corrected data) the symbol is re-fetched in full.
* Checksummed: every file's sha256 is verified on load; a mismatch discards the file and re-fetches it. The run records
  `history_hash` (sha256 over the canonical manifest of file hashes + window), and the pipeline separately records the
  `training_data_hash` of the OHLCV arrays it actually trained on.
* Feed: daily bars default to `sip` historical (Basic plan: free when older than 15 min; the adapter clamps `end`),
  matching the Pi's own daily refresh so training and live features share one volume definition. `iex` (single venue,
  a small fraction of consolidated volume) is available but would skew volume/liquidity features vs the live scanner.
Never redistribute cached bars: they stay in the private repository's Actions cache under the provider's terms.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from apex_worker.agent.canonical import canonical_json

HISTORY_SCHEMA = "apex-history-cache-1"
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
COLUMNS = ("open", "high", "low", "close", "volume")
PRICE_TOLERANCE = 1e-4  # relative; overlap disagreement beyond this => full re-fetch


@dataclass(frozen=True)
class HistorySpec:
    symbols: tuple[str, ...]
    years: int = 8
    feed: str = "sip"
    adjustment: str = "split"
    max_requests: int = 300
    overlap_sessions: int = 5
    max_symbols: int = 200

    def validate(self) -> None:
        if not self.symbols:
            raise ValueError("history spec has no symbols")
        if len(self.symbols) > self.max_symbols:
            raise ValueError(f"history spec exceeds max_symbols={self.max_symbols}")
        bad = [s for s in self.symbols if not SYMBOL_RE.match(s)]
        if bad:
            raise ValueError(f"invalid symbols: {bad[:5]}")
        if not 1 <= self.years <= 15:
            raise ValueError("years must be within 1..15")
        if self.feed not in ("sip", "iex"):
            raise ValueError("feed must be sip or iex")
        if self.adjustment not in ("split", "raw", "all"):
            raise ValueError("unsupported adjustment")


@dataclass
class HistoryResult:
    bars: dict[str, pd.DataFrame]
    start: date
    end: date
    history_hash: str
    requests: int
    full: list[str] = field(default_factory=list)
    incremental: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    refetched_after_mismatch: list[str] = field(default_factory=list)
    corrupted: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    budget_exhausted: bool = False

    def summary(self) -> dict:
        return {
            "schema": HISTORY_SCHEMA,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "history_hash": self.history_hash,
            "requests": self.requests,
            "symbols": len(self.bars),
            "full": len(self.full),
            "incremental": len(self.incremental),
            "reused": len(self.reused),
            "refetched_after_mismatch": sorted(self.refetched_after_mismatch),
            "corrupted": sorted(self.corrupted),
            "stale": sorted(self.stale),
            "missing": sorted(self.missing),
            "budget_exhausted": self.budget_exhausted,
        }


# ---- deterministic file format -----------------------------------------------------------------------
def encode_bars(df: pd.DataFrame) -> bytes:
    out = pd.DataFrame(
        {
            "session_date": pd.DatetimeIndex(df.index).strftime("%Y-%m-%d"),
            **{c: df[c].astype(float).round(6).to_numpy() for c in COLUMNS[:4]},
            "volume": df["volume"].astype(float).round().astype("int64").to_numpy(),
        }
    )
    buf = io.StringIO()
    out.to_csv(buf, index=False, float_format="%.6f", lineterminator="\n")
    return gzip.compress(buf.getvalue().encode("ascii"), compresslevel=9, mtime=0)


def decode_bars(data: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(gzip.decompress(data)), dtype={"session_date": str})
    if list(df.columns) != ["session_date", *COLUMNS]:
        raise ValueError("unexpected cache columns")
    df.index = pd.to_datetime(df.pop("session_date"), format="%Y-%m-%d")
    df.index.name = "session_date"
    return df.astype({c: float for c in COLUMNS[:4]} | {"volume": "int64"})


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Provider frame -> session-indexed OHLCV, rounded exactly like the cache so comparisons are like-for-like."""
    out = df[list(COLUMNS)].copy()
    out.index = pd.DatetimeIndex(pd.to_datetime(df.index)).normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for c in COLUMNS[:4]:
        out[c] = out[c].astype(float).round(6)
    out["volume"] = out["volume"].astype(float).round().astype("int64")
    bad = ~np.isfinite(out[list(COLUMNS[:4])].to_numpy()).all(axis=1) | (out["low"] > out["high"]) | (out["close"] <= 0)
    return out[~bad]


def _file_name(symbol: str, spec: HistorySpec) -> str:
    return f"{symbol.replace('.', '_')}.{spec.feed}.{spec.adjustment}.csv.gz"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def history_hash(manifest_symbols: dict[str, dict], spec: HistorySpec, start: date, end: date) -> str:
    body = {
        "schema": HISTORY_SCHEMA,
        "feed": spec.feed,
        "adjustment": spec.adjustment,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "files": {s: manifest_symbols[s]["sha256"] for s in sorted(manifest_symbols)},
    }
    return _sha(canonical_json(body).encode())


def _overlap_agrees(cached: pd.DataFrame, fresh: pd.DataFrame) -> bool:
    common = cached.index.intersection(fresh.index)
    if common.empty:
        return False  # cannot prove continuity: re-fetch in full
    a = cached.loc[common, list(COLUMNS[:4])].to_numpy(float)
    b = fresh.loc[common, list(COLUMNS[:4])].to_numpy(float)
    return bool(np.all(np.abs(a - b) <= PRICE_TOLERANCE * np.maximum(1.0, np.abs(a))))


class HistoryCache:
    def __init__(self, root: Path):
        self.root = root
        self.bars_dir = root / "bars"

    def _manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def load_manifest(self, spec: HistorySpec) -> dict[str, dict]:
        try:
            m = json.loads(self._manifest_path().read_text())
        except (OSError, ValueError):
            return {}
        if m.get("schema") != HISTORY_SCHEMA or m.get("feed") != spec.feed or m.get("adjustment") != spec.adjustment:
            return {}
        syms = m.get("symbols")
        return syms if isinstance(syms, dict) else {}

    def read(self, symbol: str, entry: dict, spec: HistorySpec) -> pd.DataFrame | None:
        """Verified read; None when absent or the checksum does not match (caller re-fetches)."""
        name = entry.get("file")
        if name != _file_name(symbol, spec):
            return None
        try:
            data = (self.bars_dir / name).read_bytes()
        except OSError:
            return None
        if _sha(data) != entry.get("sha256"):
            return None
        try:
            return decode_bars(data)
        except (ValueError, OSError, EOFError):
            return None

    def write(self, symbol: str, df: pd.DataFrame, spec: HistorySpec, fetched_at: str, complete_from: date) -> dict:
        self.bars_dir.mkdir(parents=True, exist_ok=True)
        data = encode_bars(df)
        name = _file_name(symbol, spec)
        tmp = self.bars_dir / (name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(self.bars_dir / name)
        return {
            "file": name,
            "sha256": _sha(data),
            "rows": len(df),
            "first": df.index.min().date().isoformat() if len(df) else None,
            "last": df.index.max().date().isoformat() if len(df) else None,
            "fetched_at": fetched_at,
            "complete_from": complete_from.isoformat(),  # provider was asked from here: nothing earlier exists
        }

    def save_manifest(self, symbols: dict[str, dict], spec: HistorySpec) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        body = {"schema": HISTORY_SCHEMA, "feed": spec.feed, "adjustment": spec.adjustment, "symbols": symbols}
        tmp = self._manifest_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n")
        tmp.replace(self._manifest_path())


async def sync_history(
    adapter,
    cache_dir: Path,
    spec: HistorySpec,
    *,
    end: date,
    now: datetime | None = None,
) -> HistoryResult:
    """Bring the cache up to `end` (latest completed session) for spec.symbols and return verified bars."""
    spec.validate()
    now = now or datetime.now(UTC)
    fetched_at = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    start = end - timedelta(days=round(365.25 * spec.years))
    cache = HistoryCache(cache_dir)
    manifest = cache.load_manifest(spec)
    res = HistoryResult({}, start, end, "", 0)
    have: dict[str, pd.DataFrame] = {}
    groups: dict[date, list[str]] = {}
    for sym in spec.symbols:
        entry = manifest.get(sym)
        df = cache.read(sym, entry, spec) if entry else None
        if entry and df is None:
            res.corrupted.append(sym)
        covers = (
            df is not None
            and len(df) > 0
            and (
                df.index.min().date() <= start + timedelta(days=10)  # history reaches back to the window start
                or str((entry or {}).get("complete_from") or "9999") <= start.isoformat()  # or nothing older exists
            )
        )
        if covers:
            have[sym] = df
            last = df.index.max().date()
            if last >= end:
                res.reused.append(sym)
                continue
            back = df.index[max(0, len(df) - spec.overlap_sessions)].date()
            groups.setdefault(back, []).append(sym)
        else:
            groups.setdefault(start, []).append(sym)

    def budget_left() -> bool:
        return (getattr(adapter, "requests", 0) - base_requests) < spec.max_requests

    base_requests = getattr(adapter, "requests", 0)
    refetch: list[str] = []
    # oldest windows first: full downloads are the most valuable; incremental groups are cheap
    for gstart in sorted(groups):
        syms = groups[gstart]
        if not budget_left():
            res.budget_exhausted = True
            _leave_stale(syms, have, res)
            continue
        fresh = await adapter.get_daily_bars(syms, gstart, end, adjustment=spec.adjustment, feed=spec.feed)
        for sym in syms:
            got = fresh.get(sym)
            new = _normalise(got) if got is not None and not got.empty else None
            if gstart == start or sym not in have:
                if new is None or new.empty:
                    res.missing.append(sym)
                    continue
                have[sym] = new
                res.full.append(sym)
                manifest[sym] = cache.write(sym, new, spec, fetched_at, start)
                continue
            if new is None or new.empty or new.index.max() <= have[sym].index.max():
                res.stale.append(sym)  # nothing newer (halted/delisted/late provider): keep the verified cached bars
                continue
            if not _overlap_agrees(have[sym], new):
                refetch.append(sym)
                continue
            merged = pd.concat([have[sym][have[sym].index < new.index.min()], new])
            have[sym] = merged[merged.index >= pd.Timestamp(start)]  # bounded window: the file never grows past it
            res.incremental.append(sym)
            done_from = max(start, date.fromisoformat(str(manifest[sym].get("complete_from") or start.isoformat())))
            manifest[sym] = cache.write(sym, have[sym], spec, fetched_at, done_from)
    if refetch:
        if budget_left():
            fresh = await adapter.get_daily_bars(refetch, start, end, adjustment=spec.adjustment, feed=spec.feed)
            for sym in refetch:
                got = fresh.get(sym)
                new = _normalise(got) if got is not None and not got.empty else None
                if new is None or new.empty:
                    res.stale.append(sym)
                    continue
                have[sym] = new
                res.refetched_after_mismatch.append(sym)
                manifest[sym] = cache.write(sym, new, spec, fetched_at, start)
        else:
            res.budget_exhausted = True
            _leave_stale(refetch, have, res)
    res.requests = getattr(adapter, "requests", 0) - base_requests
    # only symbols in this spec, trimmed to the bounded window; the hash covers exactly what training will see
    kept = {s: manifest[s] for s in spec.symbols if s in manifest and s in have}
    cache.save_manifest({**manifest, **kept}, spec)
    res.bars = {
        s: have[s][(have[s].index >= pd.Timestamp(start)) & (have[s].index <= pd.Timestamp(end))] for s in kept
    }
    res.history_hash = history_hash(kept, spec, start, end)
    return res


def _leave_stale(syms: list[str], have: dict[str, pd.DataFrame], res: HistoryResult) -> None:
    for s in syms:
        (res.stale if s in have else res.missing).append(s)
