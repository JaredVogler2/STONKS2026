# Evidence = money: an anytime-valid evidence tournament for APEX

| | |
|---|---|
| Status | **Research only.** The engine, its tests and a synthetic battery are complete. No market data was read, and nothing is traded, alerted or promoted |
| Code | `apps/pi-worker/src/apex_worker/evidence/`:<br>- `eprocess.py`: the betting e-process;<br>- `tournament.py`: the tournament and paper book;<br>- `velocity.py`: evidence speed and its limits.<br>CLI: `apex research evidence velocity\|replay`. Battery: `apps/pi-worker/bench/epa_battery.py` |
| Results | [EPA_BATTERY.md](EPA_BATTERY.md) |
| Why it was needed | [CODEBASE_REVIEW.md](CODEBASE_REVIEW.md) |
| Standing constraints | Research only:<br>- no orders, alerts, Discord signals or promotion;<br>- `HIGH_CONVICTION` and `GROUP_ACTIONABLE` stay disabled;<br>- the frozen forward shadow and `realeq-protocol-1`/`-2` are unchanged |

## 0. Summary

**Why every batch ended NO_TRADE.** The cause is the method more than the strategies. APEX validates on history and only then allocates.
- On the history it has (DEV, survivor universe, read repeatedly), that approach **cannot say yes** to any realistic edge, because power is too low.
- It **cannot trust a yes** either: survivorship and forking paths produce "mirage" edges.
- So the outcome is NO_TRADE by construction ([review](CODEBASE_REVIEW.md), M-1).

**The finding (§2).** Take the null hypothesis "this candidate does not beat SPY".
- The strongest evidence any assumption-free test can gather, while being read at every session, equals the wealth of an **unlevered paper portfolio** relative to SPY. **Evidence is money.** An edge can be proved only as fast as it compounds.
- Applied to the program's own best DEV result, taken at face value: G2 would need **about 35 years** of prospective data to reach evidence 20 (α = 0.05). The chance of doing so within 10 years is under 1%.
- No method can beat the information limit, IR²/2 nats a year, and even at that limit G2 needs 29 years.
- Machine learning, Bayesian analysis or cleverer statistics cannot change this. It is a property of how much a market reveals about a strategy.

**The method (§3): a perpetual prospective evidence tournament.**
- **Prospective only.** Candidates enter prospectively.
- **Backtests become priors.** A backtest can only set a candidate's prior mass, never its evidence. That is valid even on contaminated DEV.
- **Evidence is earned.** Each candidate's evidence is earned paper wealth against SPY. A twin numeraire separates selection skill from beta and universe effects.
- **Capital follows evidence.** The paper book holds each candidate in proportion to its prior times its evidence.
- **Claims are anytime-valid.** Over every candidate ever admitted, and every way each one is tested, the chance of a false claim stays at most α, under continuous monitoring and unlimited admissions (§3.3).

**The design rule (§4).** Evidence arrives at most at IR²/2 a year, and approaches that rate only with a declared, checked bound on one-session losses. So candidates should be designed for **information rate**, not for return:
- diversified, low-tracking-error tilts or twin-paired books;
- not concentrated 5–10 name books.

| Design | Years to evidence 20 |
|---|---|
| A diversified tilt at IR 1.5 | Simulated median about 7 years |
| G2-type books | Decades |

**What the battery shows** ([EPA_BATTERY.md](EPA_BATTERY.md)):
- **Monitoring.** Reading a t-test on the forward shadow at every session gives a **34%** false-claim rate where 5% is nominal. The e-process: **0%**.
- **Velocity.** Without an assumption, no realistic candidate reaches evidence 20 within 20 years. With a checked loss bound, a diversified tilt at IR 1.5 does so within 10 years on 86% of paths.
- **Mirage worlds**, where the backtest edge is not real: the program's current Holm-gated rule validates the mirage in **19%** of concentrated-book universes and **94%** of tilt universes. The tournament: at most 0.1%.
- **Strong honest edges:** the prior-informed, evidence-weighted book earns +3.7% a year on concentrated books (the current rule: +2.9%) and +3.2% on tilts (the current rule: +3.9%). Naive backtest selection gains most on average, but loses 3.1% a year when no edge exists, and 55% of those universes end 20% or more behind SPY.
- **Cost:** with no edge anywhere, the tournament costs 0.1–0.8% a year against SPY on paper, depending on the book and the world.
- **Downside:** on concentrated books, the evidence-weighted book ends 20% or more behind SPY in 14–19% of no-edge universes. On diversified tilts this never happened.

