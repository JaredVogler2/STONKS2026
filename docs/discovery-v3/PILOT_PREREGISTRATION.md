# Discovery V3 pilot 1: preregistration (plan `v3-pilot-1`)

| | |
|---|---|
| Machine copy | `apex_worker/discovery/v3/plans.py::PILOT_1`. `apex research discovery plans` prints its sha256; the run's `manifest.json` must show the same value |
| Plan sha256 | `11d0af37c60b46b9393fcffb7b01e0f5ad77ee34fd35f20d92d42524635ae2c7` |
| Registered | 2026-10-22, before any V3 outcome on market data existed. The engine smoke ran on the synthetic fixture only |
| Data | Alpaca historical SIP daily bars, split-adjusted. U1 (survivorship-limited). Protocol-2's own download, quality and quarantine path |
| Window | protocol-2 DEV decisions 2019-10-23 → data end − 22 sessions (**DEV_CONTAMINATED**). PRESAMPLE is never touched |
| Objective | `obj-v3-1` ([PORTFOLIO_OBJECTIVE.md](PORTFOLIO_OBJECTIVE.md)): annualised log growth minus the investable alternative, net of 25 bps per side (5 bps for SPY-only agents), pessimistic fills |
| Multiplicity | Holm over **all 10 trials** (primaries and ablations) |
| Best possible result | `PROMISING_EXPLORATORY`: a nomination for a separately registered prospective shadow, never evidence of a future edge |
| Aggregate | NO_TRADE under every outcome |

## Why these four families

The [baseline audit](BASELINE_AUDIT.md) found that S1 has book-level selection value over matched controls, yet every protocol-2 swing book (strategies and controls alike) loses heavily to SPY at the same exposure. Two drags explain it:
- **the wrapper:** stops, 20-session exits, and costs on high turnover;
- **idle cash:** about half the equity sits uninvested.

The pilot therefore tests **portfolio-level mechanisms against the investable alternative**, not new indicators. The families are mechanistically distinct:

| Family | Hypothesis id | Primary policy | Ablations (one axis each) | Comparator |
|---|---|---|---|---|
| F1 leader continuation | `f1-leader-idle-capital` | S1 E0X1 selection, cash-idle → **SPY-idle** book | (a) idle CASH (the protocol-2 book); (b) SPY-idle with a 40-session horizon | SPY (ablation a: exposure-matched SPY/cash) |
| F5 cross-sectional momentum | `f5-xs-momentum-monthly` | top 10 by 60-session return, equal weight, rebalanced every 20 sessions | (a) **random** 10 eligible members, same schedule (negative control for survivorship and equal weight); (b) cash in risk-off / volatility-shock regimes | SPY |
| F2 opportunity state | `f2-dispersion-gated-swing` | momentum swing trades (top 5 by 60-session return, every 5th session, E0X1), SPY-idle, **only when cross-sectional dispersion ≥ its 60th trailing-year percentile** | (a) the same without the gate | SPY |
| F9 regime abstention | `f9-regime-abstention` | SPY, cash while the online regime label is risk-off or a volatility shock | (a) abstain only in volatility shocks | SPY |

For each family's mechanism, competing explanation, null, falsification test and negative controls, see `plans.py`; this table summarises them.

**Falsification in one line each:**

| Family | Falsified if |
|---|---|
| F1 | active log growth versus SPY ≤ 0, or no advantage over the matched-control books under the same allocation |
| F5 | active versus SPY ≤ 0, or the random equal-weight ablation does as well |
| F2 | active versus SPY ≤ 0, or the ungated ablation does as well |
| F9 | active versus SPY ≤ 0 (drawdown and utilities are reported, but growth is the registered principal) |

## Registered rules (no change after the run)

**Engine self-checks.** The look-ahead trap must be detected and the placebo must stay quiet. Otherwise every trial is `SIMULATOR_INVALID`.

**Dispositions per trial:**

| Disposition | When |
|---|---|
| `REJECTED_DEV` | a constraint is violated, or active ≤ 0 |
| `PROMISING_EXPLORATORY` | active ≥ +1 pp a year **and** Holm p (10 trials) < 0.05 |
| `INCONCLUSIVE_DEV` | otherwise |

**Reported with every trial (descriptive):**
- zero-cost, 10 bps and 50 bps books;
- the matched-control books;
- the attribution (market, selection, friction, cash drag, residual);
- the scenario utilities;
- the Pareto frontier.

None of these changes a disposition.

**Power (measured before the run; [STATISTICAL_SIZE_POWER.md](STATISTICAL_SIZE_POWER.md)).** The principal test cannot detect a +1 pp a year edge on DEV. A null result here is **uninformative about small edges**. It can only reject large claims or flag large effects for prospective testing.

## Execution and publication

**Run.** Dispatch *Actions → apex-discovery-v3* on `main` with:
- `plan = v3-pilot-1`;
- `experiment_id = v3-pilot-1-<date>`.

The run needs the read-only Alpaca secrets of the `real-equities-research` environment.

**Publication.** A reviewed PR copies the six aggregate files into `research-results/discovery-v3/<experiment_id>/` and adds every trial to [TRIAL_INDEX.md](../../research-results/discovery-v3/TRIAL_INDEX.md), losers included.

**A second look** at these trials on the same data returns the same trial ids, and the registry counts the look. A changed plan is a new plan id.
