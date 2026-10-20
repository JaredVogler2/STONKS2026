"""Import entries from the raspberry-pi/bot starter (data/positions.sqlite3) into the APEX position ledger.

- Dry run (default) reads the starter file read-only and reports what WOULD happen; it writes nothing.
- --apply first makes a consistent timestamped backup of the starter file (mode 0600), then imports each row as
  an OPEN event through LocalPositionService with source='starter_import' and idempotency key 'starter:<id>'
  (re-running is a no-op for rows already imported). fill_at = the starter's created_at.
- Starter signal ids are linked only if they are real APEX ids (^sig_[0-9a-f]{24}$) of a published signal;
  test refs such as DEMO-001 are kept as a private note.
- The starter file is never modified, so rolling back = stop `apex bot`, start the starter service again.
- Output never contains prices or share counts (only ids, statuses and reason codes).
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from apex_worker.config import WorkerConfig
from apex_worker.contracts import ContractViolation
from apex_worker.portfolio.ledger import LedgerError, fmt, parse_fill
from apex_worker.services.positions import LocalPositionService, ServiceError
from apex_worker.storage.local import LocalStore
from apex_worker.timeutil import iso_utc

STARTER_COLUMNS = {"id", "discord_user_id", "ticker", "price", "shares", "signal_id", "created_at"}
SIGNAL_RE = re.compile(r"^sig_[0-9a-f]{24}$")
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
SNOWFLAKE_RE = re.compile(r"^[1-9][0-9]{14,20}$")
SOURCE = "starter_import"


def _read_starter(path: Path) -> tuple[list[sqlite3.Row] | None, str | None]:
    uri = f"file:{path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        return None, f"cannot open starter database: {type(exc).__name__}"
    try:
        conn.row_factory = sqlite3.Row
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(positions)")}
        if not cols >= STARTER_COLUMNS:
            return None, ("unsupported starter schema: expected table positions(" + ", ".join(sorted(STARTER_COLUMNS))
                          + ")")
        return conn.execute("SELECT id, discord_user_id, ticker, price, shares, signal_id, created_at FROM positions "
                            "ORDER BY id").fetchall(), None
    except sqlite3.Error as exc:
        return None, f"cannot read starter database: {type(exc).__name__}"
    finally:
        conn.close()


def _parse_created(raw: str) -> datetime:
    s = (raw or "").strip().replace(" ", "T")
    dt = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)  # starter stores strftime(...,'now') which is UTC
    return dt.astimezone(UTC)


def _build_event(row: sqlite3.Row, now: datetime) -> tuple[str, dict, str | None]:
    """Returns (discord_user_id, PositionEvent, apex_signal_id_or_None); raises ValueError(code) if unusable."""
    duid = str(row["discord_user_id"]).strip()
    if not SNOWFLAKE_RE.fullmatch(duid):
        raise ValueError("INVALID_DISCORD_USER")
    ticker = str(row["ticker"] or "").strip().upper()
    if not SYMBOL_RE.fullmatch(ticker):
        raise ValueError("INVALID_SYMBOL")
    try:
        created = _parse_created(str(row["created_at"]))
    except ValueError as exc:
        raise ValueError("INVALID_TIME") from exc
    try:
        fill = parse_fill(Decimal(str(row["price"]).strip()), Decimal(str(row["shares"]).strip()), Decimal(0), created, now)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(getattr(exc, "code", "INVALID_NUMBER")) from exc
    raw_sig = str(row["signal_id"] or "").strip()
    note = f"Imported from starter bot entry #{row['id']}"
    sig = raw_sig if SIGNAL_RE.fullmatch(raw_sig) else None
    if raw_sig and sig is None:
        note += f"; starter signal ref: {raw_sig[:80]}"
    event = {"client_idempotency_key": f"starter:{row['id']}", "event_type": "OPEN", "symbol": ticker,
             "source": SOURCE, "notes": note[:280],
             "fill": {"price": fmt(fill.price), "quantity": fmt(fill.quantity), "fees": "0", "fill_at": iso_utc(fill.fill_at)}}
    if sig:
        event["signal_id"] = sig
    return duid, event, sig


def backup_starter(path: Path, now: datetime) -> Path:
    dest = path.with_name(f"{path.name}.backup-{now.strftime('%Y%m%dT%H%M%SZ')}")
    n = 1
    while dest.exists():
        dest = path.with_name(f"{path.name}.backup-{now.strftime('%Y%m%dT%H%M%SZ')}-{n}")
        n += 1
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)  # consistent snapshot even if the starter bot is still running
        finally:
            dst.close()
    finally:
        src.close()
    dest.chmod(0o600)
    return dest


def _already_imported(store: LocalStore, duid: str, key: str) -> bool:
    uid = store.member_id(duid)
    if not uid:
        return False
    with store.tx() as c:
        return store.find_idempotent(c, uid, key) is not None


def migrate_starter(starter_db: Path, dry_run: bool = True, *, store: LocalStore | None = None,
                    now: datetime | None = None, is_demo: bool | None = None) -> dict:
    starter_db = Path(starter_db).expanduser()
    now = now or datetime.now(UTC)
    report: dict = {"starter_db": str(starter_db), "dry_run": dry_run, "status": "ok", "backup": None,
                    "counts": {"rows": 0, "imported": 0, "would_import": 0, "already_imported": 0, "skipped": 0,
                               "signal_linked": 0, "signal_ref_as_note": 0}, "rows": []}
    if not starter_db.exists():
        report["status"] = "not_found"
        report["detail"] = "starter database not found (nothing to migrate)"
        return report
    rows, err = _read_starter(starter_db)
    if rows is None:
        report["status"] = "unsupported_schema" if err and "schema" in err else "error"
        report["detail"] = err
        return report
    own_store = store is None
    cfg = None
    if own_store:
        from apex_worker.discord_bot.settings import BotSettings

        cfg = WorkerConfig.load()
        store = LocalStore(BotSettings.load(cfg).local_db)
    assert store is not None
    if is_demo is None:
        is_demo = (cfg.release_mode if cfg else "DEMO") == "DEMO"
    report["target_db"] = str(store.path)
    report["target_schema_version"] = store.schema_version()
    service = LocalPositionService(store, is_demo=is_demo)
    try:
        if not dry_run:
            report["backup"] = str(backup_starter(starter_db, now))
        counts = report["counts"]
        for row in rows:
            counts["rows"] += 1
            item: dict = {"starter_id": row["id"]}
            try:
                duid, event, sig = _build_event(row, now)
            except ValueError as exc:
                item.update(status="skipped", reason=str(exc))
                counts["skipped"] += 1
                report["rows"].append(item)
                continue
            if sig is not None and store.latest_signal(sig) is None:
                event.pop("signal_id")
                event["notes"] = (event["notes"] + f"; unknown APEX signal {sig}")[:280]
                sig = None
                item["note"] = "UNKNOWN_SIGNAL_KEPT_AS_NOTE"
            if _already_imported(store, duid, event["client_idempotency_key"]):
                item["status"] = "already_imported"
                counts["already_imported"] += 1
                report["rows"].append(item)
                continue
            if store.member_id(duid) is None and _revoked(store, duid):
                item.update(status="skipped", reason="MEMBERSHIP_REVOKED")
                counts["skipped"] += 1
                report["rows"].append(item)
                continue
            if dry_run:
                item["status"] = "would_import"
                counts["would_import"] += 1
            else:
                try:
                    res = service.record(duid, event, now)
                except (ServiceError, LedgerError) as exc:
                    item.update(status="skipped", reason=exc.code)
                    counts["skipped"] += 1
                    report["rows"].append(item)
                    continue
                except ContractViolation:
                    item.update(status="skipped", reason="INVALID_EVENT")
                    counts["skipped"] += 1
                    report["rows"].append(item)
                    continue
                if res.status == "duplicate":
                    item["status"] = "already_imported"
                    counts["already_imported"] += 1
                    report["rows"].append(item)
                    continue
                item.update(status="imported", position_ref=(res.position or {}).get("position_id", "")[:8])
                counts["imported"] += 1
            if sig:
                counts["signal_linked"] += 1
            elif "starter signal ref" in event["notes"] or item.get("note"):
                counts["signal_ref_as_note"] += 1
            report["rows"].append(item)
    finally:
        if own_store:
            store.close()
    if dry_run:
        report["next_step"] = "Re-run with --apply to back up the starter file and import the rows above."
    else:
        report["next_step"] = ("Stop the starter: sudo systemctl disable --now apex-pi-bot ; then start `apex bot`. "
                               "Members' old /apex_remove numbers keep working.")
    return report


def _revoked(store: LocalStore, duid: str) -> bool:
    rows = store.query("SELECT revoked_at FROM app_users WHERE discord_user_id=?", (duid,))
    return bool(rows and rows[0]["revoked_at"])
