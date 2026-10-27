"""Parse the owner's Discord messages into account commands (pure: no network, no clock).

Case does not matter, `@` works for `at`, and a `$` or thousands commas are ignored. Times are US Eastern, and a
trade's time defaults to the message's time.

  start 1000                        the account starts with $1000 in cash (`start` alone: $1000)
  bought 2 XLK at 245.10            a whole-share buy at the fill price (also: buy, add, added)
  sold 3 XLE @ 90.20 on 10/30 9:45  a sell (also: sell, exit, trim) on a given date and time
  deposit 500 · withdraw 200        cash in or out (the SPY comparison receives the same flows)
  dividend XLU 3.12                 a cash dividend received
  split XLK 2:1                     the broker split the shares, 2 new for 1 old
  skip XLK · skip all               an order of the latest alert that will not be taken
  undo                              remove the last record
  status · plan · trades · help
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from apex_worker.timeutil import ET

VERBS = {
    "start": "start", "started": "start",
    "bought": "buy", "buy": "buy", "add": "buy", "added": "buy", "purchased": "buy",
    "sold": "sell", "sell": "sell", "exit": "sell", "exited": "sell", "trim": "sell", "trimmed": "sell",
    "deposit": "deposit", "deposited": "deposit",
    "withdraw": "withdraw", "withdrew": "withdraw", "withdrawal": "withdraw",
    "dividend": "dividend", "div": "dividend",
    "split": "split",
    "skip": "skip", "skipped": "skip",
    "undo": "undo",
    "status": "status", "positions": "status", "pnl": "status", "p/l": "status", "portfolio": "status",
    "balance": "status",
    "plan": "plan",
    "trades": "trades", "history": "trades",
    "help": "help", "?": "help", "commands": "help",
}
FILLER = {"on", "shares", "share", "sh", "x", "of", "the", "and", "my", "at"}
DEFAULT_START = Decimal("1000")
MAX_SHARES = 1_000_000
MAX_PRICE = Decimal("1000000")
MAX_AMOUNT = Decimal("100000000")
FUTURE_SKEW = timedelta(minutes=2)
EARLIEST = date(2016, 1, 4)  # the market calendar's first session

_NUM = re.compile(r"^(\d+(?:\.\d+)?|\.\d+)$")
_SYM = re.compile(r"^[a-z]{1,5}(?:[.\-/][a-z]{1,2})?$")
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_US = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})(am|pm)?$")
_HOUR = re.compile(r"^(\d{1,2})(am|pm)$")
_RATIO = re.compile(r"^(\d+)(?::|/|-for-|for)(\d+)$")


class CommandError(ValueError):
    """A message that is not a valid command. `str(err)` is safe to show the owner (never logged: it may quote the
    message)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Command:
    verb: str  # start | buy | sell | deposit | withdraw | dividend | split | skip | undo | status | plan | trades | help
    symbol: str | None = None  # buy, sell, dividend, split, skip (None for `skip all`)
    shares: int | None = None
    price: Decimal | None = None
    amount: Decimal | None = None  # start, deposit, withdraw, dividend
    ratio: tuple[int, int] | None = None  # split: (new, old)
    at: datetime | None = None  # the time the owner gave (UTC); None = the message's time


def _decimal(tok: str, what: str, *, places: int, limit: Decimal) -> Decimal:
    try:
        v = Decimal(tok)
    except InvalidOperation:
        raise CommandError("NUMBER", f"{what} is not a number") from None
    if v <= 0:
        raise CommandError("NUMBER", f"{what} must be positive")
    if v > limit:
        raise CommandError("NUMBER", f"{what} is implausibly large")
    if -v.as_tuple().exponent > places:  # type: ignore[operator]
        raise CommandError("NUMBER", f"{what} allows at most {places} decimals")
    return v


