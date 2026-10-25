# Design study `design-study-2`: information rate of SEC-event tilts

| | |
|---|---|
| Status | **Registered, not yet run.** It runs once the owner has added the repository secret `APEX_SEC_USER_AGENT` (§8). Without it, the run stops `BLOCKED_SEC_USER_AGENT_MISSING` before any download |
| Plan sha256 | `b562c7a6fb2fb6cf772e8958909c2f6dd89448680cf2851d33096e7ec3487375` (`apex_worker/discovery/v3/design_events.py::DESIGN_STUDY_2`). The hash covers the event rules (`apex_worker/research/sec_history.py::EVENT_RULES`); a changed plan or rule needs a new plan id |
| Kind | A **measurement** of risk, not a strategy test. Nothing is selected on returns |
| Data | - **Daily bars:** design-study-1's (protocol-2's SIP path, `discovery.v3.pipeline.load_data`), DEV decisions 2019-10-23 → data end − 22 sessions: `DEV_CONTAMINATED`, tier `DEV_DIAGNOSTIC`. PRESAMPLE is never evaluated.<br>- **SEC filings** from 2018-01-01 to the last DEV session (§2): free, public, no key |
| Run | `apex-discovery-v3` workflow, manual, `plan = design-study-2`; aggregates only (`design_study.json`, `DESIGN_STUDY.md`) |
| Mode | Research only: paper books; no orders, alerts, Discord signals or promotion; `HIGH_CONVICTION` and `GROUP_ACTIONABLE` stay disabled. It changes no recorded series |

## 1. Why

**A different source of information.** All ten designs recorded so far ([TOURNAMENT_LANE.md](TOURNAMENT_LANE.md), [TOURNAMENT_COHORT_1.md](TOURNAMENT_COHORT_1.md)) read prices only. Filings are a different source of information, with a long published record:
- **Insider purchases.** Open-market purchases by officers and directors have been followed by higher returns (Lakonishok and Lee 2001; Jeng, Metrick and Zeckhauser 2003; Cohen, Malloy and Pomorski 2012). Sales are mostly liquidity and diversification, so only purchases are used.
- **The earnings-announcement premium.** Stocks have earned more in the days around their scheduled earnings announcements, which are predictable from the company's own cadence (Frazzini and Lamont 2007; Barber, De George, Lehavy and Trueman 2013).
- **Drift after the announcement return.** Prices have kept moving in the direction of the market's first reaction to an earnings release (Bernard and Thomas 1989; Chan, Jegadeesh and Lakonishok 1996; Brandt, Kishore, Santa-Clara and Venkatachalam 2008).

**Priors, not evidence.** Most of that record is from broader universes than U1, and several effects have weakened since publication. Whether any of them pays in today's S&P 500, after costs, is the prospective tournament's question. **This study measures what a design controls**, as design-study-1 did:
- its tracking error against its equal-weight twin;
- its worst session, which sets the loss bound it can declare.
Together they determine how fast a real edge could earn a claim.

## 2. Events (registered rules)

**Sources.** The SEC's fair-access policy requires a descriptive User-Agent with a contact email, and at most 10 requests a second. The study uses the owner's contact (`APEX_SEC_USER_AGENT`) and 5 requests a second. The contact goes to the SEC only; it is never printed or written.
- **Earnings releases:** the EDGAR submissions API, `data.sec.gov/submissions/CIK##########.json`, and the older pages it lists under `filings.files` that overlap the window. They are parsed by `catalysts.sec`.
- **Insider purchases:** the SEC's quarterly Insider Transactions Data Sets (Forms 3, 4 and 5), one zip a quarter, 2018 Q1 → the quarter of the last DEV session. The study reads the `SUBMISSION`, `NONDERIV_TRANS` and `REPORTINGOWNER` tables.
- **Issuer mapping:** U1 symbol → CIK, from the SEC's current `company_tickers.json`. U1 is today's list, so current tickers are the right key. Events are keyed by issuer CIK. A symbol without a CIK has no events.

