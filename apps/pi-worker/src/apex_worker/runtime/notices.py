"""Private #system-status notices raised by the Pi (system health, NOT investment alerts).

Each notice is a SYSTEM_ALERT intent on the `system_status` channel route, queued in the bot's LOCAL outbox (the
Pi can raise them while the registry/server is unreachable). Event keys carry a day bucket, so the local outbox's
unique event_key guarantees AT MOST ONE notice per kind per day (per model for expiry notices).

Inputs come from the server `/config` -> `training` block {last_success_at, last_run_at, last_run_status, champions},
from ModelSync (registry reachability, local model validity) and from the Alpaca QuotaTracker.

Routing (NoticeRouter): connected, a notice is POSTed to /notices so the dashboard records it; the bot then receives
it through the normal outbox bridge under the SAME event_key. Only when the server cannot take it (unreachable,
device not approved) is it queued straight into the bot's local outbox. It is never both: a notice the server queued
is not queued locally, and a later server copy of a locally-delivered notice collapses on the local unique event_key.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from apex_worker.agent.client import AgentClient, AgentHTTPError, AgentUnavailable
from apex_worker.notifications import intents
from apex_worker.timeutil import iso_utc, parse_utc

log = logging.getLogger("apex.runtime.notices")

MISSED_TRAINING_DAYS = 8
EXPIRY_WARN_DAYS = 3
REGISTRY_FAILURES = 3  # consecutive failed manifest polls (~2 h at the default 45-minute cadence)
REGISTRY_STALE_HOURS = 6


def _day(now: datetime) -> str:
    return now.strftime("%Y%m%d")


def _parse(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return parse_utc(value)
    except ValueError:
        return None


class NoticeRouter:
    """enqueue(intent, now) for Pi system notices: server outbox first, local outbox only as the offline fallback."""

    def __init__(self, client: AgentClient | None, local_enqueue: Callable[[dict, datetime], bool]):
        self.client = client
        self.local_enqueue = local_enqueue
        self.last_route: str | None = None

    def __call__(self, intent: dict, now: datetime) -> bool:
        if self.client is not None and not self.client.offline:
            try:
                res = self.client.post("notices", {"intents": [intent]}).data or {}
            except AgentUnavailable:
                pass
            except AgentHTTPError as exc:
                if exc.status == 422:  # not a system notice: never delivered anywhere
                    log.warning("server refused a system notice: %s", exc.code)
                    return False
                # device pending / auth problem: the server cannot record it, so deliver locally
            else:
                self.last_route = "server"
                return int(res.get("queued") or 0) > 0
        self.last_route = "local"
        return bool(self.local_enqueue(intent, now))


class SystemNotices:
    def __init__(self, enqueue: Callable[[dict, datetime], bool], *, is_demo: bool):
        self.enqueue = enqueue
        self.is_demo = is_demo
        self.status: dict[str, str] = {}

    def _send(self, kind: str, title: str, detail: str, action: str, now: datetime, bucket: str,
              severity: str = "NORMAL", last_good: datetime | None = None) -> bool:
        intent = intents.system_alert(kind, title, detail, action, now, self.is_demo, bucket,
                                      iso_utc(last_good) if last_good else None, severity)
        return bool(self.enqueue(intent, now))

    def check_training(self, training: dict | None, now: datetime) -> str:
        """Missed-run detector: no successful scheduled training within 8 days -> one notice per day."""
        if not isinstance(training, dict):
            self.status["training"] = "UNKNOWN"  # server too old to expose /config.training
            return "UNKNOWN"
        last_ok, last_run = _parse(training.get("last_success_at")), _parse(training.get("last_run_at"))
        if last_ok is None and last_run is None:
            self.status["training"] = "UNKNOWN"  # schedule never ran: nothing to compare against (not an alarm)
            return "UNKNOWN"
        if last_ok is not None and now - last_ok <= timedelta(days=MISSED_TRAINING_DAYS):
            self.status["training"] = "OK"
            return "OK"
        age = f"{(now - last_ok).days} days ago" if last_ok else "never"
        self._send("MISSED_TRAINING", "Weekly model training missed",
                   f"Last successful training run: {age} (SLA {MISSED_TRAINING_DAYS} days). Last run status: "
                   f"{training.get('last_run_status') or 'unknown'}. GitHub schedules can be delayed or skipped.",
                   "Check GitHub Actions (schedule, minutes budget, release secret). The Pi keeps running the current "
                   "model until its validity expires, then publishes research setups only.",
                   now, _day(now), "HIGH", last_ok)
        self.status["training"] = "STALE"
        return "STALE"

    def check_registry(self, consecutive_failures: int, last_success_at: datetime | None, now: datetime) -> str:
        stale = last_success_at is not None and now - last_success_at > timedelta(hours=REGISTRY_STALE_HOURS)
        if consecutive_failures < REGISTRY_FAILURES and not stale:
            self.status["registry"] = "OK" if last_success_at else "UNKNOWN"
            return self.status["registry"]
        self._send("REGISTRY_UNREACHABLE", "Model registry unreachable from the Pi",
                   f"{consecutive_failures} consecutive manifest polls failed. The Pi keeps using its last verified "
                   "model while that model's validity lasts; it never falls back to unverified predictions.",
                   "Check Vercel/Supabase status (a paused Supabase project recovers on the next legitimate query).",
                   now, _day(now), "HIGH", last_success_at)
        self.status["registry"] = "DOWN"
        return "DOWN"

    def check_expiry(self, models: list[tuple[str, str, str]], now: datetime) -> list[str]:
        """models: (strategy_id, model_id, validation_expires_at). Warn once per model per day within 3 days."""
        warned = []
        for sid, mid, exp_s in models:
            exp = _parse(exp_s)
            if exp is None or exp - now > timedelta(days=EXPIRY_WARN_DAYS):
                continue
            expired = exp <= now
            self._send("MODEL_EXPIRED" if expired else "MODEL_EXPIRING",
                       f"Model {'expired' if expired else 'validity ends soon'}: {sid}",
                       f"{mid} validation {'expired' if expired else 'expires'} at {exp_s}. After expiry the Pi "
                       "publishes NO high-conviction signals for this strategy (UNVALIDATED research setups only).",
                       "A successful weekly training run with a passing promotion gate renews it.",
                       now, f"{mid}:{_day(now)}", "HIGH" if expired else "NORMAL")
            warned.append(mid)
        self.status["expiry"] = "WARN" if warned else "OK"
        return warned

    def check_quota(self, snapshots: list[dict], now: datetime) -> list[str]:
        """QuotaTracker snapshots; WARN (70%) or worse -> one notice per provider per day."""
        low = []
        for snap in snapshots:
            if snap.get("level") in ("WARN", "RESTRICTED", "CRITICAL"):
                prov = str(snap.get("provider", "provider"))
                self._send("QUOTA_LOW", f"{prov} quota {snap['level']}",
                           f"{snap.get('used')}/{snap.get('limit')} requests used today ({snap.get('fraction')}). At 85% "
                           "optional work pauses; at 95% only position monitoring continues. Nothing auto-upgrades.",
                           "Reduce universe size or intraday cadence in Settings if this repeats.",
                           now, f"{prov}:{_day(now)}", "NORMAL")
                low.append(prov)
        self.status["quota"] = "LOW" if low else "OK"
        return low

    def stale_feed(self, symbols: list[str], last_good: datetime | None, now: datetime) -> bool:
        return self._send("STALE_DATA", "Market data is stale",
                          f"No fresh verified price for {len(symbols)} monitored symbol(s) "
                          f"({', '.join(sorted(symbols)[:6])}). No new price-based alerts are sent until data recovers.",
                          "Check the Pi network and the Alpaca status page; the worker retries automatically.",
                          now, now.strftime("%Y%m%dT%H"), "HIGH", last_good)
