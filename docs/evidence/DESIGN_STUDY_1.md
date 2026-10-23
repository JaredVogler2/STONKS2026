# Design study `design-study-1`: information rate of neutralized tilts

| | |
|---|---|
| Status | **Run** on 2026-10-23 ([run 39999238748](https://github.com/JaredVogler2/STONKS2026/actions/runs/39999238748), code `7c87dc6`, the plan below unchanged). Results and reviewer notes: [`research-results/discovery-v3/design-study-1-2026-10-23/`](../../research-results/discovery-v3/design-study-1-2026-10-23/REVIEW.md). The plan was committed (PR #32) before the run |
| Plan sha256 | `d5fef5eb047d5854eeb65bc6a828fd3faacdb5df0618d2a7478a344257fd7689` (`apex_worker/discovery/v3/design.py::DESIGN_STUDY_1`; a changed plan needs a new plan id) |
| Kind | A **measurement** of risk, not a strategy test. Nothing is selected on returns |
| Data | Protocol-2's SIP path (`discovery.v3.pipeline.load_data`), DEV decisions 2019-10-23 → data end − 22 sessions: `DEV_CONTAMINATED`, tier `DEV_DIAGNOSTIC`. PRESAMPLE is never evaluated |
| Run | `apex-discovery-v3` workflow, manual, `plan = design-study-1`; aggregates only (`design_study.json`, `DESIGN_STUDY.md`) |
| Mode | Research only: paper books; no orders, alerts, Discord signals or promotion; `HIGH_CONVICTION` and `GROUP_ACTIONABLE` stay disabled. It changes no recorded series |

## 1. Why

**Evidence speed.** The evidence tournament can make a claim no faster than the information rate of a candidate's return relative to its numeraire: IR²/2 nats a year ([EVIDENCE_TOURNAMENT.md](EVIDENCE_TOURNAMENT.md) §2). Its bounded test approaches that rate only if the declared worst one-session loss L lets it bet enough, because bets are capped at 0.9/L.

**What a design controls.** It cannot control its alpha. It can control:
- the **tracking error** of its active return, the denominator of IR;
- its **worst session**, which sets the L it can honestly declare.

**The question.** Cohort 0's tilts ([TOURNAMENT_LANE.md](TOURNAMENT_LANE.md) §2) rank the whole mask, so their active weights also carry sector, market-beta and size bets. Those bets are noise for the tilt's mechanism test against its equal-weight twin. The design rule predicts that neutralizing them lowers the tracking error at the same active-weight dispersion, so the same alpha would reach a claim years sooner. This study measures whether it does, and by how much, before any such design is registered prospectively.

## 2. Designs (all registered)

**Shared conventions.** Every book uses the tournament lane's conventions (`tournament_books`):
- the explicit mask;
- long-only integer micro-weight targets, weight ∝ equal weight × (1 + 0.5 a) with a ∈ [−1, 1];
- decided at the close of t, traded at the close of t + 1;
- cost-v3-2 close costs (2 bps per side for stocks, 5 for SPY);
- books start in SPY;
- rebalance sessions counted from the first decision.

The twin is `ew`: equal weight over the mask, every 20 sessions.

| Signal | Score | Rebalance |
|---|---|---|
| `mom` | 60-session log return | every 20 |
| `rev` | minus the 5-session log return | every 5 |
| `hi52` | close / 252-session high close − 1 | every 20 |

**Variants**, for each signal:
- **`raw`**: cohort 0's tilt, verbatim. The rank over the whole mask is mapped to [−1, 1] (a test asserts identical targets).
- **`sector`**: ranks within each sector, mapped to [−1, 1]. The active weights sum to zero in every sector.
- **`full`**: the sector ranks' least-squares residual on the within-sector-demeaned 252-session market beta (daily log returns on SPY's, at least 126 observations) and log 20-session median dollar volume (a size proxy). It is exactly sector-, beta- and size-neutral, then scaled to a rank's cross-sectional dispersion (1/√3) and clipped to [−1, 1].

**Two new designs** (`full` variant):
- **`composite-full`:** the mean of the three signals' standardized full-neutral residuals, rescaled and clipped as above, every 5 sessions;
- **`lowvol-full`:** minus the 60-session volatility of daily log returns (at least 40 observations), every 20.

## 3. Measurements

For each design, over every DEV session on which the books hold their own targets, from the session after the first trade to the end of the DEV decision window, against the twin and against SPY. X = (1 + R_design)/(1 + R_numeraire) − 1, net of each book's costs.

**Risk** (what the proposal reads):
- annualized tracking error;
- tracking error at the reference dispersion: tracking error × (1/√3) / the realized mean dispersion of the design's score. A variant cannot look safer by tilting less;
- the worst session and its date, the 0.1% and 1% quantiles, the best session.

**Books:**
- one-way turnover a year;
- cost a year, and the extra cost against the twin;
- the mean absolute active exposure to sector (the largest sector imbalance), beta and size, and the score dispersion (the neutrality check).

**Velocity against the twin:**
- the gross active alpha a year needed for a claim at evidence 800 within 10 years (cohort 0's threshold), at the information limit and with the bounded test at the proposed L;
- the years to 800 at a hypothetical gross alpha of 0.5%, 1% or 2% a year, net of the extra cost.

The assumption-free test (bets ≤ 1) would need an alpha of about log(800)/10 ≈ 67% a year for any tilt, so it is not tabulated.

**Cohort 0's declared bounds on DEV:** whether the registered loss bounds of `mom-tilt`, `rev-tilt` and `hi52-tilt` (0.03 against the twin, 0.05 against SPY) would have held on every DEV session.

**DEV diagnostics, labelled `DEV_CONTAMINATED`:** the annualized mean of X, its IR and the maximum drawdown. **They select nothing.**

## 4. Registered proposal rule

The rule reads risk only.
1. **Per signal:** the variant with the lowest tracking error against the twin at the reference dispersion. Ties go to the more neutral variant.
2. **Plus** `composite-full` and `lowvol-full`.
3. **Loss bounds:**
   - against the twin: the smallest value in {0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05};
   - against SPY: the smallest value in {0.01, 0.015, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2};
   - in each case, the value must be at least **twice** the worst DEV session. If no grid value is large enough, the design gets no bounded test.
4. **Prior mass:** 0.04 each, 0.20 in all, from the 0.55 reserve.

The output is a **proposal** for a later, separately registered cohort of the evidence tournament: its own series, reviewed and merged by the owner. It changes no recorded series. A neutral design that does not lower the tracking error is reported as such. The rule then proposes the lowest-tracking-error variant, which may be `raw`.

## 5. What it cannot show

- **Alpha.** The study measures noise, not edge. Whether a neutral design earns its alpha is the prospective tournament's question. DEV means are contaminated twice and are diagnostics only.
- **Future worst sessions.** The worst DEV session includes March 2020, but a future crash can be worse. The safety factor of 2 is a declared judgement. A break voids that design's bounded evidence, not its assumption-free evidence.
- **Survivorship.** U1 is today's S&P 500 list with `date_added` membership, so names removed before today are missing on DEV. Their blow-ups are not in the worst-session statistics. In a ±50% tilt of about 450 names, one name carries at most 0.5/450 ≈ 0.11% of active weight, so a −50% day moves X by about −0.06%. (Measured: 361 names on average, so 0.14% and about −0.07%.)
- **Dividends.** Prices are split-adjusted only, as in cohort 0 (price returns).

## 6. Publication

Aggregates only: no symbol, price or per-stock value. `_no_symbols` refuses to write a body containing any masked symbol as a key or value. The artifact keeps 7 days. Publishing is a reviewed PR that copies it into `research-results/discovery-v3/<experiment_id>/`.
