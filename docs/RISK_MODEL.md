# APEX risk model: how a setup becomes (or does not become) an alert

Every number below comes from code that runs the same way in live scanning and in backtests:
- `apps/pi-worker/src/apex_worker/signals/levels.py`
- `signals/quality.py`
- `signals/policy.py`
- `research/execution.py`
- `research/evaluate.py`

Versions are stamped on every signal: `policy-1+levels-1`, `quality-1`, `apex-features-1`.

## 1. Eligibility (hard vetoes)

A symbol is scanned only if it passes the universe filters in **Settings → Market rules**:
- price,
- median dollar volume and average share volume,
- history length,
- ETF / leveraged / OTC / warrant / preferred exclusions,
- tradability status.

Then each strategy's own rules are applied. A veto is final. It is recorded with a reason code (for example `LOW_DOLLAR_VOLUME`, `EARNINGS_WITHIN_HORIZON`, `OVEREXTENDED`) and the scan's most common veto is reported when the day ends in **NO TRADE**.

## 2. Quality (0–100): a ranking, never a probability

Quality ranks setups against each other. A 90 does **not** mean a 90 % chance of anything.

- **Six evidence groups**, each scored −1..1. Correlated measurements live in one group so they cannot pose as independent confirmations.

  | Group | Weight |
  |---|---|
  | price (trend slope, SMA distance, momentum) | 1.0 |
  | sector (relative strength vs sector and benchmark) | 0.8 |
  | volume | 0.8 |
  | volatility | 0.6 |
  | regime | 1.0 |
  | catalyst | 0.6 |

- **Coverage.** Missing inputs lower coverage instead of counting as neutral. Below 60 % coverage the setup gets no score at all (`INSUFFICIENT_EVIDENCE_COVERAGE`). When no catalyst data exists, the catalyst group is left out of coverage rather than counted against it.
- **Blend.** 40 % the strategy's own setup score plus 60 % the weighted group composite. The blend is mapped to `50 + 50 × blend`, multiplied by `0.85 + 0.15 × coverage` (so incomplete evidence scores lower), reduced by the penalties below, and clamped to 0–100.
- **Penalties** (points subtracted, each shown as counter-evidence):

  | Penalty | Points |
  |---|---|
  | thin liquidity (below $50 M median dollar volume) | up to 6 |
  | extended more than 2.5 ATR above SMA20 | up to 10 |
  | repeated failed breakouts | 3 per extra failure |
  | earnings date unknown | 5 |
  | recent negative catalyst | 8 |

- **Crowding.** If more than the per-sector cap of setups pass in one sector, the rest are held to WATCH (`SECTOR_CROWDING`).

## 3. Levels: entry zone, stop, target

Levels come from structure and volatility (ATR14), never a fixed percentage.

| Level | Rule |
|---|---|
| Entry zone | `close − 0.5 ATR` to `close + min(0.5 ATR, 2 % of close)`; valid for 2 sessions (setting `entry_valid_sessions`) |
| Stop / invalidation | the strategy's structural invalidation level, clamped to between 1 and 3 ATR below the zone midpoint; rounded down to the tick |
| Primary target | midpoint + 2 × (midpoint − stop); capped 0.25 ATR below overhead supply (the 120-session high) when that sits in the way; the uncapped level becomes the secondary target |
| Reward/risk | `(target − mid − costs) / (mid − stop + costs)`, costs = 10 bps per side; setups below **1.5 net** are rejected |
| Horizon | 3–20 trading sessions (heuristic), measured from the entry fill |

**A stop is a reference level, not a guaranteed exit.** Overnight gaps can open beyond it, and every alert says so.

## 4. Confidence: CALIBRATED vs EXPERIMENTAL vs UNVALIDATED

The forecast is the probability that the target is touched **before** the stop within the horizon, measured from the entry fill, with the stop counted first if both are touched in the same bar. Only a champion model can provide it. The champion must:
- have a valid Ed25519 signature,
- be unexpired (21-day validity),
- be feature-compatible,
- have passed the frozen promotion policy (`promotion-policy-1`).

| Gate (on the untouched holdout) | Required |
|---|---|
| Holdout entries | ≥ 200 for CALIBRATED (≥ 100 for EXPERIMENTAL) |
| Distinct decision dates | ≥ 50 |
| Market regimes covered | ≥ 2 |
| Brier skill over the training base rate | > 0 |
| Expected calibration error | ≤ 0.06 |
| Selected trades (above the probability threshold chosen on development data only) | ≥ 30 |
| p10 of the weekly block-bootstrap mean net return at 25 bps/side | > 0 |
| Shuffled-label control skill | ≤ 0.01 (otherwise the pipeline is leaking) |
| Challenger vs champion | skill gain ≥ 0.005 |

What each outcome produces:
- **CALIBRATED.** Probabilities are shown with an interval (normal approximation from the holdout size), plus expected value in R after costs.
- **EXPERIMENTAL.** Shown for study only; never a high-conviction alert.
- **UNVALIDATED.** No probability anywhere: not in Discord, not in the dashboard. The database enforces it with a CHECK constraint.

A model trained on synthetic data is always UNVALIDATED outside DEMO.

## 5. Publication tiers

| Tier | Requires | Goes to |
|---|---|---|
| HIGH_CONVICTION | CALIBRATED champion, probability at or above the frozen threshold, positive expected value after costs, Quality ≥ 70, sector and session caps | `#high-conviction-alerts` |
| RESEARCH_SETUP | Quality ≥ 75, at most 1 per session, labelled UNVALIDATED / EXPERIMENTAL with no probability | `#market-intelligence` |
| NO TRADE | nothing qualifies | recorded with its reason; no alert is invented to fill a quota |

Each published signal is an **immutable revision**: id, revision number and SHA-256 hash of the full snapshot. Updates are appended as lifecycle events (`PUBLISHED → ACTIVE → CLOSED`, or `EXPIRED` / `INVALIDATED`), each with its cause and evidence time. Signals are never edited in place.

## 6. Outcome accounting (no cherry-picking)

The track record counts **every published signal**, including entries that never filled and signals that expired or were invalidated.

Outcomes are theoretical, from daily bars:
- An entry fills only if price trades inside the zone.
- Stop and target are first-touch.
- A same-bar touch of both counts as the stop.
- A gap through the stop exits at the open.

Members' own fills are tracked separately and never change the signal's record.

## 7. Member position assessments

For each member's self-reported position, the Pi compares the latest fresh quote with the original plan and labels which pre-stated criteria are met:

> thesis intact · near invalidation · trim criteria met · exit criteria met · target touched (fill unknown) · holding window ending · add criteria met · price uncertain · stale data

Rules:
- Hysteresis stops the status flapping on small moves.
- Unrealized P&L is hidden when the price is older than an hour or unverified.
- With personalized group guidance OFF (the default), the wording is informational ("criteria met"), never an instruction.
- Assessments are private DMs to that member. If DMs are blocked, they are held privately and shown in the member's next bot reply. They are never posted publicly.

## 8. Illustrative sizing

Alerts include one illustration: "risking 0.5 % of a hypothetical account on the entry-to-stop distance". APEX does not know anyone's capital or risk tolerance and never recommends a position size. The percentage is configurable in **Settings → Signal policy**.