**Definitions** (the hashed `EVENT_RULES`):
- **Earnings release:** an 8-K (not 8-K/A) listing item 2.02, "Results of Operations and Financial Condition". One release per company and US Eastern acceptance day; the earliest acceptance counts.
- **Insider purchase:** a Form 4 (not 4/A) with a non-derivative transaction coded P (open market or private purchase), acquired (A), shares > 0. It must be filed by a reporting owner who is a director or an officer of the issuer; ten-percent owners and "other" do not count. One event per filing and qualifying owner.

**Point in time.** An event counts from the first session whose close-time decision could have used it:
- **an earnings release:** the first session whose close is at or after its EDGAR acceptance (`catalysts.earnings.first_usable_session`). The submissions API prints the acceptance in UTC; this was checked against EDGAR's own filing index, which shows it in US Eastern. An after-hours release counts from the next session;
- **an insider purchase:** the first session **after** its filing date. A Form 3, 4 or 5 submitted by 10 pm Eastern is dated that business day (Regulation S-T rule 13(a)(4)), so it may arrive after the close, and the data sets carry no acceptance time.

**Fail closed.** The run stops rather than reading a gap as "no event":
- **`BLOCKED_SEC_USER_AGENT_MISSING`:** no contact. Exit 2, nothing fetched;
- **`SEC_DATA_INCOMPLETE`:** a refused request, or a quarter's data set not yet published;
- **`SEC_DATA_SCHEMA_CHANGED`:** a missing table or column, or an unparseable page.

A malformed line in a data set is skipped and counted.

## 3. Designs (all registered)

**Shared conventions** (design-study-1's `full` construction, `tournament_books`):
- the explicit mask;
- long-only integer micro-weight targets, weight ∝ equal weight × (1 + 0.5 a) with a ∈ [−1, 1];
- decided at the close of t, traded at the close of t + 1;
- cost-v3-2 close costs (2 bps per side for stocks, 5 for SPY);
- books start in SPY;
- rebalance sessions counted from the first decision.

**Neutrality.** The active scores are exactly sector-, beta- and size-neutral: the least-squares residual of the sector-demeaned score on the within-sector-demeaned 252-session market beta (at least 126 observations) and log 20-session median dollar volume.

The twin is `ew`: equal weight over the mask, every 20 sessions.

| Design | Score at the close of t | Scaling | Rebalance |
|---|---|---|---|
| `insider-full` | 1 if the name has an insider purchase usable at one of the last 60 sessions (t − 59 … t), else 0 | indicator | every 5 |
| `eap-full` | 1 if the name's next release, estimated from its releases known at t, falls after the session after t and no later than the 10th session after t, else 0 | indicator | every 5 |
| `ear-full` | the latest release's return relative to SPY over its first two usable sessions, for 60 sessions once known (until the next release's return replaces it); ranked within sectors | ranked | every 5 |

**How each design reads the events:**
- **The cadence estimate** is `catalysts.earnings.estimate_next_from_8k`'s rule (a test asserts the same day). If a known release lies within 21 days of the same quarter a year earlier, the estimate is that release + 364 days; otherwise it is the last release + the median gap. It needs at least three known releases; without three, the name scores 0.
- **The window starts after the trade.** The estimated release must fall after the session after t, so a pre-open release on the trade day (whose reaction precedes the trade) does not count.
- **The announcement return** is known at the close of its second usable session. A quarantined or missing bar in its window gives no value.

**Scaling:**
- **Indicator:** the neutral residual divided by its largest absolute value. The active weights stay exactly neutral (no clipping), a flagged name is overweighted by at most 50%, and a decision with nothing flagged holds the twin.
- **Ranked:** design-study-1's `full` scaling. The residual is scaled to a rank's dispersion (1/√3) and clipped to [−1, 1], so it is neutral up to the clipping.

**Missing data never reads as a signal.** A name without a CIK, a release or an estimate scores 0.

## 4. Measurements

They are design-study-1's, for each design, over every DEV session on which the books hold their own targets, against the twin and against SPY. X = (1 + R_design)/(1 + R_numeraire) − 1, net of each book's costs.

