"""One run of the personal alerts (`apex personal run`, scheduled by .github/workflows/apex-personal.yml).

1. Read the private channel and rebuild the account from the bot's own records (personal/book.py).
2. Answer the owner's new messages in order. Each reply carries its record, so a message is answered once.
3. Once a session's daily bars are final, post what is due:
   - the daily summary;
   - the method's decision when one is due (the first after `start`, then the last session of each month);
   - on the next morning, a reminder of orders not yet reported.
Only counts leave a run (the Actions log is public); everything personal stays in the private channel.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from apex_worker.market.calendar import MarketCalendar, default_calendar
from apex_worker.personal import view as V
from apex_worker.personal.book import Book, BookError, Record, build, iso, parse_at, pending, shell
from apex_worker.personal.commands import Command, CommandError, parse
from apex_worker.personal.market import Request, Snapshot
from apex_worker.personal.method import METHODS, Decision, MethodSpec, decide, is_decision_day
from apex_worker.personal.records import decode_all, encode, nonce
from apex_worker.research.training_data import BENCHMARK
from apex_worker.timeutil import ET

PERSONAL_METHOD = "sector-mom-3"  # the registered studies pick it (docs/discovery-v3/PERSONAL_STUDY_1.md, _2.md)
METHOD_STATUS = {  # what the registered studies found, shown in the alerts (research-results/discovery-v3/)
    "sector-mom-3": "EXPERIMENT: it did not beat SPY on 2019-2026 data (personal-study-1, -4.0%/yr)",
}
SETTLE = timedelta(minutes=30)  # a session's daily bars are final 30 minutes after its close (SIP: 15-minute delay)
HISTORY_DAYS = 400  # calendar days of daily bars: the 126-session signal with room to spare
MAX_LEN = 1990


class MarketUnavailable(RuntimeError):
    """The market data could not be read (the exception type only: no message text)."""


@dataclass(frozen=True)
class Config:
    channel_id: str
    method_id: str = PERSONAL_METHOD
    allowed_users: frozenset[str] = frozenset()  # empty: anyone who can post in the private channel
    daily_summary: bool = True
    live: bool = True  # False (dry): read and compute, post nothing


@dataclass
class RunReport:
    """Counts only: the public Actions log shows nothing else."""

    dry: bool = False
    messages_read: int = 0
    records: int = 0
    answered: int = 0
    posted: int = 0
    welcome: bool = False
    summary: bool = False
    alert: bool = False
    reminder: bool = False
    notices: int = 0
    waiting_for_bars: bool = False
    market_data: bool | None = None  # read this run (a dry run always reads it: the setup check)

    def to_dict(self) -> dict:
        return asdict(self)


def month_end(cal: MarketCalendar, d: date) -> date:
    """The last session of d's month."""
    first_next = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return cal.previous_session(first_next)


def _chunks(text: str) -> list[str]:
    """Split at line breaks into messages Discord accepts; the record line (the last line) ends the last one."""
    out, cur = [], ""
    for raw in text.split("\n"):
        line = raw
        while len(line) > MAX_LEN:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:MAX_LEN])
            line = line[MAX_LEN:]
        if cur and len(cur) + 1 + len(line) > MAX_LEN:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


