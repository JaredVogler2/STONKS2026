# Design study `design-study-2` (`design-study-2-2026-10-25`): reviewer notes

| | |
|---|---|
| Run | [40184666594](https://github.com/JaredVogler2/STONKS2026/actions/runs/40184666594), `apex-discovery-v3` with `plan = design-study-2`, manual dispatch on `main`, 2026-10-25 10:24 UTC. The research step took 7 min 3 s |
| Code | `4a72cea` (the PR #40 merge). Plan sha256 `b562c7a6…7375` equals the [registration](../../../docs/evidence/DESIGN_STUDY_2.md). PR #40 changed only how the SEC contact is read and how a failed request is reported, not the plan or an event rule |
| Data | - **Daily bars:** Alpaca historical **SIP**, split-adjusted, U1: dataset `7b7c3a6b…`. It runs one session further than design-study-1's `0339c815…` (data end 2026-10-21 against 2026-10-20). `data_quality_summary.json` is otherwise identical.<br>- **SEC filings:** the EDGAR submissions API and the 35 quarterly Form 3/4/5 data sets, 2018-01-01 → 2026-09-21: 944 requests at 5 a second. They are public and need no key. The owner's contact went to the SEC only |
| Window | 1,735 DEV sessions, 2019-10-24 → 2026-09-21 (first decision 2019-10-22), `DEV_CONTAMINATED`, tier `DEV_DIAGNOSTIC`. On average 361 of 495 stocks were in the explicit mask |
| Artifact | `apex-discovery-40184666594-1`, digest `sha256:7df5bdad…761d`. The three files here are byte-for-byte copies |
| Earlier dispatches | Three stopped before any design was computed: [40173390590](https://github.com/JaredVogler2/STONKS2026/actions/runs/40173390590) and [40174614230](https://github.com/JaredVogler2/STONKS2026/actions/runs/40174614230) were `BLOCKED_SEC_USER_AGENT_MISSING` (the contact failed the rule), and [40174747964](https://github.com/JaredVogler2/STONKS2026/actions/runs/40174747964) stopped at the first SEC request (a line break in the contact; fixed by PR #40) |
| Kind | A **measurement of risk**. The proposal rule reads only tracking error and worst sessions, and no recorded series or published result changes |

## 1. The SEC events: complete coverage, counts as the rules imply

**Mapping.** All 495 names map to a CIK. 494 have at least one earnings release in the window, and 444 at least one qualifying insider purchase.

| | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 (to 09-21) |
|---|---|---|---|---|---|---|---|---|---|
| Earnings releases | 1,898 | 1,929 | 2,021 | 1,974 | 1,997 | 2,012 | 2,051 | 2,064 | 1,548 |
| Insider purchases | 679 | 654 | 724 | 757 | 900 | 718 | 530 | 586 | 378 |

- **Earnings releases: 17,494**, about four a name a year, as quarterly reporting implies. 7,740 of them (44%) were accepted after the close or on a day without a session, so they count from the next session.
- **Insider purchases: 5,926.** The data sets hold 302,334 Form 4 filings for these issuers. 6,632 of them report a purchase coded P (acquired, shares > 0). 853 of those were filed only by ten-percent owners or "other" owners and are excluded. A filing counts once for each qualifying owner.

**In the books** (the share of the masked names, averaged over the sessions):
- `insider-full`: 13.7% flagged (a purchase usable in the last 60 sessions). Every decision had at least one flagged name.
- `eap-full`: 13.0% flagged (an estimated release in the window). An estimate existed for 99.5%. As a check, the window spans 9 of the roughly 63 sessions between quarterly releases, which is 14%.
- `ear-full`: an announcement return was available for 93.6%.

## 2. Risk: the lowest tracking error measured, from small tilts

| Design | Tracking error vs twin | At the reference dispersion | Score dispersion | Worst session vs twin | 0.1% quantile | Worst vs SPY (2020-03-12) | Turnover a year | Extra cost a year |
|---|---|---|---|---|---|---|---|---|
| `insider-full` | **0.69%** | 1.14% | 0.35 | −0.53% (2020-03-18) | −0.34% | −3.20% | 1.84 | 0.04% |
| `eap-full` | **0.69%** | 1.42% | 0.28 | −0.50% (2020-03-18) | −0.31% | −3.22% | 3.68 | 0.11% |
| `ear-full` | **0.82%** | 0.83% | 0.57 | −0.51% (2020-03-18) | −0.30% | −3.22% | 2.52 | 0.07% |

The twin `ew` against SPY: tracking error 7.50%, worst session −3.00% (2020-03-12). All three designs had their worst session against the twin on 2020-03-18. Against SPY, all three were worst on 2020-03-12, the day equal weight itself lost 3.00% against SPY.

**The construction did what it claims** (mean absolute active exposure per decision):
- the indicator designs: beta 0.000002, size 0.000004, largest sector imbalance 0.000003. That is zero up to the integer weights;
- `ear-full`: 0.0003, 0.0005 and 0.0001. It is neutral up to the clipping, like design-study-1's `full` designs (0.0004–0.0023).

**Reading.**
- **The raw tracking errors are the lowest measured so far**, below `lowvol-full`'s 0.76%.
- **That is mostly because the tilts are small.** An indicator flags only 13–14% of the names, so its score dispersion is 0.28–0.35, against a rank's 0.58.
- **Per unit of tilt the noise is familiar.** At the reference dispersion, the indicator designs (1.14% and 1.42%) are about as noisy as cohort 1's signal tilts (1.26–1.45%). `ear-full` (0.83%) is like `lowvol-full` (0.80%).

## 3. What it would take to prove a design

The table gives the gross active alpha a year against the twin, before costs, needed for a claim at evidence **1,000** within 10 years. That is the threshold of a twin-only claim at prior mass 0.04. The years to 1,000 use the bounded test at the proposed loss bound, net of the measured extra cost.

| Design | Twin loss bound | Alpha needed: limit · **bounded** | Years at 0.5% a year | At 1% | At 2% |
|---|---|---|---|---|---|
| `insider-full` | 0.015 | 0.85% · **1.33%** | 36.0 | **14.0** | **6.3** |
| `eap-full` | 0.01 | 0.92% · **1.09%** | 43.9 | **11.4** | **4.6** |
| `ear-full` | 0.015 | 1.04% · **1.42%** | 50.1 | **15.8** | **6.7** |
| Cohort 1: `lowvol-full` | 0.0075 | **0.9%** | — | 9.0 | 3.5 |
| Cohort 1: the signal tilts | 0.015–0.02 | **1.8–2.1%** | — | 24.5–62.9 | 8.7–10.9 |

Cohort 1's figures are from [TOURNAMENT_COHORT_1.md](../../../docs/evidence/TOURNAMENT_COHORT_1.md) §4, at the same level and with the same method.

**Reading.**
1. **In portfolio terms these designs sit between `lowvol-full` and the signal tilts.** A 1% a year edge would be provable in 11–16 years, against 25–63 for the signal tilts.
2. **At this little noise, the loss bound limits the bounded test.**
   - At the information limit, every design needs the same net information ratio, 1.18 (√(2 ln 1000 / 10)).
   - At these small tracking errors, the bounded test's bet cap (0.9/L) binds.
   - `eap-full`, with L = 0.01, needs 1.09%.
   - `insider-full` and `ear-full` missed 0.01 narrowly: twice their worst sessions is 1.07% and 1.01%. With L = 0.015 they need 1.33% and 1.42%.
   - The registered grid stands.
3. **A flag needs a large effect per name.**
   - About 6% (`insider-full`) and 5% (`eap-full`) of the book is moved toward the flagged names.[^w]
   - A claim within 10 years would therefore need the flagged names to beat comparable unflagged names by roughly 22–23% a year while flagged.
   - That is about 5% over the 60 sessions after an insider purchase, and about 0.8% for each release, which is held for about 9 sessions.
   - Effects that large would be strong for S&P 500 stocks, and the published record (registration §1) comes mostly from broader universes. A claim within 10 years is a long shot, but the tournament allocates paper capital by the evidence long before a claim.

[^w]: For a score with two values, the extra weight on the flagged names is 0.5 · s · √(p(1 − p)), with p the flagged share and s the score dispersion: 6.0% and 4.8%. The neutral residual is not exactly two-valued, so these figures are approximations.

## 4. Checks

- **The twin reproduces design-study-1.**
  - Over the 1,734 shared sessions, the equal-weight twin's statistics against SPY are design-study-1's to every printed digit: the best session, the worst session and the maximum drawdown.
  - The changes in its mean and its variance are both explained, to 12 digits, by one extra session, 2026-09-21, on which the twin trailed SPY by 0.99%.
- **The event counts match the rules.** There are four releases a name a year, the release window flags the share its length implies, and all 495 names map to a CIK.
- **Aggregates only.** Checked against the U1 snapshot, no U1 symbol or CIK appears in `design_study.json` or `DESIGN_STUDY.md`. ("IR" in a table header is the information ratio.) `data_quality_summary.json` names the five late-start stocks, as every earlier run's does. The SEC contact appears nowhere.

## 5. DEV diagnostics (`DEV_CONTAMINATED`; they select nothing)

| Against the twin | Mean a year | IR | Maximum drawdown (log) |
|---|---|---|---|
| `insider-full` | −0.08% | −0.11 | 1.8% |
| `eap-full` | −0.03% | −0.04 | 1.9% |
| `ear-full` | −0.06% | −0.07 | 2.3% |

Against SPY, all three trail by about 1.5% a year, as the twin does (−1.44%, the mega-cap era).

**None of these is evidence.**
- DEV is contaminated twice and U1 is survivor-biased.
- Each standard error is 0.26–0.31% a year (about 0.38 in IR), so every mean is within a third of a standard error of zero.
- They agree with the program's record: nothing beat its investable alternative on DEV.

## 6. The registered proposal

The rule proposes every design, each with the smallest grid loss bound at least twice its worst DEV session.

| Candidate | Rebalance | Twin | Twin loss bound | Loss bound against SPY | Prior mass |
|---|---|---|---|---|---|
| `insider-full` | every 5 | `ew` | 0.015 | 0.075 | 0.04 |
| `eap-full` | every 5 | `ew` | 0.01 | 0.075 | 0.04 |
| `ear-full` | every 5 | `ew` | 0.015 | 0.075 | 0.04 |

The prior mass totals 0.12 of the 0.35 reserve, which would leave 0.23. Claims would be against the twin only, at 1,000.

**It is a proposal.** Registering it as cohort 2, its own series, is the owner's decision. Beyond what cohort 1 needed, that registration needs:
1. **Live event recording, with the same rules.** The quarterly data sets are published only after each quarter, so live purchases must be read from each day's Form 4 filings. Those carry the same fields: the transaction code, acquired or disposed, the shares and the owner's relationship. Each record would name the filings it used, so a replay can check it.
2. **A reconciliation** of the live events against each quarterly data set when it is published. A mismatch is recorded, never repaired in place.
3. **The SEC contact in the tournament job**, the same secret, and a request budget that fits the job's 8-minute timeout.
4. **Fail closed.** A session without the SEC's answer is a `MISSED_OBSERVATION`, never a decision read as "no event".

## 7. Limits

- **Survivorship.** U1 is today's S&P 500 list. Names removed before today are missing, and so are their filings.
- **Future crashes.** They can be worse than March 2020. All three twin loss bounds rest on the same day, 2020-03-18, and the safety factor of 2 is a declared judgement.
- **Coverage.** Releases outside an 8-K item 2.02 are missed; one name has none in the window. A late Form 4 counts from its filing date, never from its transaction date.
- **Correlation between the designs was not measured.** How much their active returns move together, and with cohort 1's, is unknown. The tournament's allocation would show it.
- **Price returns.** Prices are split-adjusted only, so dividends are excluded throughout.
