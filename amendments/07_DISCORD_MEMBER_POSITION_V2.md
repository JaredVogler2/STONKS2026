# Amendment 07 — Discord Multi-member Portfolio Lifecycle and Individualized Risk Logic

## Mission

Deliver the intended group experience: shared discoveries in a private Discord server and individual, private, independently monitored positions, including late entry, adding, trimming, closing, and honest outcome/risk language. No subscription or Robinhood login needed; members self-report fills.

## 1. Actors and workflow

- **Owner/admin:** manages server/guild allowlist, scans, models, alert routing, quotas, compliance release mode; no default unrestricted browsing of friends' private fills.
- **Member:** opts in to Discord notifications; records personal position only via authenticated interaction; can manage lots and delete/export records.
- **Market worker:** computes one symbol observation for all authorized portfolios and makes independent assessments; never treats a group alert entry as proof of member buying.
- **Position engine:** canonical Supabase lot/event ledger; Pi maintains temporary spool for outages and never falsely acknowledges permanent save when it is only queued.

Sequence:
1. APEX discovers and validates a setup; publishes one immutable signal with ID, precise source/freshness, entries/targets/stops/horizon and clear status.
2. Shared Discord embed appears with `Record My Entry`, `Watch`, `Why?` buttons, not a literal request to publicly type fills.
3. Clicking Entry opens modal for price, shares, fill date, optional broker/paper tag and notes; responses are ephemeral.
4. Supabase records authorized immutable `BUY` lot, idempotent by interaction/event ID, returns private receipt with position ID and statement “self-reported, not connected to Robinhood.”
5. Periodically, one symbol quote fans out to independent member assessments. Members opt-in to private DMs; blocked DMs queue for private retrieval.
6. Partial sale/add/close/correct updates event journal and P&L without changing the original published signal's performance denominator.
7. Expiration/invalidation/target is published as a shared research state; a member still holding receives separate private context, not an assumed broker execution.

## 2. Detailed ledger semantics

- Per-account lot-level decimal price/quantity/fees with UTC fill timestamp and America/New_York display.
- FIFO allocations by default, idempotency key bound to verified actor and original signal; validate realized P&L, cost basis including fees, mixed fractional share fills, repeated buys.
- A sell may only dispose remaining quantity. Duplicate commands replayed cannot produce duplicate order/fill. Race two simultaneous partial exits; database transaction prevents negative quantity.
- Different members may record same signal symbol at widely different prices and dates. Their forecasts, risk/reward-from-current, time remaining and realized P&L differ. Do NOT reuse the original calibrated entry probability for off-plan fills.
- Members can track outside-signal holdings as manual entries; separately labeled unsupported/research-only unless new model evaluates them properly.
- Corporate action/split support: adjust derived position quantities/costs only with verified effective date; preserve immutable original fill record; dividends only if sourced/modeled.
- Correction and deletion need two-step private confirmation, audit event and necessary retention policy.
- Source quote is estimated market P&L, never execution-confirmed sale; gap, spread, halt and last-trade anomalies visible.

## 3. Individualized assessment policy

Supported risk state labels in paper/research mode:
- `THESIS_INTACT`
- `STOP/INVALIDATION_RISK`
- `TARGET_TOUCHED_FILL_UNCONFIRMED`
- `TIME_LIMIT_REVIEW`
- `CATALYST_REGIME_CHANGE`
- `PRICE_STALE_CHECK_BROKER`
- `ADD_CRITERIA_REVIEW` (only if independent fresh setup + risk budget/remaining upside; not averaging down solely due to red P&L).

A position decision uses CURRENT marginal expected return and downside for THAT user, not simply original entry P&L; plus signal version, age, support, market/sector stress, earnings event risk, and maximum 20 NYSE sessions unless re-evaluated as new setup. Distinguish stop “price touched” from guaranteed executable stop fill. Gate all actionable personalized buy/add/trim/sell instruction wording until applicable compliance release review; educational/private state assessments may be available within allowed scope.

Hysteresis and cooldown: only notify for new material state/risk threshold crossed, not every price tick. Urgent invalidation can bypass cooldown after confirmation. Source stale should produce one appropriate status notice, not spam every minute.

## 4. User scenarios to test

A: 20 shares at $180, later add 5 at $186, sell 12 at $195; lot allocations and realized/unrealized correctly computed.  
B: 10 shares at $193 outside original range; system warns original target probability is not transferable to B's entry.  
C: watches, never buys; no personal P&L invented.  
D: buys 3.5 fractional at $176 and closes after session deadline; quantity and fees exact.  
E: blocks DMs; state remains in database pending private fetch, no group leak.  
F: leaves Discord server; command and private delivery revoked with retention policy.  
G: clicks ancient/forged `Record My Entry` ID; reject expired/not genuine signal.  
H: two users race editing same guessed lot ID; only authenticated owner can mutate.  
I: signal invalidate before A exits; A continues as manual tracked position with appropriate uncertainty.

## 5. Discord reliability specifics

- Gateway bot on Pi with proper intents and least privilege. Interaction initial acknowledgment/defer promptly; expire/edit safe.
- Shared alert embed text uses canonical event payload; no fake percent chance, actual quote source/age, contradictory thesis evidence and one clear horizon definition.
- Outbox per recipient and event-version, retry/429 with backoff and dedupe after bot/Pi restart.
- Failed DMs must not fall back to public or another user's channel.
- Channel privacy policy and role pings opt-in; admin summary includes counts but not default exposure of personal shares/entry prices.
- Rate-limit abuse and oversized text/modal injection sanitized.

## 6. Acceptance metrics

Privacy violations 0/complete test suite; duplicate fill rate 0 under retries; negative remaining shares 0; precision errors 0 vs independent decimal test vectors; shared-symbol market requests 1 per actual fetch cycle regardless of N members; all private alerts traceable to observation/as-of and position version; Discord iPhone usability demonstrated on recorded session. No order execution or brokerage scraping.