"""Input validation for member-entered values and friendly messages for every backend error code.

Numbers are parsed from STRINGS into Decimal. Slash-command `number` options arrive as Python floats; they are
converted with str() first (Discord sends the literal the member typed, so str() round-trips it exactly) and
then validated with the ledger's own precision and safety limits.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime, time
from decimal import Decimal

from apex_worker.portfolio.ledger import (
    EARLIEST_FILL,
    FEES_MAX_DP,
    FUTURE_SKEW,
    MAX_FEES,
    MAX_PRICE,
    MAX_QUANTITY,
    PRICE_MAX_DP,
    QTY_MAX_DP,
    LedgerError,
    fmt,
    parse_decimal,
)
from apex_worker.timeutil import ET

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
SIGNAL_ID_RE = re.compile(r"^sig_[0-9a-f]{24}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
NOTES_MAX = 280
LEGACY_REF_MAX = 80


class InputError(ValueError):
    """A member input problem. `code` is stable; the message is safe to show to that member."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def parse_symbol(raw: str | None) -> str:
    s = (raw or "").strip().upper().lstrip("$")
    if not SYMBOL_RE.fullmatch(s):
        raise InputError("INVALID_SYMBOL", "Enter a valid US ticker: 1-10 characters, letters/digits/dot, starting with "
                                           "a letter (for example NVDA or BRK.B).")
    return s


def _number_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        raise InputError("INVALID_NUMBER", "Expected a number.")
    text = str(value).strip()
    if text.startswith("$"):
        text = text[1:].strip()
    if "," in text:
        raise InputError("INVALID_NUMBER", "Use a dot for decimals and no thousands separators (for example 1250.5).")
    return text


def _decimal(value: object, field: str, *, max_dp: int, max_value: Decimal, allow_zero: bool = False) -> str:
    text = _number_text(value)
    if not text:
        raise InputError("MISSING_VALUE", f"{field} is required.")
    try:
        return fmt(parse_decimal(text, field, max_dp=max_dp, max_value=max_value, allow_zero=allow_zero))
    except LedgerError as exc:
        raise InputError(exc.code, f"{field.capitalize()}: {exc}.") from exc


def parse_price(value: object, field: str = "price") -> str:
    return _decimal(value, field, max_dp=PRICE_MAX_DP, max_value=MAX_PRICE)


def parse_quantity(value: object, field: str = "shares") -> str:
    return _decimal(value, field, max_dp=QTY_MAX_DP, max_value=MAX_QUANTITY)


def parse_fees(value: object) -> str:
    text = _number_text(value)
    if not text:
        return "0"
    return _decimal(text, "fees", max_dp=FEES_MAX_DP, max_value=MAX_FEES, allow_zero=True)


def parse_fill_time(raw: str | None, now: datetime) -> datetime:
    """'now' / empty, 'HH:MM' (today, New York time), or ISO-8601 date+time. No zone means New York time."""
    s = (raw or "").strip()
    now = now.astimezone(UTC)
    if not s or s.lower() == "now":
        return now.replace(microsecond=0)
    if re.fullmatch(r"\d{1,2}:\d{2}", s):
        hh, mm = (int(x) for x in s.split(":"))
        if hh > 23 or mm > 59:
            raise InputError("INVALID_TIME", "Fill time HH:MM must be a valid 24-hour time (New York).")
        today_et = now.astimezone(ET).date()
        dt = datetime.combine(today_et, time(hh, mm), tzinfo=ET)
    else:
        if not re.search(r"[T ]\d{1,2}:\d{2}", s):
            raise InputError("INVALID_TIME", "Fill time needs a date AND time, e.g. 2026-10-20 10:31 (New York), "
                                             "2026-10-20T14:31Z, or 'now'.")
        try:
            dt = datetime.fromisoformat(s.replace(" ", "T", 1) if "T" not in s else s)
        except ValueError as exc:
            raise InputError("INVALID_TIME", "Could not read the fill time. Use 'now', HH:MM, or "
                                             "YYYY-MM-DD HH:MM (New York time).") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ET)
    dt = dt.astimezone(UTC).replace(microsecond=0)
    if dt > now + FUTURE_SKEW:
        raise InputError("FUTURE_FILL", "That fill time is in the future. Times without a zone are New York time.")
    if dt < EARLIEST_FILL:
        raise InputError("INVALID_TIME", "That fill time is implausibly old.")
    return dt


