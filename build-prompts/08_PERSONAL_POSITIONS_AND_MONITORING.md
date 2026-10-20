# PHASE 08 — Per-user entry, lots, adds, sells and individualized position monitoring

## Product behavior

Ten people can see ONE shared trade signal. Each clicks a button or slash command to privately record THEIR actual fill price and amount; APEX monitors all users independently using a SHARED cached symbol quote, evaluates position-specific risk/target/time, and sends private updates. User-supplied fills are **self-reported**, not proof of Robinhood execution or broker integration.

## Ledger, identity and permission model

- Each Discord user has an internal UUID, verified guild membership and separate personal ledger. The bot authenticates Discord user through actual signed/Gateway event context, not user-entered IDs.
- `PositionAccount` stores self-reported brokerage/paper label, optional private hypothetical budget; never require live broker login or API token.
- `PositionLot` for each buy fill: symbol, originating `signal_id` nullable for manually added position, fill_time, fill_price, quantity, fees, side, idempotency key, notes. Support fractional shares and fees.
- `PositionEvent` immutable: OPEN, ADD, PARTIAL_EXIT, CLOSE, CORRECT, CANCEL_CORRECTION. Use FIFO cost accounting by default (configurable and documented); maintain audit for each change; refuse selling more shares than remain.
- Position ID generation server-side; multiple buys never silently overwrite initial cost. Derive average cost/realized/unrealized separately.
- Signals are group research; members may have bought outside the suggested entry. Mark "outside signal entry range" and reassess, not pretend their cost basis matches.
- A user can be long after published signal closes; user chooses whether to mark position closed, alerts may switch to unsupported/manual watch with clear status.

## Discord UX (must be executable and private)

- Shared alert embeds show `Record My Entry`, `Watch`, `View Thesis` and `Ignore`.
- Clicking entry opens modal or slash dialog for price, quantity, optional fill timestamp and optional private notes. Validate date (no impossible future fills), decimal >0, quantity >0 and originating signal ID linked to actual published signal.
- Ephemeral confirmation includes estimated dollar notional, average cost, risk invalidation, as-of market-data age and privacy notice; no public trade details.
- `/apex_positions` shows only user's positions. `/apex_add`, `/apex_reduce`, `/apex_close`, `/apex_correct`, `/apex_unwatch`, `/apex_notifications`, `/apex_help`.
- Users can opt in/out of persistent private DMs; if DMs blocked, send private on next slash-command response and preserve pending notifications; do not dump P&L into group channel.
- Avoid Discord channel spam: notification tiers, deadband hysteresis, changes-of-state instead of constant repetitive updates, user-specific cooldown.

## Position risk methodology

At each fresh verified quote:
1. Determine cost basis, quantity, exposure, realized and hypothetical unrealized P&L (fees accounted).
2. Locate original signal price stop, target, deadline and state; validate against own entry, fill recency and sector regime.
3. Compute original thesis survival features and **marginal expected gain/risk from CURRENT price**, not anchored solely to entry.
4. Categorize risk: invalidation triggered, price discovery gap, target nearing, target breached, time-to-expiry, higher-than-entry drawdown, volatility regime shock, earnings uncertainty, dilution/catalyst reversal, concentration.
5. Generate a **position assessment**: HOLD/REVIEW RISK/CONSIDER REDUCING/CONSIDER EXIT/ADD ELIGIBLE only with deterministic reasons, linked source and uncertainty. “Sell now” and “buy more” actionable personalized language gated by compliance mode.
6. For ADD: require NEW qualifying setup or improved calibrated marginal expectancy, no unresolved thesis invalidation, available user risk budget, max exposure, liquidity, no near earnings unpriced risk, and meaningful upside relative to stop. **Never add solely because losing**.
7. For reducing/exiting: expected incremental upside vs expected downside after costs; adverse regime; stop breach/gap; nearing/max 20-session hold unless explicit reassessment; consider user's hypothetical risk tolerance only with explicit input.
8. Show original vs revised scenario; correction of facts never quietly rewrites previous assessment.
9. Position entry outside range or entered days late: issue “OFF-PLAN ENTRY: reassessment required” and suppress original success-probability label as no longer applicable.

## Monitoring schedules and financial honesty

- Priority symbols with open positions get higher IEX monitoring allocation; every 5–15 minutes, evaluate state; urgent verified invalidation event may be sooner depending actual quote.
- Feed outage -> mark all affected positions as stale; send a single risk-of-staleness message after configured SLA, not a blind exit recommendation.
- Earnings unknown -> risk marked UNKNOWN, not safe.
- Price targets are NOT guaranteed fills; limit up/down/halts and slippage apply.
- Compute time horizon by NYSE sessions, not raw elapsed calendar days. Track signal entry wait separately from time in user's trade.

## Architecture and scale

Symbol quote fan-out: one shared `quote_event` → per-user evaluation partition. Transactional outbox keyed `user_id + position_id + event_type + assessment_version`. A bulk shared alert is a separate event from personalized updates. Re-evaluate 50 users × same symbol without 50 market-data calls.

Back-end RLS tests and concurrent fill idempotency. Discord Gateway persists user commands to authenticated server API and uses durable local spool if cloud temporarily unavailable. Never acknowledge permanent success if cloud has not persisted; instead indicate "queued pending sync" clearly.

## Required scenario tests

- Member A fills NVDA at $180, B at $190, C adds at $177: individualized returns, invalidation and marginal upside.
- B cannot open, view, edit or delete A's positions via REST or Discord.
- Duplicate Discord interaction nonce/idempotency key cannot create second fill.
- Two buys, partial exit, fee deduction, later reopen, correction audit.
- Closing position does not erase original signal from model success denominator.
- Overnight gap below stop and feed stale: alert calibrated uncertainty, no false fill assumption.
- Five users opt-out of DMs; queue remains private.
- Discord user leaves server: revoke ability to command bot and handle retention of their own data.

## Acceptance

Full replay proves one shared source symbol drives independent per-member position decisions and Discord private response/delivery. Member's position and P&L never leaks into general channels, code logs or another member's API response.