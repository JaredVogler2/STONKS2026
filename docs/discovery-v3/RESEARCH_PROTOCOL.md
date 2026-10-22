# Discovery V3 research protocol

**Status:** frozen in PR-B for the V3 engine. Pilot hypotheses are registered separately (PR-C, `PILOT_PREREGISTRATION.md`) **before** their run.

## 1. Data windows and contamination

| Window | Role for V3 | Best evidence tier |
|---|---|---|
| 2016-01-04 … 2017-01-02 | warm-up | — |
| 2017-01-03 … 2019-09-20 (protocol-2 PRESAMPLE) | **not used by V3**: evaluated and exposed (A0) | — |
| 2019-10-23 … data end − 22 sessions (protocol-2 DEV) | V3 DEV: development and diagnostics | `DEV_DIAGNOSTIC` |
| from 2026-10-23 (prospective) | forward shadow: frozen lane `qv2a1-frozen-v1`, plus V3 exploratory series once registered | `PROSPECTIVE_EXPLORATORY` (exploratory) / confirmatory for the frozen lane |

**Enforcement.** `contracts.EvaluationWindow` refuses to label any window before 2026-10-23 as anything but `DEV_CONTAMINATED`, and `tier_for` caps results. Holding out symbols on the same dates is robustness stress, never unseen time (SCI03).

## 2. Evidence tiers

| Tier | Meaning |
|---|---|
| `MECHANISM_PROPOSAL` | Falsifiable, with no performance evidence. Synthetic-fixture runs never exceed this tier |
| `DEV_DIAGNOSTIC` | Uses inspected 2016–2026 history. It can reject ideas or nominate them for a prospective test, never confirm them |
| `PROSPECTIVE_EXPLORATORY` | Immutable future decisions, but interim outcomes may be seen and the idea refined. It supports learning, not confirmation |
| `INDEPENDENT_CONFIRMATORY` | Registered ahead of future observation, frozen, blind until the registered look, sufficiently powered, and on licensed point-in-time sources |

## 3. Hypotheses, trials and budget

**A hypothesis** (`contracts.HypothesisSpec`) must name:
- an economic mechanism and a competing explanation;
- a null hypothesis and a falsification test;
- at least one negative control and one baseline;
- causally available inputs (otherwise it is `SOURCE_BLOCKED`);
- a fixed budget: **1 primary policy plus at most 2 single-axis ablations** (selection, entry, exit, allocation, idle capital or market filter).

**The registry** (`registry.TrialRegistry`, append-only and hash-chained) works like this:
- every trial is registered **before** the manifest is frozen and before any metric;
- each evaluation is a `LOOK`, and repeated looks are counted on the result (SCI08);
- every trial ends in exactly one `RESULT` or `ABANDON`;
- a batch with an open trial is invalid (SCI01);
- re-running the same experiment on the same data returns the same trial ids, and a source revision is visibly a new trial.

**Multiplicity:**
- Holm runs over every registered trial of the batch, and the registry keeps per-family counts for later families.
- Results are reported in `research-results/discovery-v3/TRIAL_INDEX.md`, which lists every trial, losers included.
- A pilot batch is 3–5 mechanistically distinct families: no grid search, no Bayesian tuning on contaminated history.

## 4. Engine self-checks (every batch)

- **Look-ahead trap.** A selector that reads the next 20 sessions on purpose must be detected (p < 0.001).
- **Placebo.** A random eligible stock must not be significant (p ≥ 0.01).
- **Failure.** If either check fails, every trial in the batch is `SIMULATOR_INVALID`.

## 5. Statistical size and power

V3 reuses protocol-2's block bootstrap with the A1 Student-t floor. Its measured size on production-shaped nulls is:
- 5.8–6.6% at nominal 5%;
- 1.6–1.9% at nominal 1.25%.

The commands and output are in `PROTOCOL_V2.md` §12 and `bench/qv2_a1_production_null_battery.py`. A V3-specific size and power battery on **daily active book returns** (`STATISTICAL_SIZE_POWER.md`) is a PR-C deliverable, run before the pilot's results are read. Until then, a Holm p between 0.03 and 0.05 is borderline.

## 6. Execution and outputs

**Execution:**
- **CLI.** `apex research discovery run --plan <id> --source alpaca|fixture`.
- **Real data.** The manual `apex-discovery-v3` workflow:
  - runs from `main` only, in the `real-equities-research` environment;
  - has a read-only token;
  - checks the minutes budget first;
  - has a 45-minute timeout;
  - keeps its artifact for 7 days.

**Outputs:**
- `manifest.json` (frozen before metrics);
- `registry.jsonl`;
- `results.json` (aggregates only, no prices);
- `frontier.json`;
- `TRIAL_INDEX.md`.

**Publishing.** A reviewed PR copies them into `research-results/discovery-v3/<experiment_id>/`.

**Nothing in V3 can:**
- place orders;
- send alerts;
- sign or promote models;
- enable group-actionable output.

Status transitions (`REGISTERED → DEV_TESTED → REJECTED | RESEARCH_SHORTLIST → FORWARD_SHADOW → …`) never grant trading rights.
