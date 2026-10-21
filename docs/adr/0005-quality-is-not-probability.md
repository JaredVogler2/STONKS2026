# 0005 — Quality is a ranking, not a probability

**Context.** A single 0–100 number next to a trade idea is easily read as a win chance.

**Decision.**
- Quality ranks setups using grouped, correlation-aware evidence and coverage-aware scoring.
- Probabilities appear only from a champion model that passed the frozen out-of-sample gates and is unexpired.
- Otherwise the signal is UNVALIDATED and no probability is shown anywhere. A database CHECK constraint enforces this.
- Every surface (Discord, dashboard, docs) states that Quality is not a probability.

**Consequences.** On today's evidence (synthetic data only) every real strategy is UNVALIDATED, so APEX publishes labelled research setups or NO TRADE. That is the intended honest outcome.