@dataclass
class Runner:
    cfg: Config
    discord: object  # DiscordREST or MemoryChannel: me(), history(channel), send(channel, content, ...)
    market: Callable[[Request], Snapshot]
    now: datetime
    cal: MarketCalendar = field(default_factory=default_calendar)

    def __post_init__(self) -> None:
        self.now = self.now.astimezone(UTC)
        self.spec = METHODS[self.cfg.method_id]
        self.status = METHOD_STATUS.get(self.cfg.method_id, "UNVALIDATED: no registered study adopted it")
        self.report = RunReport(dry=not self.cfg.live)
        self._snap: Snapshot | None = None
        self._line = 0  # the line of the owner's message being answered
        self._symbols: set[str] = set()
        self._earliest: date | None = None

    # ---- channel ----------------------------------------------------------------------------------------------------
    def run(self) -> RunReport:
        me = self.discord.me()
        self.bot_id = str(me["id"])
        msgs = self.discord.history(self.cfg.channel_id)
        self.report.messages_read = len(msgs)
        self.max_id = max((int(m["id"]) for m in msgs), default=0)
        self.records = [Record(str(m["id"]), rec) for m in msgs if str(m["author"]["id"]) == self.bot_id
                        for rec in decode_all(m.get("content") or "")]
        self.report.records = len(self.records)
        done = {(str(r.data["ref"]), int(r.data.get("line") or 0)) for r in self.records if r.data.get("ref")}
        todo = [m for m in msgs if self._from_owner(m)]
        self.broken: BookError | None = None
        try:
            self.book = build(self.records)
        except BookError as exc:  # only if bot messages were deleted by hand: `undo` still works
            self.broken, self.book = exc, shell(self.records)
        if not self.records:
            self._post(V.help_text(self.spec, self.status), {"k": "hello"})
            self.report.welcome = True
        blank = [m for m in todo if (str(m["id"]), 0) not in done and not self._text(m)
                 and not m.get("attachments") and not m.get("sticker_items")]
        if blank:
            self._note("no-content", "⚠️ I can see messages but not their text. In the Discord developer portal, open "
                       "your app → Bot and turn on **Message Content Intent** (or start each message by mentioning me). "
                       "I'll answer them once I can read them.")
            todo = [m for m in todo if m not in blank]
        parsed = []
        for m in todo:  # one command per line: a message can report every fill of an alert
            lines = [ln for ln in self._text(m).splitlines() if ln.strip()] or [""]
            for i, line in enumerate(lines):
                if (str(m["id"]), i) in done:
                    continue
                try:
                    cmd: Command | CommandError = parse(line, sent_at=parse_at(m["timestamp"]))
                    if cmd.symbol:
                        self._symbols.add(cmd.symbol)
                    if cmd.at:
                        d = cmd.at.astimezone(ET).date()
                        self._earliest = d if self._earliest is None else min(self._earliest, d)
                except CommandError as exc:
                    cmd = exc
                parsed.append((m, i, cmd))
        for m, i, cmd in parsed:
            self._line = i
            self._answer(m, cmd)
        if self.broken is not None:
            self._note("broken", f"⚠️ Your records don't add up: {self.broken} Reply `undo` to remove the last one.")
        elif self.book.started_at is not None:
            self._scheduled()
        if not self.cfg.live and self._snap is None:  # the setup check: Discord was read above, now Alpaca
            _ = self.snap
        if self._snap is not None:
            self.report.market_data = self._final_session() is not None or bool(self._snap.bars)
        return self.report

    def _from_owner(self, m: dict) -> bool:
        a = m.get("author") or {}
        if a.get("bot") or str(a.get("id")) == self.bot_id or m.get("type", 0) not in (0, 19):
            return False
        return not self.cfg.allowed_users or str(a.get("id")) in self.cfg.allowed_users

    def _text(self, m: dict) -> str:
        t = m.get("content") or ""
        for tag in (f"<@{self.bot_id}>", f"<@!{self.bot_id}>"):
            t = t.replace(tag, " ")
        return t.strip()

    def _post(self, text: str, rec: dict, *, reply_to: str | None = None) -> Record:
        chunks = _chunks(f"{text}\n{encode(rec)}")
        posted: dict = {}
        for i, chunk in enumerate(chunks):
            last = i == len(chunks) - 1
            if self.cfg.live:
                posted = self.discord.send(self.cfg.channel_id, chunk, reply_to=reply_to if i == 0 else None,
                                           nonce=nonce(rec) if last else None)
            else:
                posted = {"id": str(self.max_id + 1)}
            self.max_id = max(self.max_id, int(posted["id"]))
            self.report.posted += 1
        r = Record(str(posted["id"]), rec)
        self.records.append(r)
        try:
            self.book, self.broken = build(self.records), None
        except BookError as exc:
            self.broken, self.book = exc, shell(self.records)
        return r

    def _note(self, what: str, text: str, **key) -> None:
        today = self.now.astimezone(ET).date().isoformat()
        rec = {"k": "note", "what": what, "day": key.pop("day", today), **key}
        if any(all(r.data.get(k) == v for k, v in rec.items()) for r in self.records if r.kind == "note"):
            return
        self._post(text, rec)
        self.report.notices += 1

    # ---- market data ------------------------------------------------------------------------------------------------
    @property
    def snap(self) -> Snapshot:
        if self._snap is None:
            today = self.now.astimezone(ET).date()
            held = set(self.book.held())
            start = today - timedelta(days=HISTORY_DAYS)
            if self._earliest:
                start = min(start, self._earliest - timedelta(days=7))
            since = self.book.started_at.astimezone(ET).date() if self.book.started_at else today
            try:
                self._snap = self.market(Request(
                    daily=tuple(sorted({BENCHMARK, *self.spec.data_symbols, *held, *self._symbols})),
                    latest=tuple(sorted({BENCHMARK, *held, *self._symbols})), start=start, end=today,
                    actions=tuple(sorted({BENCHMARK, *held})), actions_start=min(since, today) - timedelta(days=7)))
            except Exception as exc:  # one private note a day; the run then fails visibly
                self._note("market-down", "⚠️ The market data is unavailable right now, so I'm holding replies and alerts "
                           "until the next run.")
                status = getattr(exc, "status", None)  # an HTTP status, never a message
                raise MarketUnavailable(type(exc).__name__ + (f" {status}" if isinstance(status, int) else "")) from None
        return self._snap

    def _final_session(self) -> date | None:
        """The latest session whose daily bars are final for SPY and the method's symbols (a rotation tolerates one
        missing ETF: it is simply not ranked)."""
        last = self.cal.latest_completed_session(self.now - SETTLE)
        if not self.snap.has_day(BENCHMARK, last):
            return None
        need = self.spec.data_symbols
        have = sum(self.snap.has_day(s, last) for s in need)
        return last if have >= len(need) - (1 if isinstance(self.spec, MethodSpec) else 0) else None

    def _complete(self, dec: Decision) -> bool:
        """Never trade on a partial picture (a rotation without most ranks would EXIT everything)."""
        if isinstance(self.spec, MethodSpec):
            return len(dec.signals) >= len(self.spec.universe) - 1
        return bool(dec.target)

    def _basis(self, dec: Decision) -> str:
        if isinstance(self.spec, MethodSpec):
            return "Selection (6-month return): " + (", ".join(f"{s} ({V.spct(dec.signals[s])})" for s in dec.target)
                                                     or "none (cash)")
        return f"Signal: {dec.explain}"

    def _prices_now(self) -> tuple[dict[str, float], float | None, str]:
        held = list(self.book.held())
        if self.cal.is_open(self.now):
            prices = {s: self.snap.latest[s][0] for s in held if s in self.snap.latest}
            for s in held:
                if s not in prices and (lc := self.snap.last_close(s)):
                    prices[s] = lc[1]
            spy = self.snap.latest.get(BENCHMARK, (None,))[0]
            return prices, spy, f"IEX last trades, {V.when(self.now)}"
        last = self.cal.latest_completed_session(self.now)
        prices = {s: lc[1] for s in held if (lc := self.snap.last_close(s, last))}
        spy = self.snap.last_close(BENCHMARK, last)
        return prices, spy[1] if spy else None, f"closes of {V.day(last)}"

    def _spy_price(self, at: datetime) -> float | None:
        """The SPY price the benchmark trades a flow at: that session's close once final, else the latest trade."""
        d = at.astimezone(ET).date()
        today = self.now.astimezone(ET).date()
        final = d < today or (self.cal.is_session(d) and self.now >= self.cal.session(d).close_utc + SETTLE)
        if final and (c := self.snap.close_on(BENCHMARK, d)) is not None:
            return c
        if d >= today and BENCHMARK in self.snap.latest:
            return self.snap.latest[BENCHMARK][0]
        lc = self.snap.last_close(BENCHMARK, d)
        return lc[1] if lc else None

    # ---- the owner's messages ---------------------------------------------------------------------------------------
    def _reply(self, m: dict, text: str, rec: dict) -> None:
        self._post(text, rec | {"ref": str(m["id"])} | ({"line": self._line} if self._line else {}),
                   reply_to=str(m["id"]))
        self.report.answered += 1

    def _answer(self, m: dict, cmd: Command | CommandError) -> None:
        if isinstance(cmd, CommandError):
            text = "I only read text messages. Type `help`." if cmd.code == "EMPTY" else f"❓ {cmd}"
            self._reply(m, text, {"k": "ack", "err": cmd.code})
            return
        if self.broken is not None and cmd.verb not in ("undo", "help", "trades"):
            self._reply(m, f"⚠️ Your records don't add up: {self.broken} Reply `undo` first.", {"k": "ack",
                                                                                                 "err": "BROKEN"})
            return
        getattr(self, f"_cmd_{cmd.verb}", self._money)(m, cmd)

    def _cmd_help(self, m: dict, cmd: Command) -> None:
        self._reply(m, V.help_text(self.spec, self.status), {"k": "ack"})

    def _cmd_trades(self, m: dict, cmd: Command) -> None:
        self._reply(m, V.trades_text(self.book), {"k": "ack"})

    def _cmd_status(self, m: dict, cmd: Command) -> None:
        if self.book.started_at is None:
            self._reply(m, "The account hasn't started yet: send `start 1000` once the cash is in.", {"k": "ack"})
            return
        prices, spy, as_of = self._prices_now()
        v = V.valuation(self.book, prices, as_of, spy, self.snap.spy_dividends())
        lines = [V.account_text(v, title="📒 **Status**")]
        if left := self._pending_latest():
            lines.append(V.pending_text(*left, self.spec))
        lines.append(f"Next check: after the close of {V.day(self._next_decision())}.")
        self._reply(m, "\n".join(lines), {"k": "ack"})

    def _cmd_plan(self, m: dict, cmd: Command) -> None:
        last = self._final_session() or self.cal.latest_completed_session(self.now - SETTLE)
        dec = decide(self.spec, self.snap.closes(), last, self._held(), float(self.book.cash))
        if not self._complete(dec):
            self._reply(m, "I don't have enough prices for the method right now; try again later.", {"k": "ack"})
            return
        orders = V.orders_payload(dec.orders)
        when = ("it acts when the signal flips, and sizes idle cash at month-end" if self.spec.daily else
                "it trades only at month-end")
        body = [f"🧭 If the month ended on the close of {V.day(last)} (`{self.spec.method_id}`; {when}, next month-end "
                f"after the close of {V.day(self._next_decision())}):"]
        body += [f"- {V.order_line(o, self.spec, None, dec.ranks)}" for o in orders] or ["- no trades"]
        body.append(self._basis(dec))
        self._reply(m, "\n".join(body), {"k": "ack"})

    def _cmd_undo(self, m: dict, cmd: Command) -> None:
        target = self.book.last_undoable((int(m["id"]), self._line))
        if target is None:
            self._reply(m, "Nothing to undo.", {"k": "ack", "err": "NOTHING"})
            return
        self._reply(m, f"↩️ Removed: {V.describe(target)}.", {"k": "void", "target": target.msg_id})

    def _cmd_skip(self, m: dict, cmd: Command) -> None:
        alerts = [r for r in self.book.of("alert") if r.origin < (int(m["id"]), self._line)]
        if not alerts:
            self._reply(m, "There is no alert to skip yet.", {"k": "ack", "err": "NO_ALERT"})
            return
        alert = alerts[-1]
        left = pending(self.book, alert, self.cal.session(date.fromisoformat(alert.data["day"])).close_utc)
        if not left:
            self._reply(m, f"No open orders in the {V.day(date.fromisoformat(alert.data['day']))} alert.",
                        {"k": "ack", "err": "NO_ORDER"})
            return
        if cmd.symbol and not any(o["s"] == cmd.symbol for o in left):
            self._reply(m, f"No open {cmd.symbol} order in the {V.day(date.fromisoformat(alert.data['day']))} alert.",
                        {"k": "ack", "err": "NO_ORDER"})
            return
        what = cmd.symbol or "the remaining orders"
        self._reply(m, f"👍 Skipped {what} from the {V.day(date.fromisoformat(alert.data['day']))} alert. The next "
                       "month-end check starts from what you actually hold.",
                    {"k": "skip", "sym": cmd.symbol, "alert": alert.data["day"]})

    def _money(self, m: dict, cmd: Command) -> None:
        sent_at = parse_at(m["timestamp"])
        at = cmd.at or sent_at
        rec: dict = {"k": cmd.verb, "at": iso(at)}
        notes: list[str] = []
        if cmd.verb in ("start", "deposit", "withdraw"):
            spy = self._spy_price(at)
            if spy is None:  # not answered: tried again at the next run
                self._note("no-spy", "⚠️ I couldn't get SPY's price for the comparison; I'll record your cash at the "
                           "next run.")
                return
            rec |= {"amt": str(cmd.amount), "spy": f"{spy:.4f}"}
        elif cmd.verb in ("buy", "sell"):
            rec |= {"sym": cmd.symbol, "n": cmd.shares, "px": str(cmd.price)}
            notes += self._price_notes(cmd, at)
        elif cmd.verb == "dividend":
            rec |= {"sym": cmd.symbol, "amt": str(cmd.amount)}
        elif cmd.verb == "split":
            assert cmd.ratio is not None
            rec |= {"sym": cmd.symbol, "new": cmd.ratio[0], "old": cmd.ratio[1]}
        try:
            after = build([*self.records, Record(str(self.max_id + 1), rec | {"ref": str(m["id"]), "line": self._line})])
        except BookError as exc:
            self._reply(m, f"❌ Not recorded: {exc}", {"k": "ack", "err": exc.code})
            return
        if cmd.verb in ("buy", "sell") and cmd.at is None and not self.cal.is_open(at):
            notes.append("Recorded at your message's time (the market was closed); if the trade was earlier, `undo` "
                         "and add `on 10/30 3:55pm`.")
        if cmd.symbol and cmd.verb in ("buy", "sell") and cmd.symbol not in self.spec.universe:
            notes.append(f"{cmd.symbol} isn't one of the method's ETFs: I track it, the alerts ignore it.")
        if after.cash < 0:
            notes.append(f"Cash is now {V.usd(after.cash)}: if you added money, record it with `deposit`.")
        self._reply(m, "\n".join([self._confirm(cmd, at, rec, after), *(f"⚠️ {n}" for n in notes)]), rec)

    def _confirm(self, cmd: Command, at: datetime, rec: dict, after: Book) -> str:
        when = V.when(at)
        if cmd.verb == "start":
            return (f"✅ Started with {V.usd(Decimal(rec['amt']))} ({when}). The comparison buys the same cash of SPY "
                    f"at ${V.pfmt(Decimal(rec['spy']).quantize(V.CENT))}. Your starting allocation comes next "
                    "(within the hour).")
        if cmd.verb in ("deposit", "withdraw"):
            return (f"✅ {cmd.verb.capitalize()} of {V.usd(Decimal(rec['amt']))} recorded ({when}); the SPY comparison "
                    f"gets the same flow at ${V.pfmt(Decimal(rec['spy']).quantize(V.CENT))}. Cash "
                    f"{V.usd(after.cash)}."
                    + (f" It goes to work at the month-end check after {V.day(self._next_decision())} (`plan` shows "
                       "how)." if cmd.verb == "deposit" else ""))
        if cmd.verb == "dividend":
            return f"✅ Dividend of {V.usd(Decimal(rec['amt']))} from {cmd.symbol} recorded. Cash {V.usd(after.cash)}."
        h = after.holdings[cmd.symbol]  # type: ignore[index]
        if cmd.verb == "split":
            return (f"✅ Split {cmd.symbol} {rec['new']}:{rec['old']} recorded: {h.shares} sh, avg "
                    f"${V.pfmt(h.avg_cost.quantize(V.CENT))}.")
        total = Decimal(rec["px"]) * rec["n"]
        verb = "bought" if cmd.verb == "buy" else "sold"
        tail = (f"{cmd.symbol} now {h.shares} sh, avg ${V.pfmt(h.avg_cost.quantize(V.CENT))}" if h.shares else
                f"{cmd.symbol} closed")
        if cmd.verb == "sell":
            tail += f" · realized on {cmd.symbol} {V.susd(h.realized)}"
        return (f"✅ Recorded: {verb} {rec['n']} {cmd.symbol} at ${V.pfmt(rec['px'])} = {V.usd(total)} ({when}). "
                f"{tail} · cash {V.usd(after.cash)}")

    def _price_notes(self, cmd: Command, at: datetime) -> list[str]:
        d = at.astimezone(ET).date()
        px = float(cmd.price)  # type: ignore[arg-type]
        rng = self.snap.range_on(cmd.symbol, d)  # type: ignore[arg-type]
        if rng:
            lo, hi = rng
            if lo * 0.98 <= px <= hi * 1.02:
                return []
            where = f"{cmd.symbol}'s range that day (${V.pfmt(round(lo, 2))}–${V.pfmt(round(hi, 2))})"
        else:
            ref = self.snap.latest.get(cmd.symbol, (None,))[0] or (self.snap.last_close(cmd.symbol, d) or (None, None))[1]
            if ref is None:
                return [f"I have no price for {cmd.symbol} to check this against."]
            if abs(px / ref - 1) <= 0.08:
                return []
            where = f"{cmd.symbol}'s recent price (about ${V.pfmt(round(ref, 2))})"
        return [f"${V.pfmt(cmd.price)} is far from {where}: if it's a typo, `undo` and send it again."]  # type: ignore[arg-type]

    # ---- scheduled posts --------------------------------------------------------------------------------------------
    def _scheduled(self) -> None:
        last = self._final_session()
        if last is None:
            self.report.waiting_for_bars = True
        else:
            if (self.cfg.daily_summary and not self.book.has("summary", last.isoformat())
                    and self.book.started_at < self.cal.session(last).close_utc):  # type: ignore[operator]
                self._summary(last)
            if (due := self._decision_due(last)) is not None:
                self._alert(due)
        self._remind()
        self._split_notices()

    def _held(self) -> dict[str, int]:
        return {s: q for s, q in self.book.held().items() if s in self.spec.universe}

    def _decision_due(self, last: date) -> date | None:
        """The first decision after `start`; each month-end; and for a daily method, a close where the signal's asset
        differs from the latest alert's (a flip, not merely orders the owner has not reported yet)."""
        alerts = self.book.of("alert")
        if not alerts:
            return last
        prev = date.fromisoformat(alerts[-1].data["day"])
        due = last if is_decision_day(self.cal, last) else self.cal.previous_session(last.replace(day=1))
        if due > prev:
            return due
        if self.spec.daily and last > prev:
            dec = decide(self.spec, self.snap.closes(), last, self._held(), float(self.book.cash), rebalance=False)
            if self._complete(dec) and dec.target != alerts[-1].data.get("sel"):
                return last
        return None

    def _next_decision(self) -> date:
        today = self.now.astimezone(ET).date()
        nxt = month_end(self.cal, today)
        if self.book.has("alert", nxt.isoformat()) or self.now >= self.cal.session(nxt).close_utc + SETTLE:
            nxt = month_end(self.cal, nxt + timedelta(days=7))
        return nxt

    def _pending_latest(self) -> tuple[Record, list[dict]] | None:
        alert = self.book.latest("alert")
        if alert is None:
            return None
        left = pending(self.book, alert, self.cal.session(date.fromisoformat(alert.data["day"])).close_utc)
        return (alert, left) if left else None

    def _summary(self, last: date) -> None:
        held = list(self.book.held())
        prices = {s: c for s in held if (c := self.snap.close_on(s, last)) is not None}
        for s in held:
            if s not in prices and (lc := self.snap.last_close(s, last)):
                prices[s] = lc[1]
        v = V.valuation(self.book, prices, f"closes of {V.day(last)}", self.snap.close_on(BENCHMARK, last),
                        self.snap.spy_dividends())
        lines = [V.account_text(v, title=f"📊 **{V.day(last)} close**")]
        if left := self._pending_latest():
            lines.append(V.pending_text(*left, self.spec))
        self._post("\n".join(lines), {"k": "summary", "day": last.isoformat()})
        self.report.summary = True

    def _alert(self, due: date) -> None:
        held = self._held()
        first = not self.book.of("alert")
        month_end_due = is_decision_day(self.cal, due)
        dec = decide(self.spec, self.snap.closes(), due, held, float(self.book.cash), rebalance=first or month_end_due)
        if not self._complete(dec):
            self._note("no-signals", f"⚠️ The {V.day(due)} decision is waiting: the market data is incomplete. I'll "
                       "try again at the next run.")
            return
        today = self.now.astimezone(ET).date()
        trade_day = self.cal.next_session(due)
        if first and self.cal.is_session(today) and self.now < self.cal.session(today).close_utc:
            trade_day = today
        open_now = self.cal.is_open(self.now)
        late = not first and self.now >= self.cal.session(trade_day).open_utc
        trade_when = ("Trade today, near the current price" if open_now and trade_day == today
                      else f"Trade at or near the open of {V.day(trade_day)}")
        orders = V.orders_payload(dec.orders)
        closes = {s: c for s in dec.target if (c := self.snap.close_on(s, due)) is not None}
        if first:
            head = "📣 **Starting allocation**"
        elif month_end_due:
            head = "📣 **Month-end rebalance**" if orders else "📣 **Month-end check**"
        else:
            head = "📣 **Signal change**"
        text = V.alert_text(spec=self.spec, head=head, status=self.status, decided=due, trade_when=trade_when,
                            orders=orders, target=dec.target, held=set(held), basis=self._basis(dec),
                            equity=dec.equity, cash_after=dec.cash_after, closes=closes, ranks=dec.ranks, late=late,
                            notes=dec.notes)
        self._post(text, {"k": "alert", "day": due.isoformat(), "m": self.spec.method_id, "orders": orders,
                          "eq": f"{dec.equity:.2f}", "cash": f"{dec.cash_after:.2f}", "sel": dec.target,
                          "posted": iso(self.now)})
        self.report.alert = True

    def _remind(self) -> None:
        alert = self.book.latest("alert")
        if alert is None or self.book.has("remind", alert.data["day"]):
            return
        decided = date.fromisoformat(alert.data["day"])
        trade_day = self.cal.next_session(decided)
        sess = self.cal.session(trade_day)
        posted = parse_at(alert.data["posted"]) if alert.data.get("posted") else None
        if (self.now.astimezone(ET).date() != trade_day or not sess.open_utc - timedelta(hours=3) <= self.now
                < sess.open_utc + timedelta(hours=1) or (posted and posted.astimezone(ET).date() == trade_day)):
            return
        left = pending(self.book, alert, self.cal.session(decided).close_utc)
        if left:
            self._post(V.reminder_text(alert, left, self.spec, trade_day), {"k": "remind", "day": alert.data["day"]})
            self.report.reminder = True

    def _split_notices(self) -> None:
        for sp in self.snap.splits():
            h = self.book.holdings.get(sp["symbol"])
            if not h or not h.shares or not h.since or sp["ex_date"] < h.since.astimezone(ET).date().isoformat():
                continue
            if any(r.data.get("sym") == sp["symbol"] and (r.at and r.at.astimezone(ET).date().isoformat()
                                                         >= sp["ex_date"]) for r in self.book.of("split")):
                continue
            new, old = (int(x) if float(x).is_integer() else x for x in (sp["new"], sp["old"]))
            self._note("split", f"ℹ️ The market data shows a {new}:{old} split of {sp['symbol']} on {sp['ex_date']}. "
                       f"Once your broker shows the new share count, reply `split {sp['symbol']} {new}:{old} on "
                       f"{sp['ex_date']}`.", day=sp["ex_date"], sym=sp["symbol"])


__all__ = ["HISTORY_DAYS", "METHOD_STATUS", "PERSONAL_METHOD", "SETTLE", "Config", "MarketUnavailable", "RunReport",
           "Runner", "month_end"]
