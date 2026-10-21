"""Real-equity historical research (build-prompts/14_REAL_EQUITIES_VALIDATION_EXECUTION.md).

Research only: this package never signs, registers, uploads or promotes a model, never calls the release API and never
places orders. It re-uses the production research building blocks (signals, levels, execution simulator, purged
walk-forward, frozen promotion-policy evaluation) on real Alpaca SIP history and writes the compact, non-licensed
summary files of `research-results/real-equities/<experiment_id>/`.
"""