**What it does not do:**
- It does not create an edge.
- It does not prove one faster than the market reveals it.
- It does not authorize trading. Every claim and allocation is a paper research artefact until the owner separately authorizes anything.

## 1. The identity: the portfolio is the test

Notation, for one settlement period (a session):
- R_c is the candidate's **net** simple return, after its own trading costs;
- R_s is SPY's simple return;
- X = (1 + R_c) / (1 + R_s) − 1 is the candidate's return **in SPY units**.

Take a sub-account that holds λ of its wealth in the candidate and 1 − λ in SPY, rebalanced to λ every period. Because 1 + λR_c + (1 − λ)R_s = (1 + R_s)(1 + λX), its wealth relative to SPY is exactly

    W_λ(t) = V(t) / SPY(t) = Π_s (1 + λ X_s)

The test `test_identity_sub_account_relative_wealth_equals_portfolio_over_spy` checks this against an explicit holdings simulation with rebalancing costs.

**The null hypothesis, H0:** E[X_s | everything known when period s starts] ≤ 0.

In words: no unlevered mix of SPY with the candidate grows faster than SPY in expectation. Under H0:
- for 0 ≤ λ ≤ 1, each factor has conditional mean at most 1 and is never negative (X > −1), so W_λ is a **nonnegative supermartingale**, and so is any mixture over λ;
- **Ville's inequality** then gives

      P_H0( sup_t W(t) ≥ 1/α ) ≤ α,    for every α, under monitoring after every session, forever.

So the paper portfolio's relative wealth is an anytime-valid test statistic (an e-process). There is no look penalty, no fixed horizon and no optional-stopping problem:
- `1 / sup W` is a p-value valid at any time;
- **rebalancing costs** only shrink W, so the guarantee is conservative;
- the **mixture** over a grid of bets λ is one portfolio holding the wealth-weighted bet. That bet is chosen from past data only (`test_mixture_is_one_portfolio_holding_the_predictable_wealth_weighted_bet`, `test_bet_is_predictable`).

## 2. Evidence = money, and the information limit

**Theorem.** Let H0 contain every law of X on (−1, ∞) with E[X] ≤ 0. Suppose f ≥ 0 is an e-variable, meaning E_P[f(X)] ≤ 1 for every P in H0. Then f(x) ≤ 1 + λx for all x > −1, for some λ in [0, 1].

**Proof.**
1. **Reduce to a concave function.** Let ĝ be the least concave majorant of f on (−1, ∞).
   - For any mean m ≤ 0, two-point laws with mean m approach ĝ(m), so the e-variable condition forces ĝ(m) ≤ 1 on (−1, 0].
   - In particular ĝ(0) ≤ 1.
2. **Bound ĝ by a tangent line.** A concave function lies below each of its tangent lines. So for any supergradient s at 0, ĝ(x) ≤ ĝ(0) + s·x ≤ 1 + s·x.
3. **Case s < 0.** Then ĝ(x) ≤ 1 for x ≥ 0, and also for x ≤ 0 by step 1. So f ≤ 1, which is the claim with λ = 0.
4. **Case s ≥ 0.** Nonnegativity near −1 gives 0 ≤ ĝ(−1⁺) ≤ 1 − s, so s ≤ 1. Take λ = s. ∎

**Sequential version.** Apply the theorem conditionally in each period. Every e-process built from one-period conditional e-variables is then dominated by Π(1 + λ_t X_t), where each λ_t ∈ [0, 1] is chosen from the past. This class contains every betting or likelihood-ratio construction. More generally, Ramdas, Ruf, Larsson & Koolen show that admissible anytime-valid inference must rely on nonnegative supermartingales. **So the strongest assumption-free evidence is the relative wealth of some unlevered paper portfolio, chosen from past data.**

