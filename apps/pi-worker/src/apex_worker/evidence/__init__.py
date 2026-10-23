"""Evidence engine: anytime-valid evidence that a candidate beats SPY, measured as the wealth of a paper portfolio.

Research only: no orders, alerts, signing or promotion. Method, proofs and limits: docs/evidence/EVIDENCE_TOURNAMENT.md.

* `eprocess`   - the betting e-process (one candidate against one numeraire), whole-path and streaming;
* `tournament` - the perpetual prospective tournament: evidence-proportional paper allocation, online claims, twins;
* `velocity`   - how long evidence takes, and the information limit that no method can beat.
"""