def parse_notes(raw: str | None) -> str:
    s = (raw or "").strip()
    s = "".join(ch for ch in s if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    if len(s) > NOTES_MAX:
        raise InputError("NOTES_TOO_LONG", f"Notes are limited to {NOTES_MAX} characters (you entered {len(s)}).")
    return s


def classify_signal_ref(raw: str | None) -> tuple[str | None, str | None]:
    """Returns (apex_signal_id, legacy_ref). Starter refs such as DEMO-001 are kept only as a private note."""
    s = (raw or "").strip()
    if not s:
        return None, None
    if SIGNAL_ID_RE.fullmatch(s):
        return s, None
    if len(s) > LEGACY_REF_MAX:
        raise InputError("SIGNAL_REF_TOO_LONG", f"Signal ID is too long ({LEGACY_REF_MAX}-character maximum).")
    if s.lower().startswith("sig_"):
        raise InputError("UNKNOWN_SIGNAL", "That does not look like an APEX signal id (sig_ followed by 24 hex "
                                           "characters). Use /apex_signals to pick one.")
    return None, parse_notes(s)


# --- friendly messages for every ServiceError / LedgerError / InputError code -------------------------------------
FRIENDLY: dict[str, str] = {
    # services.positions.ServiceError
    "MEMBERSHIP_REVOKED": "Your APEX access was revoked. Ask the server owner if you think this is a mistake.",
    "POSITION_REQUIRED": "This action needs one of your existing positions. Pick one from /apex_positions.",
    "UNKNOWN_SIGNAL": "That is not a published APEX signal. Use /apex_signals or the alert buttons.",
    "SYMBOL_MISMATCH": "The ticker you entered does not match that signal's ticker.",
    "SYMBOL_REQUIRED": "A ticker is required.",
    "POSITION_NOT_FOUND": "No position with that reference belongs to you.",
    # portfolio.ledger.LedgerError
    "INVALID_NUMBER": "That is not a valid number.",
    "NON_POSITIVE": "Values must be greater than zero.",
    "TOO_LARGE": "That value exceeds the safety limit.",
    "TOO_PRECISE": "Too many decimal places (prices: 4, shares: 6, fees: 4).",
    "INVALID_TIME": "The fill time is not valid.",
    "FUTURE_FILL": "The fill time cannot be in the future.",
    "BAD_SEQUENCE": "Your position history changed while saving. Please try again.",
    "NO_POSITION": "There is nothing to change on that position yet.",
    "DUPLICATE_EVENT": "That change was already recorded.",
    "MISSING_FILL": "A price, quantity and time are required for this action.",
    "MISSING_CORRECTION": "Choose which fill to correct.",
    "UNKNOWN_TARGET": "That fill is not part of this position.",
    "BAD_TARGET": "Only buys/sells can be corrected, and only corrections can be undone.",
    "MISSING_REPLACEMENT": "A replacement correction needs the corrected price and shares.",
    "ALREADY_CANCELLED": "That correction was already undone.",
    "NOTHING_TO_SELL": "No shares remain at that fill time.",
    "OVERSELL": "You cannot sell more shares than you hold.",
    "ALREADY_EXISTS": "You are already tracking this.",
    "ALREADY_OPEN": "This position already has an entry. Use /apex_add for another buy.",
    "NOT_OPEN": "That position is not open.",
    "NOT_WATCHING": "Only a watch-only position can be unwatched; use /apex_close for an open position.",
    "CLOSE_QUANTITY_MISMATCH": "Closing sells your whole remaining quantity; use /apex_reduce to sell part.",
    # discord_bot.validation.InputError
    "INVALID_SYMBOL": "Enter a valid ticker.",
    "MISSING_VALUE": "A required value is missing.",
    "NOTES_TOO_LONG": f"Notes are limited to {NOTES_MAX} characters.",
    "SIGNAL_REF_TOO_LONG": "Signal ID is too long.",
    "AMBIGUOUS_POSITION": "That reference matches more than one of your positions; pick one from the list.",
    "NO_FILL_SELECTED": "Choose which fill to correct.",
    "NOT_REMOVABLE": "This position has several fills; use /apex_correct to void a specific fill.",
    "SIGNAL_NOT_OPEN": "This signal is no longer open for new entries.",
}


def friendly(code: str, detail: str | None = None) -> str:
    head = FRIENDLY.get(code, "That request could not be completed.")
    if detail and detail.rstrip(".").lower() not in head.rstrip(".").lower():
        return f"{head}\n-# {detail.strip()}"
    return head