**Consequence: evidence velocity.** The expected log-evidence a year is at most

    g* = max over 0 ≤ λ ≤ 1 of E log(1 + λX) ≈ λ*μ − λ*²s²/2,    with λ* = min(1, μ/s²),

where μ is the annual drift of X and s its tracking error.
- For every candidate with a positive estimate in the table below, λ* reaches 1. So **g\* equals the candidate's own log growth relative to SPY.**
- Evidence 20 therefore needs relative wealth of about 20× (more after the bet grid's prior).

**A declared assumption buys speed.** Declare that X ≥ −L in every session.
- Bets up to λ < 1/L are then valid: notional leverage in the test, never in the book.
- g\* rises toward the **information limit IR²/2** (IR = μ/s). This is the Kullback–Leibler rate of the Gaussian model, which no test can exceed in expectation, even one told the true alternative.
- The assumption is checked at every observation. A violation marks the process `INVALID_ASSUMPTION` permanently and voids its claims.

**The program's candidates, taken at their DEV estimates.** This is optimistic: the DEV estimates are inflated by survivorship and forking paths. `velocity.py`, with a known best bet (simulated mixtures are slower; see the battery):

| Candidate | Log growth vs numeraire (a year) | TE | IR | Assumption-free: years to e = 20 | Assumption-free: P(within 10 y) | Loss bound L | Bounded: years | Bounded: P(within 10 y) | Information limit (years) | Fixed-horizon z, 80% power, one look (years) |
|---|---|---|---|---|---|---|---|---|---|---|
| G2 vs SPY | +8.62% | 26.8% | 0.46 | **34.8** | 0.9% | 0.2 | 28.9 | 14% | 28.9 | 29.8 |
| G2 vs 20 random books (selection) | +11.4% | 23.7% | 0.60 | 26.2 | 1.0% | 0.2 | 16.6 | 38% | 16.6 | 17.1 |
| S1 vs matched controls, open entry | +1.35% | 5.9% | 0.26 | 223 | 0 | 0.1 | 91 | 0.1% | 91 | 94 |
| S1 vs matched controls, close entry | +0.18% | 4.9% | 0.06 | 1679 | 0 | 0.1 | 1617 | 0 | 1617 | 1669 |
| Hypothetical concentrated book, +12% at 15% TE | +10.9% | 15% | 0.80 | 27.5 | 0 | 0.2 | 9.6 | 65% | 9.4 | 9.7 |
| Hypothetical diversified tilt, 3% at 3% TE | +2.96% | 3% | 1.00 | 101 | 0 | 0.05 | 7.6 | 79% | 6.0 | 6.2 |
| Hypothetical diversified tilt, 4.5% at 3% TE | +4.46% | 3% | 1.50 | 67 | 0 | 0.05 | 4.5 | 99% | 2.7 | 2.7 |

Sources for the program rows, all from `research-results/discovery-v3/v3-pilot-2-2026-10-23/results.json`:
- G2 vs SPY: TE from its volatility (0.353), SPY's (0.195) and beta 1.2;
- the other program rows: TE implied by each test's bootstrap confidence interval.

**Reading.**
1. The program's strongest DEV result is not provable prospectively within a decade, even if it is exactly real.
2. S1's selection value is not provable in a lifetime.
3. **The fixed-horizon test is not slower than the information limit.** Its cost is a single look at a horizon fixed in advance. The e-process buys continuous monitoring, stopping and unlimited candidates, at a price of a few years (§4).

## 3. The tournament (`tournament.py`)

### 3.1 Admission and prior mass

Candidate k is admitted with prior mass γ_k. It takes part from a stated session, which must come after every settled session. Total mass across all candidates ever admitted is at most 1; mass not yet given out stays in SPY. So:
- admission can continue **forever** with no multiplicity inflation, because a schedule such as cohort i receiving 2^−(i+1) never runs out;
- the prior can come from anything known before the candidate's first period, **including contaminated DEV backtests**. Validity needs only that the masses are fixed in advance. A backtest can make a prior better or worse; it can never supply evidence;
- a changed candidate is a **new** candidate with its own mass. The registry forbids editing a candidate's identity.

### 3.2 Evidence per candidate

| Evidence | Numeraire | Assumption | Book or statistic |
|---|---|---|---|
| `vs_spy` | SPY | none | The λ-grid sub-accounts (λ ≤ 1): real unlevered paper portfolios, charged their rebalancing costs |
| `vs_spy_bounded_loss` | SPY | X ≥ −L every session (declared, checked) | A test statistic (λ up to 0.9/L), not a portfolio. Voided permanently if the bound breaks |
| `vs_twin` | The candidate's negative control: the same mask, schedule, sizes and execution, with random selection | none | Measures **selection skill**. A high-beta or survivor-tilted book beats SPY in bull markets, but not its own twin (`test_claims_edge_mechanism_and_bounded_void`) |

### 3.3 Claims

All claims are online: they hold under continuous monitoring, over every candidate ever admitted.

**One error budget for every claim.** Candidate k is tested on up to m_k ≤ 4 e-processes, its *claim tests*:
- `vs_spy`, always;
- `vs_spy_bounded_loss`, with a declared loss bound;
- `vs_twin`, with a twin;
- `vs_twin_bounded_loss`, with a declared twin loss bound.

Its prior mass γ_k is split equally across them (`CLAIM_RULE` in `evidence/tournament.py`, recorded in a series configuration). Each test therefore claims at evidence **m_k/(α γ_k)**. By a union bound, which needs no independence between tests or candidates, P(any false claim, any test, any candidate, ever) ≤ Σ_k m_k · α γ_k / m_k = α Σγ ≤ α.

| Claim | Condition | Error guarantee |
|---|---|---|
| `EDGE_VS_SPY` | sup `vs_spy` ≥ m_k/(α γ_k) | ≤ α γ_k/m_k |
| `EDGE_VS_SPY_BOUNDED_LOSS` | The same on `vs_spy_bounded_loss`, while the bound holds | ≤ α γ_k/m_k under the declared bound; void if it breaks |
| `SELECTION_VS_TWIN` | sup `vs_twin` ≥ m_k/(α γ_k): the candidate beats its own negative control | ≤ α γ_k/m_k |
| `SELECTION_VS_TWIN_BOUNDED_LOSS` | The same on `vs_twin_bounded_loss`, while the bound holds | ≤ α γ_k/m_k under the declared bound; void if it breaks |
| `EDGE_AND_MECHANISM` | Both `EDGE_VS_SPY` and `SELECTION_VS_TWIN` hold | Nothing extra: it is false only if one of those two claims is |
| All of the above | — | P(any false claim) ≤ α Σγ ≤ α |
| Global | G = (1 − Σγ) + Σ γ_k e_k ≥ 1/α | ≤ α if no candidate beats SPY |
| `e_bh_now` | e-BH over the current e-values | FDR ≤ α at a fixed or stopping time, under arbitrary dependence |

Splitting the mass costs at most log 4 ≈ 1.4 nats of evidence per claim, against a candidate tested only one way. Testing several ways without splitting it would let the family's error grow to m_k α.

A claim is a statistical statement on paper. **It authorizes nothing.** Live use would still need independent scientific, operational and legal review and an explicit owner decision.

### 3.4 The paper book

Let u be the prior mass of "no candidate beats SPY", held in SPY (default 0.5).

**`evidence_weighted` (default):**
- Candidate k is held at weight (1 − u) γ_k ε_k / (u + (1 − u) G_ε). Here ε is the bounded evidence while its bound holds, otherwise the assumption-free evidence, read before the period. G_ε is the same sum as G, using ε.
- This is Bayesian model averaging with e-values as likelihood ratios.
- Weights are long-only and sum to at most 1. So the book's relative wealth is itself a nonnegative supermartingale under the global null (`test_evidence_weighted_book_is_a_supermartingale_under_the_global_null`).
- The streaming class matches the vectorised path exactly (`test_evidence_weighted_book_streaming_matches_the_path_and_follows_evidence`).
- **Caution for concentrated candidates.** The book holds a candidate at full weight within its share. With ten concentrated books (TE 10–25%) at 50% of the book, it ended 20% or more behind SPY in 14–19% of no-edge universes over 10 years; the mixture book never did ([battery C](EPA_BATTERY.md)). Give concentrated candidates small prior masses, or use the mixture book for them.

**`mixture`:**
- The literal identity: the book holds every sub-account, and its relative wealth is exactly B = u + (1 − u)G.
- Pathwise, B ≥ u.
- Pathwise regret: log G ≥ log(γ_k w_j) + log W_kj for every candidate k and bet j.
- Tested against an explicit holdings simulation (`test_mixture_book_wealth_is_the_paper_portfolio_relative_to_spy`).

### 3.5 What changes for the program

| Today | With the tournament |
|---|---|
| Validate on DEV, then allocate. DEV is read again by every batch | DEV only shapes priors. Evidence comes only from sessions that had not happened when the candidate was admitted |
| Each batch restarts Holm, and the cumulative trial count only grows | One prior budget for all time. Admitting a candidate costs only its mass |
| Fixed-horizon tests on a forward shadow that posts every session | An anytime-valid readout. Interim reading is free (§5: naive reading gives 34% false claims) |
| Binary dispositions (REJECTED / INCONCLUSIVE / PROMISING) | Graded evidence that accumulates, plus a paper allocation that follows it |
| Survivor universe (U1) and no point-in-time events (`SOURCE_BLOCKED`) | **Prospective data has no survivorship, and events recorded as they happen are point-in-time by construction.** The SEC pipeline already in the code (8-K, Form 4 with acceptance times) makes S4, H3 and H7 evaluable from their first prospective session ([review](CODEBASE_REVIEW.md), C-1) |

## 4. Designing for information rate

The rate at which a candidate earns evidence is bounded by its information rate. With a well-chosen loss bound it approaches it. Hence:

1. **Diversify the bet, not just the book.** A tilt of SPY's weights (for example ±50% of cap weight by signal rank) over hundreds of names has a TE of 2–4%. A 5–10 name book has 15–27%. The same signal quality (IC) then gives a far higher IR (Grinold's fundamental law: IR ≈ IC·√breadth).
2. **Declare the loss bound the design supports, and let the engine check it.** For a diversified tilt, a one-session relative loss of 5% against SPY is far beyond its history. For a 10-name book, 20% is plausible. Over 30 simulated years with t(4) shocks, L = 0.2 broke on 26% of G2-like paths but on 1.5% of tilt paths at L = 0.05 ([battery B](EPA_BATTERY.md)).
3. **Pair with twins.** Testing against a twin removes beta, sector and universe effects, which lowers TE further, and tests the mechanism rather than the market.
4. **Trade at the close, rarely.** Execution costs about 2 bps per side at the close against about 21 at the open (`cost-v3-2`). Every basis point of cost is lost evidence.
5. **Accept the price of continuous monitoring.**
   - Simulated mixture medians run a few years above the known-best-bet times. Part of the gap is the price of not knowing the best bet: about log M nats for an M-point grid. Part is the grid's top bet (0.9/L) sitting below the optimum for very low-TE designs.
   - A registered fixed-horizon look remains the fastest way to confirm one pre-specified hypothesis. The two co-exist: the e-process for allocation and monitoring, one fixed-horizon look per registered confirmatory claim.

