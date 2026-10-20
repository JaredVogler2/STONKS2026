"""Keyword/regex catalyst taxonomy for headlines (deterministic; no LLM).

Directions are always *qualified*: a "beat" is relative to expectations APEX has not independently
sourced, a buyback is an authorization (not execution), M&A direction depends on acquirer vs target,
denials and rumours downgrade confidence. Unmatched headlines are ``news_other`` / ``unknown``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from apex_worker.catalysts.models import CatalystType, Direction

TAXONOMY_VERSION = "taxonomy/1.0.0"
_I = re.IGNORECASE
_T = CatalystType
_D = Direction


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    catalyst_type: CatalystType
    direction: Direction
    reason: str
    confidence: float


@dataclass(frozen=True)
class TaxonomyResult:
    catalyst_type: CatalystType
    direction: Direction
    reason_codes: tuple[str, ...]
    extraction_confidence: float
    rule: str


def _r(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, _I)


_GUIDE = r"(?:guidance|outlook|forecast|targets?)"
_EXEC = r"(?:CEO|CFO|COO|chief\s+\w+\s+officer|chair(?:man|woman)?|president|founder)"
RULES: tuple[Rule, ...] = (
    Rule(
        "guidance_cut",
        _r(
            r"\b(?:cuts?|cutting|lowers?|lowered|lowering|slash(?:es|ed)?|reduces?|reduced|trims?|trimmed"
            r"|withdraws?"
            r"|withdrew|withdrawn|suspends?|suspended)\b[^.;]{0,40}?\b" + _GUIDE + r"\b"
            r"|\b" + _GUIDE + r"\b[^.;]{0,20}\b(?:cut|lowered|below (?:estimates|expectations|consensus))\b"
            r"|\bprofit warning\b|\bwarns\b[^.;]{0,30}\b(?:profit|revenue|sales|earnings)\b"
        ),
        _T.GUIDANCE_CUT,
        _D.POTENTIALLY_NEGATIVE,
        "GUIDANCE_LOWERED",
        0.8,
    ),
    Rule(
        "guidance_raise",
        _r(
            r"\b(?:raises?|raised|raising|lifts?|lifted|boosts?|boosted|ups|upped|increases?|increased"
            r"|hikes?|hiked)\b"
            r"[^.;]{0,40}?\b" + _GUIDE + r"\b"
            r"|\b" + _GUIDE + r"\b[^.;]{0,20}\b(?:raised|above (?:estimates|expectations|consensus))\b"
        ),
        _T.GUIDANCE_RAISE,
        _D.POTENTIALLY_POSITIVE,
        "GUIDANCE_RAISED",
        0.8,
    ),
    Rule(
        "regulatory_rejection",
        _r(
            r"\bcomplete response letter\b|\bCRL\b|\bclinical hold\b"
            r"|\b(?:FDA|EMA)\b[^.;]{0,40}\b(?:rejects?|rejected|declines?|declined|refuses?|refused)\b"
        ),
        _T.REGULATORY_DECISION,
        _D.POTENTIALLY_NEGATIVE,
        "REGULATORY_REJECTION",
        0.85,
    ),
    Rule(
        "regulatory_approval",
        _r(
            r"\b(?:FDA|EMA)\b[^.;]{0,40}\b(?:approv\w*|clear(?:s|ed|ance)|grants?|granted|authori[sz]\w*)\b"
            r"|\b(?:receives?|wins?|gains?|secures?|obtains?)\b[^.;]{0,30}\b(?:FDA|EMA|regulatory"
            r"|marketing)\b"
            r"[^.;]{0,20}\b(?:approval|clearance|authori[sz]ation)\b"
        ),
        _T.REGULATORY_DECISION,
        _D.POTENTIALLY_POSITIVE,
        "REGULATORY_APPROVAL",
        0.85,
    ),
    Rule(
        "regulatory_process",
        _r(
            r"\b(?:FDA|EMA)\b[^.;]{0,40}\b(?:accepts?|accepted|priority review|advisory (?:committee|panel)"
            r"|PDUFA)\b"
        ),
        _T.REGULATORY_DECISION,
        _D.UNKNOWN,
        "REGULATORY_PROCESS_STEP",
        0.7,
    ),
    Rule(
        "merger_target",
        _r(
            r"\b(?:to be acquired|agrees? to be acquired|to be bought)\b"
            r"|\breceives?\b[^.;]{0,30}\b(?:takeover|buyout|acquisition)\s+(?:offer|bid|proposal)\b"
        ),
        _T.MERGER_ACQUISITION,
        _D.POTENTIALLY_POSITIVE,
        "TARGET_PREMIUM_POSSIBLE",
        0.75,
    ),
    Rule(
        "merger",
        _r(
            r"\b(?:acquires?|acquired|acquisition of|to acquire|agrees? to (?:buy|acquire)|merger|merge with"
            r"|merges"
            r"|takeover|buyout|tender offer|deal to buy|explor\w*\s+(?:a\s+)?(?:sale"
            r"|strategic alternatives))\b"
        ),
        _T.MERGER_ACQUISITION,
        _D.MIXED,
        "ROLE_ACQUIRER_OR_TARGET_UNRESOLVED",
        0.7,
    ),
    Rule(
        "buyback",
        _r(
            r"\b(?:buyback|buy-back|share repurchase|stock repurchase|repurchase (?:program|plan"
            r"|authori[sz]ation)"
            r"|repurchase up to|to repurchase)\b"
        ),
        _T.BUYBACK,
        _D.POTENTIALLY_POSITIVE,
        "BUYBACK_AUTHORIZATION_NOT_EXECUTION",
        0.8,
    ),
    Rule(
        "dilution",
        _r(
            r"\b(?:public offering|stock offering|share offering|secondary offering|follow-on offering"
            r"|equity offering"
            r"|at-the-market|ATM (?:program|offering)|registered direct|private placement"
            r"|convertible (?:senior )?notes? offering)\b"
        ),
        _T.DILUTION_OFFERING,
        _D.POTENTIALLY_NEGATIVE,
        "DILUTION_RISK",
        0.8,
    ),
    Rule(
        "contract_award",
        _r(
            r"\b(?:wins?|won|awarded|secures?|secured|lands?|landed|receives?|received|books?"
            r"|booked)\b[^.;]{0,60}?"
            r"\b(?:contract|order|task order|award)s?\b"
        ),
        _T.CONTRACT_AWARD,
        _D.POTENTIALLY_POSITIVE,
        "CONTRACT_WIN",
        0.75,
    ),
    Rule(
        "sector_shock",
        _r(r"\b(?:tariffs?|export (?:controls?|bans?|restrictions?)|embargo|industry-wide|sector-wide)\b"),
        _T.SECTOR_SHOCK,
        _D.MIXED,
        "SECTOR_LEVEL_EVENT",
        0.5,
    ),
    Rule(
        "regulatory_action",
        _r(
            r"\b(?:SEC charges|charged by|DOJ|Justice Department|FTC|antitrust|probe|investigation"
            r"|investigating"
            r"|subpoena\w*|lawsuit|sues|sued|class action|fined|penalt(?:y|ies)|recalls?|recalled"
            r"|warning letter"
            r"|indict\w*|settles|settlement|consent decree)\b"
        ),
        _T.REGULATORY_ACTION,
        _D.POTENTIALLY_NEGATIVE,
        "REGULATORY_OR_LEGAL_ACTION",
        0.7,
    ),
    Rule(
        "management_departure",
        _r(
            r"\b"
            + _EXEC
            + r"\b[^.;]{0,40}\b(?:resigns?|resigned|ousted|fired|terminated|steps? down|stepping down"
            r"|to step down|departs?|departing|exits?|leaves|leaving|retir\w*)\b"
        ),
        _T.MANAGEMENT_CHANGE,
        _D.UNKNOWN,
        "EXECUTIVE_DEPARTURE",
        0.75,
    ),
    Rule(
        "management_appointment",
        _r(
            r"\b(?:names?|named|appoints?|appointed|hires?|hired|promotes?|promoted)\b[^.;]{0,40}\b"
            + _EXEC
            + r"\b"
        ),
        _T.MANAGEMENT_CHANGE,
        _D.UNKNOWN,
        "EXECUTIVE_APPOINTMENT",
        0.7,
    ),
    Rule(
        "earnings",
        _r(
            r"\b(?:Q[1-4]|first[- ]quarter|second[- ]quarter|third[- ]quarter|fourth[- ]quarter|quarterly"
            r"|full[- ]year"
            r"|fiscal)\b[^.;]{0,40}\b(?:results|earnings|profit|revenue|sales|loss|EPS)\b"
            r"|\b(?:reports?|reported|posts?|posted)\b[^.;]{0,40}\b(?:results|earnings|profit|loss|revenue)\b"
            r"|\b(?:beats?|beating|tops?|topped|misses?|missed|exceeds?|exceeded)\b[^.;]{0,30}"
            r"\b(?:estimates|expectations|consensus|forecasts?)\b"
        ),
        _T.EARNINGS_RELEASE,
        _D.UNKNOWN,
        "EARNINGS_RESULTS",
        0.7,
    ),
)

_GOVERNMENT = _r(
    r"\b(?:department of|DoD|Pentagon|Army|Navy|Air Force|Marine Corps|Space Force|NASA|federal|government"
    r"|GSA"
    r"|DHS|Veterans Affairs)\b"
)
_NEGATION = _r(
    r"\b(?:denies|denied|deny|refutes?|refuted|no plans? to|not (?:planning|considering|in talks)|rules out"
    r"|ruled out"
    r"|rejects? (?:report|rumou?r|speculation))\b"
)
_SPECULATION = _r(
    r"\b(?:reportedly|rumou?r\w*|considering|explor(?:es|ing)|weighs|weighing|in talks|said to be"
    r"|sources say)\b"
)
_BEAT = _r(
    r"\b(?:beats?|beating|tops?|topped|exceeds?|exceeded|above)\b[^.;]{0,30}\b(?:estimates|expectations"
    r"|consensus)\b"
)
_MISS = _r(
    r"\b(?:misses?|missed|falls short|fell short|below)\b[^.;]{0,30}\b(?:estimates|expectations|consensus)\b"
)
_PRICE_DOWN = _r(
    r"\b(?:despite|shares (?:fall|fell|slide|slid|drop|dropped|sink|sank|tumble|tumbled|slump))\b"
)
_PRICE_UP = _r(r"\bshares (?:jump|jumped|soar|soared|surge|surged|rally|rallied|rise|rose)\b")
_UNPLANNED = _r(r"\b(?:effective immediately|abrupt\w*|unexpected\w*|ousted|fired|terminated|amid)\b")
_RETIRE = _r(r"\bretir\w*\b")
_SETTLE = _r(r"\b(?:settles|settlement)\b")


def classify_headline(headline: str) -> TaxonomyResult:
    text = headline or ""
    hits = [(rule, m.start()) for rule in RULES if (m := rule.pattern.search(text))]
    if not hits:
        return TaxonomyResult(_T.NEWS_OTHER, _D.UNKNOWN, ("UNCLASSIFIED_HEADLINE",), 0.3, "none")
    rule = hits[0][0]
    ctype, direction, conf = rule.catalyst_type, rule.direction, rule.confidence
    reasons = [rule.reason]
    names = {r.name for r, _ in hits}

    if rule.name in {"guidance_cut", "guidance_raise"} and {"guidance_cut", "guidance_raise"} <= names:
        first = min((h for h in hits if h[0].name in {"guidance_cut", "guidance_raise"}), key=lambda h: h[1])[
            0
        ]
        ctype, direction = first.catalyst_type, _D.MIXED
        reasons = ["GUIDANCE_MIXED"]
    elif rule.name == "contract_award" and _GOVERNMENT.search(text):
        ctype, conf = _T.GOVERNMENT_PROCUREMENT, 0.8
        reasons.append("GOVERNMENT_COUNTERPARTY")
    elif rule.name == "regulatory_action" and _SETTLE.search(text):
        direction = _D.MIXED
        reasons.append("SETTLEMENT_RESOLVES_UNCERTAINTY")
    elif rule.name == "management_departure":
        if _UNPLANNED.search(text):
            direction = _D.POTENTIALLY_NEGATIVE
            reasons.append("UNPLANNED_DEPARTURE_POSSIBLE")
        elif _RETIRE.search(text):
            reasons.append("PLANNED_SUCCESSION")
        else:
            reasons.append("DEPARTURE_CONTEXT_UNCLEAR")
    elif rule.name == "earnings":
        beat, miss = bool(_BEAT.search(text)), bool(_MISS.search(text))
        if beat and (miss or _PRICE_DOWN.search(text)):
            direction = _D.MIXED
            reasons.append("BEAT_BUT_NEGATIVE_CONTEXT")
        elif miss and _PRICE_UP.search(text):
            direction = _D.MIXED
            reasons.append("MISS_BUT_POSITIVE_CONTEXT")
        elif beat:
            direction = _D.POTENTIALLY_POSITIVE
        elif miss:
            direction = _D.POTENTIALLY_NEGATIVE
        reasons.append(
            "EXPECTATIONS_NOT_INDEPENDENTLY_SOURCED" if beat or miss else "SURPRISE_VS_EXPECTATIONS_UNKNOWN"
        )
    if _NEGATION.search(text):
        direction = _D.UNKNOWN
        conf -= 0.2
        reasons.append("NEGATION_OR_DENIAL")
    elif _SPECULATION.search(text):
        conf -= 0.15
        reasons.append("UNCONFIRMED_REPORT")
    secondary = [r.reason for r, _ in hits[1:] if r.reason not in reasons][:3]
    reasons.extend(f"ALSO_{code}"[:64] for code in secondary)
    return TaxonomyResult(ctype, direction, tuple(reasons), round(max(0.1, conf), 4), rule.name)


__all__ = ["RULES", "TAXONOMY_VERSION", "Rule", "TaxonomyResult", "classify_headline"]