**Risk** (what the proposal reads):
- annualized tracking error;
- tracking error at the reference dispersion;
- the worst session and its date, the 0.1% and 1% quantiles, the best session.

**Books:**
- one-way turnover a year;
- cost a year, and the extra cost against the twin;
- the mean absolute active exposure to sector, beta and size, the score dispersion, and the share of masked names flagged.

**Velocity against the twin:**
- the gross active alpha a year needed for a claim at evidence **1,000** within 10 years. That is the threshold of a twin-only claim at prior mass 0.04: 2 / (0.05 × 0.04), as in cohort 1. It is given at the information limit and with the bounded test at the proposed L;
- the years to 1,000 at a hypothetical gross alpha of 0.5%, 1% or 2% a year, net of the extra cost.

**Event coverage** (aggregates only):
- releases and purchases by year;
- names mapped to a CIK;
- after-close releases;
- the mean share of masked names flagged by each design.

**DEV diagnostics, labelled `DEV_CONTAMINATED`:** the annualized mean of X, its IR and the maximum drawdown. **They select nothing.**

## 5. Registered proposal rule

The rule reads risk only.
1. **Every design is proposed.** There is one variant per signal, so there is nothing to choose between.
2. **Loss bounds:**
   - against the twin: the smallest value in {0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05};
   - against SPY: the smallest value in {0.01, 0.015, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2};
   - in each case, the value must be at least **twice** the worst DEV session. If no grid value is large enough, the design gets no bounded test.
3. **Prior mass:** 0.04 each, 0.12 in all, from the 0.35 reserve. Claims are against the twin only, as in cohort 1.

The output is a **proposal** for a later, separately registered cohort of the evidence tournament: its own series, reviewed and merged by the owner. That registration also needs the same events recorded live (daily filings, the same rules), which is built only if the proposal is adopted. It changes no recorded series.

## 6. What it cannot show

- **Alpha.** The study measures noise, not edge. DEV means are contaminated twice and are diagnostics only.
- **Future worst sessions.** A future crash can be worse than any DEV session. The safety factor of 2 is a declared judgement.
- **Survivorship.** U1 is today's S&P 500 list, so names removed before today, and their filings, are missing on DEV.
- **Mapping and coverage:**
  - A symbol whose company changed its CIK, or that has no CIK in today's list, has no events. Their count is reported.
  - Releases filed only by press release, or under another form, are missed.
  - A late Form 4 counts from its filing date, never from its transaction date.
- **The cadence estimate** can miss a moved date. The window and the 21-day anchor tolerance are declared choices.
- **Dividends.** Prices are split-adjusted only, as in cohorts 0 and 1 (price returns).

## 7. Publication

Aggregates only: no symbol, CIK, owner, price or per-stock value. `_no_symbols` refuses to write a body containing any masked symbol or mapped CIK as a key or value. The SEC contact is never written. The artifact keeps 7 days. Publishing is a reviewed PR that copies it into `research-results/discovery-v3/<experiment_id>/`.

## 8. Running it

**1. The owner adds the contact, once.** Go to **Settings → Secrets and variables → Actions → Secrets → New repository secret**:
- name: `APEX_SEC_USER_AGENT`;
- value: a name and a contact email, such as `APEX Research you@example.com`. A bare email also works: the run sends `APEX Research <email>`. Line breaks and odd spaces in the value are joined into single spaces, because a header cannot carry them. The run stops, naming the rule but never showing the value, if the value has no email, a name shorter than 3 characters, a generic client name such as `curl`, or a non-ASCII character.

**2. This registration merges.** The workflow runs only plans committed on `main`.

**3. Manual dispatch.** Run `apex-discovery-v3` with `plan = design-study-2` and `experiment_id = design-study-2-<date>`.

**Expected cost:**
- one daily-bar download, as design-study-1;
- about 600 to 1,500 SEC requests at 5 a second (a few minutes);
- 35 quarterly data sets;
- within the 45-minute manual-run reservation.

The 2026 Q3 data set must already be published, because the last DEV session is in that quarter. If it is not, the run stops `SEC_DATA_INCOMPLETE` and is repeated later.
