"""Starter-bot migration (raspberry-pi/bot data/positions.sqlite3 -> APEX ledger): dry run, apply, idempotency,
backup, and starter-compatible /apex_remove afterwards."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from apex_worker.discord_bot.backend import LocalMemberBackend, LocalPositionBackend
from apex_worker.discord_bot.samples import sample_signal
from apex_worker.discord_bot.service import Actor, BotService
from apex_worker.operations.starter_migration import migrate_starter
from apex_worker.storage.local import LocalStore
from apex_worker.timeutil import iso_utc

# Exact DDL of raspberry-pi/bot/position_store.py
STARTER_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    price TEXT NOT NULL,
    shares TEXT NOT NULL,
    signal_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_positions_owner ON positions(discord_user_id);
"""
NOW = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
A, B = "100000000000000001", "100000000000000002"
GUILD = "900000000000000001"


def make_starter(path, rows):
    with sqlite3.connect(path) as c:
        c.executescript(STARTER_SCHEMA)
        for r in rows:
            c.execute("INSERT INTO positions (discord_user_id,ticker,price,shares,signal_id,created_at) "
                      "VALUES (?,?,?,?,?,?)", r)


@pytest.fixture
def env(tmp_path):
    store = LocalStore(tmp_path / "apex.sqlite3")
    sig = sample_signal(NOW - timedelta(days=2), symbol="NVDA", seed="mig")
    store.save_signal_revision(sig)
    starter = tmp_path / "bot" / "data" / "positions.sqlite3"
    starter.parent.mkdir(parents=True)
    make_starter(starter, [
        (A, "NVDA", "181.25", "20", "DEMO-001", "2026-10-18T14:30:00.000Z"),       # 1 starter test ref -> note
        (A, "NVDA", "101.5", "0.5", sig["id"], iso_utc(NOW - timedelta(hours=40))),  # 2 real APEX signal -> linked
        (B, "AMD", "150", "10", "", "2026-10-19T15:00:00.000Z"),                     # 3 manual
        (B, "AMD", "150", "10", "sig_" + "e" * 24, "2026-10-19T15:00:00.000Z"),      # 4 unknown sig -> note
        (B, "bad ticker", "1", "1", "", "2026-10-19T15:00:00.000Z"),                 # 5 invalid ticker
        (A, "MSFT", "400.123456", "1", "", "2026-10-19T15:00:00.000Z"),              # 6 too precise
        (A, "TSLA", "20000000", "1", "", "2026-10-19T15:00:00.000Z"),                # 7 too large (starter allowed 10M)
    ])
    yield {"store": store, "starter": starter, "sig": sig, "tmp": tmp_path}
    store.close()


def test_dry_run_reports_and_writes_nothing(env):
    before = env["starter"].read_bytes()
    rep = migrate_starter(env["starter"], dry_run=True, store=env["store"], now=NOW)
    assert rep["status"] == "ok" and rep["dry_run"] and rep["backup"] is None
    c = rep["counts"]
    assert (c["rows"], c["would_import"], c["skipped"], c["imported"]) == (7, 4, 3, 0)
    assert {r["starter_id"]: r.get("reason") for r in rep["rows"] if r["status"] == "skipped"} == {
        5: "INVALID_SYMBOL", 6: "TOO_PRECISE", 7: "TOO_LARGE"}
    assert env["store"].member_id(A) is None and env["store"].query("SELECT COUNT(*) FROM position_events")[0][0] == 0
    assert env["starter"].read_bytes() == before
    assert not list(env["starter"].parent.glob("*.backup-*"))
    blob = json.dumps(rep)
    assert "181.25" not in blob and "400.123456" not in blob  # never prints fills


def test_apply_backs_up_imports_and_is_idempotent(env):
    store = env["store"]
    rep = migrate_starter(env["starter"], dry_run=False, store=store, now=NOW)
    assert rep["counts"]["imported"] == 4 and rep["counts"]["skipped"] == 3
    assert rep["counts"]["signal_linked"] == 1 and rep["counts"]["signal_ref_as_note"] == 2
    backup = rep["backup"]
    assert backup and (oct(__import__("os").stat(backup).st_mode & 0o777) == "0o600")
    with sqlite3.connect(backup) as c:
        assert c.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 7
    positions = LocalPositionBackend(store)
    a = {p["lots"][0]["fill_price"]: p for p in positions.positions(A)}
    assert set(a) == {"181.25", "101.5"}
    assert a["181.25"]["signal_id"] is None and a["101.5"]["signal_id"] == env["sig"]["id"]
    assert a["181.25"]["lots"][0]["fill_at"] == "2026-10-18T14:30:00Z"
    ev = positions.events(A, a["181.25"]["position_id"])[0]
    assert ev["source"] == "starter_import" and "DEMO-001" in ev["notes"] and "#1" in ev["notes"]
    keys = {r[0] for r in store.query("SELECT client_idempotency_key FROM position_events")}
    assert keys == {"starter:1", "starter:2", "starter:3", "starter:4"}
    b = positions.positions(B)
    assert len(b) == 2 and all(p["signal_id"] is None for p in b)
    # re-run: nothing new
    again = migrate_starter(env["starter"], dry_run=False, store=store, now=NOW + timedelta(minutes=5))
    assert again["counts"]["imported"] == 0 and again["counts"]["already_imported"] == 4
    assert store.query("SELECT COUNT(*) FROM position_events")[0][0] == 4
    dry = migrate_starter(env["starter"], dry_run=True, store=store, now=NOW)
    assert dry["counts"]["already_imported"] == 4 and dry["counts"]["would_import"] == 0


def test_starter_remove_numbers_keep_working_after_migration(env):
    store = env["store"]
    migrate_starter(env["starter"], dry_run=False, store=store, now=NOW)
    svc = BotService(positions=LocalPositionBackend(store), members=LocalMemberBackend(store), signals=store,
                     component_secret=b"k" * 32, guild_id=GUILD, clock=lambda: NOW)
    prompt = svc.remove(Actor(A, "1300000000000000001", GUILD), position_id="1")
    assert prompt.code == "CONFIRM_REQUIRED"
    done = svc.component(Actor(A, "1300000000000000002", GUILD), prompt.rows[0][0].custom_id)
    assert done.code == "PERSISTED"
    left = [p["lots"][0]["fill_price"] for p in LocalPositionBackend(store).positions(A)]
    assert left == ["101.5"]
    # B cannot remove A's starter entry #2 by number
    assert svc.remove(Actor(B, "1300000000000000003", GUILD), position_id="2").code == "POSITION_NOT_FOUND"


def test_unsupported_schema_and_missing_file(env):
    other = env["tmp"] / "other.sqlite3"
    with sqlite3.connect(other) as c:
        c.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, foo TEXT)")
    rep = migrate_starter(other, dry_run=False, store=env["store"], now=NOW)
    assert rep["status"] == "unsupported_schema" and rep["backup"] is None
    assert migrate_starter(env["tmp"] / "nope.sqlite3", store=env["store"])["status"] == "not_found"


def test_cli_wiring_uses_dry_run_by_default(env, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("APEX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("APEX_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.delenv("APEX_LOCAL_DB", raising=False)
    from apex_worker.cli import main

    assert main(["migrate-starter", "--starter-db", str(env["starter"])]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["counts"]["would_import"] == 4
    assert out["target_db"].endswith("apex_local.sqlite3")
    assert main(["migrate-starter", "--starter-db", str(env["starter"]), "--apply"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["counts"]["imported"] == 4 and out["backup"]
