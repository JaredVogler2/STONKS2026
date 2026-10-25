"""Point-in-time SEC event history for research (design-study-2, docs/evidence/DESIGN_STUDY_2.md).

Two event types for the U1 universe, from free public SEC data:
* earnings releases: 8-K filings listing item 2.02 ("Results of Operations and Financial Condition"), from the EDGAR
  submissions API (data.sec.gov/submissions/CIK##########.json and the older pages it lists under `filings.files`),
  parsed by catalysts.sec;
* insider open-market purchases: Form 4 non-derivative transactions coded P by a director or an officer of the issuer,
  from the SEC's quarterly Insider Transactions Data Sets (Forms 3, 4 and 5: one zip a quarter, filings by filing date).

Point in time (the first session whose close-time decision may use an event):
* an earnings release: the first session whose close is at or after its EDGAR acceptance
  (catalysts.earnings.first_usable_session); an after-hours release counts from the next session;
* an insider purchase: the first session AFTER its filing date. A Form 3, 4 or 5 submitted by 10 pm Eastern is dated
  that business day (Regulation S-T rule 13(a)(4)), so it may arrive after the close, and the data sets carry no
  acceptance time.

Access: the SEC's fair-access policy requires a descriptive User-Agent with a contact email (`APEX_SEC_USER_AGENT`;
without one the run stops, it never falls back) and at most 10 requests a second (this module uses
REQUESTS_PER_SECOND). The contact is sent to the SEC only and is never printed or written. Research only: nothing here
reads a price, and only aggregate counts (`SecHistory.summary`) leave the runner.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import numpy as np

from apex_worker.catalysts.earnings import first_usable_session
from apex_worker.catalysts.http import (
    FetchError,
    HttpClientConfig,
    PoliteHttpClient,
    TokenBucket,
    UserAgentError,
    validate_sec_user_agent,
)
from apex_worker.catalysts.sec import (
    COMPANY_TICKERS_URL,
    SUBMISSIONS_URL,
    FilingMeta,
    SubmissionsParseError,
    parse_submissions,
)
from apex_worker.catalysts.tickers import TickerMap, normalize_cik
from apex_worker.market.calendar import MarketCalendar
from apex_worker.research.realeq.data import RunStop
from apex_worker.timeutil import ET

HISTORY_START = date(2018, 1, 1)  # earnings-release history for the cadence estimate before the first DEV decision
REQUESTS_PER_SECOND = 5.0  # half the SEC's fair-access cap
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
# The SEC moved the newest quarters (2026 Q2 on) to a second path; both are tried, in this order.
FORM345_URLS = ("https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/"
                "{year}q{quarter}_form345.zip",
                "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/"
                "{year}q{quarter}_form345.zip")
FORM345_MAX_BYTES = 200 * 1024 * 1024  # a quarter is about 8 MB (2026 Q3)
STATUS_SEC_BLOCKED = "BLOCKED_SEC_USER_AGENT_MISSING"
STATUS_SEC_INCOMPLETE = "SEC_DATA_INCOMPLETE"
STATUS_SEC_SCHEMA = "SEC_DATA_SCHEMA_CHANGED"
_PAGE_RE = re.compile(r"^CIK\d{10}-submissions-\d{3}\.json$")
_INSIDER_ROLES = ("director", "officer")

# The registered event definitions (recorded in the design study's plan; any change needs a new registration).
EVENT_RULES: dict[str, str] = {
    "earnings_release": "an 8-K (not 8-K/A) listing item 2.02; one release per company and US Eastern acceptance day "
                        "(the earliest acceptance); usable from the first session whose close is at or after the "
                        "acceptance",
    "insider_purchase": "a Form 4 (not 4/A) with a non-derivative transaction coded P (open market or private "
                        "purchase), acquired (A), shares > 0, filed by a reporting owner who is a director or an "
                        "officer of the issuer; one event per filing and qualifying owner; usable from the first "
                        "session after the filing date",
    "issuer_mapping": "U1 symbol -> CIK from the SEC's current company_tickers.json (U1 is today's list); events are "
                      "keyed by issuer CIK; a symbol without a CIK has no events",
}
FORM345_COLUMNS = {
    "SUBMISSION.tsv": ("ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK"),
    "NONDERIV_TRANS.tsv": ("ACCESSION_NUMBER", "TRANS_CODE", "TRANS_ACQUIRED_DISP_CD", "TRANS_SHARES",
                           "TRANS_PRICEPERSHARE"),
    "REPORTINGOWNER.tsv": ("ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP"),
}


@dataclass(frozen=True)
class Release:
    """An earnings release (8-K item 2.02)."""

    cik: str
    day: date  # the US Eastern calendar day of the acceptance
    accepted_at: datetime  # UTC
    session: date  # the first session whose close-time decision may use it


@dataclass(frozen=True)
class Purchase:
    """An insider open-market purchase (Form 4, code P, director or officer)."""

    cik: str  # the issuer's
    owner: str  # the reporting owner's CIK
    filing_date: date
    session: date  # the first session after the filing date
    shares: float
    value: float | None  # shares x price where every coded-P line has a price


@dataclass
class SecHistory:
    releases: dict[str, list[Release]]  # symbol -> releases sorted by acceptance
    purchases: dict[str, list[Purchase]]  # symbol -> purchases sorted by session
    symbols: list[str]
    ciks: dict[str, str]  # symbol -> CIK (mapped symbols only)
    start: date
    end: date
    stats: dict = field(default_factory=dict)

    def summary(self) -> dict:
        """Aggregate counts only: no symbol, CIK, owner or value per stock."""
        rel = [r for rs in self.releases.values() for r in rs]
        pur = [p for ps in self.purchases.values() for p in ps]
        years = range(self.start.year, self.end.year + 1)
        return {"window": [self.start.isoformat(), self.end.isoformat()], "rules": EVENT_RULES,
                "symbols": len(self.symbols), "symbols_with_cik": len(self.ciks),
                "symbols_without_cik": len(self.symbols) - len(self.ciks),
                "earnings_releases": len(rel), "insider_purchases": len(pur),
                "symbols_with_releases": sum(1 for rs in self.releases.values() if rs),
                "symbols_with_purchases": sum(1 for ps in self.purchases.values() if ps),
                "releases_by_year": {str(y): sum(1 for r in rel if r.day.year == y) for y in years},
                "purchases_by_year": {str(y): sum(1 for p in pur if p.filing_date.year == y) for y in years},
                "after_close_releases": sum(1 for r in rel if r.session > r.day),
                **{k: v for k, v in self.stats.items() if not isinstance(v, dict | list)}}


# ---- access ---------------------------------------------------------------------------------------------------------
APP_NAME = "APEX Research"  # the application's own name, declared when the owner's secret is only an email
_BARE_EMAIL_RE = re.compile(r"<?\s*([A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})\s*>?")
_ANY_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_GENERIC_START = ("python-httpx", "python-requests", "curl", "wget", "mozilla", "java", "go-http-client")


def _contact_problem(raw: str) -> str:
    """Which rule the contact breaks, in words that never contain the value itself."""
    if not raw:
        return "is not set (the repository secret is missing or empty)"
    if not raw.isascii() or not raw.isprintable():
        return ("contains a character an HTTP header cannot carry (an accented letter, a curly quote or another "
                "non-ASCII character); use plain letters, digits and the email")
    if not _ANY_EMAIL_RE.search(raw):
        return "contains no email address that the SEC could write to (expected name@domain.tld)"
    name = _ANY_EMAIL_RE.sub("", raw).strip(" <>()[],;:-\t")
    if name.lower().startswith(_GENERIC_START):
        return "starts with a generic client name (curl, Mozilla, python-requests ...)"
    return "needs a name of at least 3 characters before the email"


def user_agent_from_env(env: Mapping[str, str] | None = None) -> str:
    """The owner's SEC contact (`APEX_SEC_USER_AGENT`), validated and sent as the User-Agent header. Any run of
    whitespace, a line break included, becomes one space (a header cannot carry a line break); a secret holding only an
    email gets this application's name in front (`APEX Research you@example.com`), which is what the SEC asks for. The
    value is never echoed: the validator's own message quotes it, so the stop says which rule failed instead."""
    env = os.environ if env is None else env
    raw = " ".join((env.get("APEX_SEC_USER_AGENT") or "").split())  # a line break or tab cannot travel in a header
    if (m := _BARE_EMAIL_RE.fullmatch(raw)) is not None:
        raw = f"{APP_NAME} {m.group(1)}"
    try:
        if not raw.isascii() or not raw.isprintable():
            raise UserAgentError("not a header value")
        return validate_sec_user_agent(raw)
    except UserAgentError:
        raise RunStop(STATUS_SEC_BLOCKED,
                      f"BLOCKED — APEX_SEC_USER_AGENT {_contact_problem(raw)}. Set the repository secret "
                      "APEX_SEC_USER_AGENT to a name and a contact email, e.g. 'APEX Research you@example.com' (the "
                      "SEC's fair-access policy requires it; see docs/evidence/DESIGN_STUDY_2.md).") from None