## 5. Validation

See [EPA_BATTERY.md](EPA_BATTERY.md) for the full tables. It covers:
- **(A)** size under continuous monitoring;
- **(B)** evidence velocity, simulated against analytic;
- **(C)** a multiverse of allocation policies in two worlds (concentrated books and diversified tilts). Each world has five scenarios: null, mirage, weak edge, strong edge and decaying edge.

Unit tests:
- `tests/test_evidence_eprocess.py`;
- `tests/test_evidence_tournament.py`;
- `tests/test_evidence_velocity.py`.

## 6. Limits

- **No edge is created.** If no candidate has an edge, the paper book trails SPY by its exploration cost: 0.3–0.9% a year in the battery.
- **Evidence is slow by nature.** The table in §2 is the market's limit, not the engine's.
- **The bounded-loss assumption is an assumption.** It is checked, and a violation voids it; it is never silently repaired. Choose L from the design's mechanics: diversification and position caps.
- **Prospective data takes calendar time.** Years, not runs.
- **Prior masses are owner decisions.** A lopsided prior concentrates exploration risk.
- **The parts are known; the combination is the contribution.** The parts:
  - testing by betting (Shafer; Grünwald, de Heide & Koolen; Ramdas, Grünwald, Vovk & Shafer; Waudby-Smith & Ramdas);
  - e-BH (Wang & Ramdas);
  - universal-portfolio mixtures (Cover);
  - Kelly growth (Kelly; Breiman).

  Their use as a prospective-only, capital-weighted, open-ended discovery program is the methodological change here. Backtests are demoted to priors, and evidence is equated with earned relative wealth. This is not standard quant or analyst practice.