def _normalize(text: str) -> list[str]:
    s = text.strip().lower().replace("@", " at ").replace("$", "")
    while re.search(r"(\d),(\d{3})(?!\d)", s):
        s = re.sub(r"(\d),(\d{3})(?!\d)", r"\1\2", s)
    return re.sub(r"[,;]+", " ", s).split()


def _date(tok: str, ref: date) -> date | None:
    if tok == "today":
        return ref
    if tok == "yesterday":
        return ref - timedelta(days=1)
    try:
        if m := _ISO.match(tok):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := _US.match(tok):
            if m[3]:
                y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
                return date(y, int(m[1]), int(m[2]))
            d = date(ref.year, int(m[1]), int(m[2]))
            return d if d <= ref else date(ref.year - 1, int(m[1]), int(m[2]))
    except ValueError:
        raise CommandError("DATE", "that date does not exist") from None
    return None


def _time(tok: str, nxt: str | None) -> tuple[time, bool] | None:
    """(time, consumed the next token as am/pm), or None when `tok` is not a time."""
    m, h = _TIME.match(tok), _HOUR.match(tok)
    if not m and not h:
        return None
    hour, minute = (int(m[1]), int(m[2])) if m else (int(h[1]), 0)
    suffix = (m[3] if m else h[2]) or (nxt if nxt in ("am", "pm") else None)
    if suffix == "pm" and hour < 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        raise CommandError("TIME", "that time does not exist")
    return time(hour, minute), bool(suffix and not (m and m[3]) and not h)