def sec_http(user_agent: str, *, transport=None, bucket: TokenBucket | None = None,
             rps: float = REQUESTS_PER_SECOND) -> PoliteHttpClient:
    cfg = HttpClientConfig.for_sec(user_agent, requests_per_second=rps, max_bytes=FORM345_MAX_BYTES,
                                   timeout_seconds=120.0)
    return PoliteHttpClient(cfg, transport=transport, bucket=bucket)


def _get(http: PoliteHttpClient, url: str, what: str, *, accept: str | None = None,
         missing_ok: bool = False) -> bytes | None:
    try:
        return http.get(url, accept=accept).content
    except FetchError as exc:
        if missing_ok and exc.status == 404:
            return None
        cause = type(exc.__cause__).__name__ if exc.__cause__ is not None else exc.status  # never the message: it may
        raise RunStop(STATUS_SEC_INCOMPLETE, f"SEC {what}: {exc.reason_code} ({cause})") from None  # quote headers


# ---- earnings releases ----------------------------------------------------------------------------------------------
def parse_page(data: bytes | str | Mapping[str, Any], cik: str, name: str = "") -> list[FilingMeta]:
    """An older submissions page (`filings.files[*].name`): the same column arrays as `filings.recent`, at top level."""
    page = json.loads(data) if isinstance(data, bytes | str) else data
    return list(parse_submissions({"cik": cik, "name": name, "filings": {"recent": page}}).filings)