## 7. Next steps (each needs the owner's decision; nothing is activated by this change)

1. **Prospective tournament lane: implemented** ([TOURNAMENT_LANE.md](TOURNAMENT_LANE.md); owner-approved 2026-10-23).
   - The series `v3x-epa-tournament-v1` holds G2, three diversified tilts, equal weight and their twins.
   - It runs in its own workflow and ledger, labelled `PROSPECTIVE_EXPLORATORY` and NO_TRADE.
   - It records once the owner sets the repository variable `APEX_TOURNAMENT_LANE` to `on`; until then every run is a dry rehearsal.
   - S1 and S3 books are planned as a later cohort.
2. **Design for information rate: measured** ([DESIGN_STUDY_1.md](DESIGN_STUDY_1.md); [results](../../research-results/discovery-v3/design-study-1-2026-10-23/REVIEW.md)). On DEV, sector-, beta- and size-neutral tilts have about 42% less tracking error against their twin than cohort 0's, and shallower worst sessions. At the same alpha, evidence arrives about 3× faster. Its proposal, five neutral designs at prior mass 0.04 each, is **registered as cohort 1** ([TOURNAMENT_COHORT_1.md](TOURNAMENT_COHORT_1.md); series `v3x-epa-tournament-c1-v1`, claims against the twin only, recorded in the same workflow).
3. **Wire the SEC pipeline into prospective candidates:** Form 4 insider purchases and 8-K item 2.02 earnings timing. It is point-in-time when recorded live.
4. **Total-return data** (`adjustment="all"`) for the forward lane ([review](CODEBASE_REVIEW.md), C-4).
5. **Optional improvements:** an adaptive bettor (online Newton step) in place of the fixed grid, to cut the grid's prior penalty.

