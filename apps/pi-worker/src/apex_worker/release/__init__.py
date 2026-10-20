"""CI-side release tooling (laptop-free cloud training amendment, 2026-10-20).

Used by the GitHub Actions workflows in the protected `model-release` environment: a small HMAC-signed client for the
release API (`/api/v1/release/*`, `rel_*` identity), candidate upload, training-run reports, promotion/rollback, the
Actions-minutes budget gate, schedule/SLA arithmetic and the lightweight drift diagnostic. Nothing here runs on the Pi's
hot path and nothing here ever prints or persists a secret.
"""