def company_filings(http: PoliteHttpClient, cik: str, start: date, end: date) -> tuple[list[FilingMeta], int]:
    """Every filing of `cik` dated within [start, end]: the recent block plus each older page that overlaps the
    window. Returns (filings, requests)."""
    raw = _get(http, SUBMISSIONS_URL.format(cik=normalize_cik(cik)), f"submissions of CIK {cik}",
               accept="application/json")
    try:
        doc = json.loads(raw)
        parsed = parse_submissions(doc)
    except (ValueError, SubmissionsParseError) as exc:
        raise RunStop(STATUS_SEC_SCHEMA, f"SEC submissions of CIK {cik} do not parse: {exc}") from None
    filings, requests = list(parsed.filings), 1
    for f in (doc.get("filings") or {}).get("files") or []:
        name = str(f.get("name") or "")
        if not _PAGE_RE.match(name):
            raise RunStop(STATUS_SEC_SCHEMA, f"SEC submissions page name {name!r} is not the expected form")
        lo, hi = date.fromisoformat(f["filingFrom"]), date.fromisoformat(f["filingTo"])
        if hi < start or lo > end:
            continue
        page = _get(http, SUBMISSIONS_PAGE_URL.format(name=name), f"submissions page {name}",
                    accept="application/json")
        requests += 1
        try:
            filings += parse_page(page, cik, parsed.name)
        except (ValueError, SubmissionsParseError) as exc:
            raise RunStop(STATUS_SEC_SCHEMA, f"SEC submissions page {name} does not parse: {exc}") from None
    return [f for f in filings if start <= f.filing_date <= end], requests


def releases_from_filings(filings: Iterable[FilingMeta], cal: MarketCalendar) -> list[Release]:
    """EVENT_RULES['earnings_release']: 8-K (not 8-K/A) with item 2.02, one per US Eastern acceptance day."""
    first: dict[date, FilingMeta] = {}
    for f in filings:
        if f.base_form != "8-K" or f.is_amendment or "2.02" not in f.items:
            continue
        day = f.published_at.astimezone(ET).date()
        if day not in first or f.published_at < first[day].published_at:
            first[day] = f
    out = [Release(f.cik, day, f.published_at, first_usable_session(f.published_at, cal))
           for day, f in first.items()]
    return sorted(out, key=lambda r: r.accepted_at)