## References

- J. Ville (1939). *Étude critique de la notion de collectif.*
- J. L. Kelly (1956). A new interpretation of information rate. *Bell System Technical Journal.*
- L. Breiman (1961). Optimal gambling systems for favorable games.
- R. Grinold (1989). The fundamental law of active management. *Journal of Portfolio Management.*
- T. Cover (1991). Universal portfolios. *Mathematical Finance.*
- G. Shafer, V. Vovk (2001; 2019). *Probability and Finance: It's Only a Game!*; *Game-Theoretic Foundations for Probability and Finance.*
- G. Shafer (2021). Testing by betting: a strategy for statistical and scientific communication. *JRSS A.*
- R. Wang, A. Ramdas (2022). False discovery rate control with e-values. *JRSS B.*
- A. Ramdas, J. Ruf, M. Larsson, W. Koolen (2020). Admissible anytime-valid sequential inference must rely on nonnegative martingales. arXiv:2009.03167.
- A. Ramdas, P. Grünwald, V. Vovk, G. Shafer (2023). Game-theoretic statistics and safe anytime-valid inference. *Statistical Science.*
- P. Grünwald, R. de Heide, W. Koolen (2024). Safe testing. *JRSS B.*
- I. Waudby-Smith, A. Ramdas (2024). Estimating means of bounded random variables by betting. *JRSS B.*