def parse(text: str, *, sent_at: datetime) -> Command:
    """The command in `text`, sent at `sent_at` (aware). Raises CommandError with a message for the owner."""
    toks = _normalize(text)
    if not toks:
        raise CommandError("EMPTY", "empty message")
    verb = VERBS.get(toks[0])
    if verb is None:
        raise CommandError("UNKNOWN", "I did not understand that. Type `help` for the commands.")
    rest = toks[1:]
    if verb in ("undo", "status", "plan", "trades", "help"):
        return Command(verb)
    ref = sent_at.astimezone(ET)
    day: date | None = None
    clock: time | None = None
    price: Decimal | None = None
    marked_shares: list[str] = []
    numbers: list[str] = []
    symbols: list[str] = []
    ratio: tuple[int, int] | None = None
    i = 0
    while i < len(rest):
        tok, nxt = rest[i], rest[i + 1] if i + 1 < len(rest) else None
        if tok == "for":
            raise CommandError("FOR", "write the price per share with `at`, e.g. `bought 2 XLK at 245.10`")
        if verb == "split" and ratio is None and (m := _RATIO.match(tok)):
            ratio = (int(m[1]), int(m[2]))
            i += 1
            continue
        if (d := _date(tok, ref.date())) is not None:
            if day is not None:
                raise CommandError("DATE", "two dates in one message")
            day = d
            i += 1
            continue
        if tok == "at" and nxt is not None and _time(nxt, rest[i + 2] if i + 2 < len(rest) else None):
            i += 1
            continue
        if (t := _time(tok, nxt)) is not None:
            if clock is not None:
                raise CommandError("TIME", "two times in one message")
            clock, ate = t
            i += 2 if ate else 1
            continue
        if tok == "at" and nxt is not None and _NUM.match(nxt):
            if price is not None:
                raise CommandError("PRICE", "two prices in one message")
            price = _decimal(nxt, "the price", places=4, limit=MAX_PRICE)
            i += 2
            continue
        if _NUM.match(tok):
            if nxt in ("shares", "share", "sh", "x"):
                marked_shares.append(tok)
                i += 2
                continue
            numbers.append(tok)
            i += 1
            continue
        if tok in FILLER or tok in ("am", "pm", "all"):
            i += 1
            continue
        if _SYM.match(tok):
            symbols.append(tok.upper())
            i += 1
            continue
        raise CommandError("TOKEN", f"I could not read `{tok[:20]}`. Type `help` for examples.")
    if len(symbols) > 1:
        raise CommandError("SYMBOL", "one symbol per message, please")
    sym = symbols[0] if symbols else None
    at = _resolve_at(day, clock, sent_at)

    if verb in ("start", "deposit", "withdraw"):
        if sym or len(numbers) > 1 or marked_shares:
            raise CommandError("AMOUNT", f"write it as `{verb} 500`")
        raw = numbers[0] if numbers else (str(price) if price is not None else None)
        if raw is None and verb != "start":
            raise CommandError("AMOUNT", f"how much? e.g. `{verb} 500`")
        amount = DEFAULT_START if raw is None else _decimal(raw, "the amount", places=2, limit=MAX_AMOUNT)
        return Command(verb, amount=amount, at=at)
    if verb == "skip":
        if numbers or price is not None:
            raise CommandError("SKIP", "write it as `skip XLK` or `skip all`")
        return Command("skip", symbol=sym)
    if sym is None:
        raise CommandError("SYMBOL", "which symbol? e.g. `bought 2 XLK at 245.10`")
    if verb == "dividend":
        raw = numbers[0] if len(numbers) == 1 else (str(price) if price is not None and not numbers else None)
        if raw is None or marked_shares:
            raise CommandError("AMOUNT", "write it as `dividend XLU 3.12` (the cash received)")
        return Command("dividend", symbol=sym, amount=_decimal(raw, "the amount", places=2, limit=MAX_AMOUNT), at=at)
    if verb == "split":
        if ratio is None and len(numbers) == 1 and numbers[0].isdigit():
            ratio = (int(numbers[0]), 1)
        if ratio is None or ratio[0] <= 0 or ratio[1] <= 0 or ratio[0] == ratio[1]:
            raise CommandError("SPLIT", "write it as `split XLK 2:1` (new shares : old shares)")
        return Command("split", symbol=sym, ratio=ratio, at=at)
    # buy / sell
    shares_tok = marked_shares[0] if marked_shares else None
    bare = list(numbers)
    if len(marked_shares) > 1:
        raise CommandError("SHARES", "two share counts in one message")
    if shares_tok is None and bare:
        ints = [n for n in bare if n.isdigit()]
        if price is None and len(bare) == 2 and len(ints) == 1:
            shares_tok = ints[0]  # `bought XLK 245.10 2`: the integer is the share count
        else:
            shares_tok = bare[0]
        bare.remove(shares_tok)
    if price is None and len(bare) == 1:
        price = _decimal(bare.pop(), "the price", places=4, limit=MAX_PRICE)
    if bare:
        raise CommandError("NUMBERS", "too many numbers: write it as `bought 2 XLK at 245.10`")
    if shares_tok is None or price is None:
        raise CommandError("TRADE", f"I need the shares and the price, e.g. `{'bought' if verb == 'buy' else 'sold'} "
                                    f"2 {sym} at 245.10`")
    if not shares_tok.isdigit():
        raise CommandError("WHOLE", "whole shares only (Ally has no fractional shares)")
    shares = int(shares_tok)
    if shares <= 0 or shares > MAX_SHARES:
        raise CommandError("SHARES", "the share count must be between 1 and 1,000,000")
    return Command(verb, symbol=sym, shares=shares, price=price, at=at)


def _resolve_at(day: date | None, clock: time | None, sent_at: datetime) -> datetime | None:
    if day is None and clock is None:
        return None
    ref = sent_at.astimezone(ET)
    at = datetime.combine(day or ref.date(), clock or time(12, 0), ET).astimezone(UTC)
    if clock is None and at > sent_at:  # a date alone means "that day", never later than the message
        at = sent_at.astimezone(UTC)
    if at > sent_at + FUTURE_SKEW:
        raise CommandError("FUTURE", "that time is in the future")
    if at.astimezone(ET).date() < EARLIEST:
        raise CommandError("DATE", "that date is too far back")
    return at


__all__ = ["DEFAULT_START", "VERBS", "Command", "CommandError", "parse"]