# ---- insider purchases ----------------------------------------------------------------------------------------------
def quarters(start: date, end: date) -> list[tuple[int, int]]:
    out, y, q = [], start.year, (start.month - 1) // 3 + 1
    while (y, q) <= (end.year, (end.month - 1) // 3 + 1):
        out.append((y, q))
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def _filing_date(raw: str) -> date:
    raw = raw.strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC).date()  # a calendar date, no time
        except ValueError:
            continue
    raise ValueError(f"unrecognised filing date {raw!r}")


def _float(raw: str) -> float | None:
    try:
        x = float(raw.strip().replace(",", ""))
    except ValueError:
        return None
    return x if np.isfinite(x) else None


def _rows(zf: zipfile.ZipFile, member: str, stats: dict) -> Iterator[dict[str, str]]:
    """Stream one table of a Form 3/4/5 data set as dicts of the registered columns. A line with the wrong number of
    fields is skipped and counted (`malformed_lines`); a missing table or column stops the run (schema changed)."""
    stem = member.rsplit(".", 1)[0].upper()  # matched by table name, whatever the extension
    names = {n.rsplit("/", 1)[-1].rsplit(".", 1)[0].upper(): n for n in zf.namelist() if not n.endswith("/")}
    if stem not in names:
        raise RunStop(STATUS_SEC_SCHEMA, f"Form 3/4/5 data set lacks {member}")
    with zf.open(names[stem]) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline="")
        header = text.readline().rstrip("\r\n").split("\t")
        cols = [c.strip().upper() for c in header]
        need = FORM345_COLUMNS[member]
        missing = [c for c in need if c not in cols]
        if missing:
            raise RunStop(STATUS_SEC_SCHEMA, f"Form 3/4/5 {member} lacks columns {missing}")
        pos = [cols.index(c) for c in need]
        for line in text:
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) != len(cols):
                stats["malformed_lines"] = stats.get("malformed_lines", 0) + 1
                continue
            yield {c: parts[i] for c, i in zip(need, pos, strict=True)}


def parse_form345(content: bytes, issuer_ciks: Iterable[str], cal: MarketCalendar,
                  stats: dict | None = None) -> list[Purchase]:
    """EVENT_RULES['insider_purchase'] for one quarterly data set, restricted to `issuer_ciks`."""
    stats = {} if stats is None else stats
    wanted = {normalize_cik(c) for c in issuer_ciks}
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise RunStop(STATUS_SEC_SCHEMA, "a Form 3/4/5 data set is not a zip file") from None
    with zf:
        subs: dict[str, tuple[str, date]] = {}
        for r in _rows(zf, "SUBMISSION.tsv", stats):
            if r["DOCUMENT_TYPE"].strip() != "4" or not r["ISSUERCIK"].strip():
                continue
            cik = normalize_cik(r["ISSUERCIK"])
            if cik in wanted:
                subs[r["ACCESSION_NUMBER"].strip()] = (cik, _filing_date(r["FILING_DATE"]))
        buys: dict[str, list[tuple[float, float | None]]] = {}
        for r in _rows(zf, "NONDERIV_TRANS.tsv", stats):
            acc = r["ACCESSION_NUMBER"].strip()
            if acc not in subs or r["TRANS_CODE"].strip().upper() != "P":
                continue
            if r["TRANS_ACQUIRED_DISP_CD"].strip().upper() != "A":
                continue
            shares = _float(r["TRANS_SHARES"])
            if shares is None or shares <= 0:
                continue
            buys.setdefault(acc, []).append((shares, _float(r["TRANS_PRICEPERSHARE"])))
        owners: dict[str, set[str]] = {}
        for r in _rows(zf, "REPORTINGOWNER.tsv", stats):
            acc = r["ACCESSION_NUMBER"].strip()
            if acc not in buys:
                continue
            rel = r["RPTOWNER_RELATIONSHIP"].lower()
            if any(role in rel for role in _INSIDER_ROLES) and r["RPTOWNERCIK"].strip():
                owners.setdefault(acc, set()).add(normalize_cik(r["RPTOWNERCIK"]))
    out = []
    for acc, lines in buys.items():
        cik, filed = subs[acc]
        shares = float(sum(s for s, _p in lines))
        value = (float(sum(s * p for s, p in lines)) if all(p is not None for _s, p in lines) else None)
        session = cal.next_session(filed)
        for owner in sorted(owners.get(acc, ())):
            out.append(Purchase(cik, owner, filed, session, shares, value))
    stats["form4_filings_in_universe"] = stats.get("form4_filings_in_universe", 0) + len(subs)
    stats["coded_p_filings"] = stats.get("coded_p_filings", 0) + len(buys)
    stats["coded_p_filings_without_director_or_officer"] = (
        stats.get("coded_p_filings_without_director_or_officer", 0) + sum(1 for a in buys if not owners.get(a)))
    return out


# ---- the history ----------------------------------------------------------------------------------------------------
def load_sec_history(symbols: Iterable[str], *, start: date, end: date, cal: MarketCalendar, user_agent: str | None = None,
                     http: PoliteHttpClient | None = None, progress: Callable[[str], None] = print) -> SecHistory:
    """Earnings releases and insider purchases of `symbols` dated within [start, end]. Every quarter of the window must
    have its Form 3/4/5 data set; a missing one stops the run (SEC_DATA_INCOMPLETE) rather than leaving a gap that
    would read as "no insider bought"."""
    symbols = sorted(set(symbols))
    http = http or sec_http(user_agent or user_agent_from_env())
    stats: dict[str, Any] = {"requests": 1}
    tmap = TickerMap.load(_get(http, COMPANY_TICKERS_URL, "company_tickers.json", accept="application/json"))
    ciks = {s: c for s in symbols if (c := tmap.cik_for(s)) is not None}
    by_cik: dict[str, list[str]] = {}
    for s, c in ciks.items():
        by_cik.setdefault(c, []).append(s)
    releases: dict[str, list[Release]] = {s: [] for s in symbols}
    for i, (cik, syms) in enumerate(sorted(by_cik.items())):
        filings, n = company_filings(http, cik, start, end)
        stats["requests"] += n
        rel = releases_from_filings(filings, cal)
        for s in syms:
            releases[s] = rel
        if (i + 1) % 100 == 0:
            progress(f"SEC submissions: {i + 1} of {len(by_cik)} companies")
    purchases: dict[str, list[Purchase]] = {s: [] for s in symbols}
    missing = []
    for y, q in quarters(start, end):
        raw = None
        for url in FORM345_URLS:
            raw = _get(http, url.format(year=y, quarter=q), f"Form 3/4/5 data set {y}q{q}", missing_ok=True)
            stats["requests"] += 1
            if raw is not None:
                break
        if raw is None:
            missing.append(f"{y}q{q}")
            continue
        for p in parse_form345(raw, by_cik, cal, stats):
            if start <= p.filing_date <= end:
                for s in by_cik[p.cik]:
                    purchases[s].append(p)
        progress(f"SEC Form 3/4/5 data set {y}q{q} read")
    if missing:
        raise RunStop(STATUS_SEC_INCOMPLETE, f"Form 3/4/5 data sets not published (yet) for {', '.join(missing)}: the "
                                             "window needs every quarter")
    for ps in purchases.values():
        ps.sort(key=lambda p: (p.session, p.owner))
    stats["quarters"] = len(quarters(start, end))
    return SecHistory(releases, purchases, symbols, ciks, start, end, stats)


def synthetic_history(symbols: Iterable[str], sessions: Iterable[date], *, seed: int = 7) -> SecHistory:
    """DEMO fixture events (fictional, never real): a release about every 63 sessions per symbol at a random phase and
    timing, and a purchase on about 1 in 400 symbol-sessions. Fixture runs only."""
    symbols, days = sorted(set(symbols)), list(sessions)
    rng = np.random.default_rng(seed)
    releases: dict[str, list[Release]] = {}
    purchases: dict[str, list[Purchase]] = {}
    for k, s in enumerate(symbols):
        cik = f"{9990000000 + k:010d}"
        rel, i = [], int(rng.integers(0, 63))
        while i < len(days) - 1:
            after = bool(rng.random() < 0.5)
            hour = 16 if after else 8
            at = datetime(days[i].year, days[i].month, days[i].day, hour, 5, tzinfo=ET)
            rel.append(Release(cik, days[i], at.astimezone(UTC), days[i + 1] if after else days[i]))
            i += int(rng.integers(58, 68))
        releases[s] = rel
        buys = [Purchase(cik, f"{9991000000 + k:010d}", days[j], days[j + 1], 1000.0, None)
                for j in range(len(days) - 1) if rng.random() < 1 / 400]
        purchases[s] = buys
    return SecHistory(releases, purchases, symbols, {s: f"{9990000000 + k:010d}" for k, s in enumerate(symbols)},
                      days[0], days[-1], {"synthetic": True})
