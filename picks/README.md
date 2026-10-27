# APEX live picks

> **Research only. Not investment advice.** This folder records what the APEX live engine *would* pick, before anyone
> knows how the pick turns out. The engine is **unvalidated**: every real-data study so far ended NO_TRADE
> ([research-results/README.md](../research-results/README.md)). Nothing here is traded, posted to Discord or alerted.

The record answers one question honestly: **going forward, do the app's picks beat comparable stocks and the market,
after costs?** Until it has an answer, treat every pick as an untested idea.

## Where the record is

| What | Where |
|---|---|
| **Track record**: the latest pick, open picks, every labelled pick against its controls and SPY | [`picks/` on the `picks-ledger` branch](https://github.com/JaredVogler2/STONKS2026/tree/picks-ledger/picks): `README.md`, `picks.csv`, `sessions.csv`, `ledger.jsonl` |
| **Ledger**: the record itself, one hash-chained comment per record, timestamped by GitHub | the issue "APEX forward-shadow ledger · v3x-live-picks-v1 · vol 1" |
| Code | `apps/pi-worker/src/apex_worker/discovery/v3/live_picks.py` (decisions and labels), `research/forward/picks_lane.py` (the lane), `research/forward/picks_view.py` (the track record) |
| Workflow | `.github/workflows/apex-live-picks.yml` |

**Why a separate branch.** Workflows never push to `main`. The publish job may write one thing: `picks/` on the
`picks-ledger` branch, which holds nothing else. That folder is a view: every run regenerates it from the verified
ledger, so anyone can rebuild it and compare.

## How a session is decided

After each NYSE close, the workflow runs at 19:23 New York time, with a retry at 22:53. A decision counts only if GitHub
stores it before the next open.

1. **Data.** Alpaca historical SIP daily bars, split-adjusted, through the session t: the S&P 500 snapshot (U1), SPY and
   the 11 sector ETFs. If bar t is not final yet, the run retries.
2. **Earnings.** The SEC filings of every U1 stock (8-K item 2.02) give its recent releases and an estimated next one.
   Only filings accepted by the decision instant (the close + 15 minutes) count. A setup whose 20-session hold overlaps
   a release window is vetoed. Missing data stays UNKNOWN.
3. **The live engine, unchanged.** The Pi worker's end-of-day path with its default configuration:
   - features over the last 320 sessions;
   - the candidate engine:
     - gates: price at least $10, liquidity, history, not over-extended, the risk-off breaker, earnings;
     - the enabled H1–H7 triggers (H3 and H7 need a catalyst feed and stay silent, as on the worker);
     - quality and sector crowding;
   - the signal policy: levels, caps and tiers.

   No model is loaded, so every setup is UNVALIDATED and only research setups exist: quality at least 75, **one a
   session**.
4. **Open picks count.** A pick still open when the worker would scan counts toward the policy's caps, just as a
   published signal does. The same stock is not picked again while it is open.
5. **Recorded before any outcome exists:**
   - the **pick**, or NO PICK with the reason;
   - its plan in percent of the close: entry zone, stop, target, net reward/risk;
   - up to four **qualified setups**: what the engine would also post if it posted up to five a session. They are never
     called picks; they add sample size for the engine's top setups;
   - an ordered reserve of **matched controls** per plan.

The record is the same pick the worker posts in group mode on the same bars. `tests/test_live_picks.py` checks this
against the worker's own `end_of_day`.

## How a pick is labelled

The engine's own lifecycle rules are used, the same ones `signals/monitor.py` applies to published signals.

- **Entry:** a limit order over the entry zone, valid for the next 2 sessions.
  - If the stock opens inside the zone, it fills at the open.
  - If it opens above the zone and trades down to it, it fills at the top of the zone.
  - If it opens below the zone, it does not fill (the plan never buys weakness).
- **Exit:**
  - at the stop or the target;
  - at the open, if the stock gaps through either;
  - at the close of the 20th session after the fill otherwise.
  - A bar that touches both counts as a stop. Costs are 10 bps a side.
- **Per-pick return:** a pick that never fills counts as 0. No capital was deployed.
- **Controls:** up to 10 stocks of the same session, drawn before any outcome exists.
  - They come from the same sector and volatility tercile when there are enough.
  - Each gets the pick's stop distance in ATR and the same rules.
  - They answer: would a comparable stock with the same plan have done as well?
- **Benchmarks:** SPY and the sector ETF, from the entry session's open to the exit session's close.
- **When:** once the 22 sessions after t are final, about five weeks after the pick.

Prices and volumes are never recorded, only plan distances and returns.

## How to read it

- **vs controls** measures the engine's *selection*: did it pick better than chance among comparable stocks? **vs SPY**
  measures whether the pick beat the market over its hold.
- **Too few picks** means fewer than 30 labelled. Below that, the numbers move a lot with each new label. Even after,
  they are descriptive, not a validated result: a few good months can be luck, and so can a few bad ones.
- **Engine changes.** The series pins the engine's behaviour with a fingerprint of its decisions on a synthetic
  fixture. Any change to the strategies, gates, quality, policy or levels stops the series with INTEGRITY. An improved
  engine starts a new series (`v3x-live-picks-v2`); v1 stays as it was, so no track record is ever rewritten.

## Limitations

- **Universe.** The Pi worker scans the 400 most liquid listed names, stocks and ETFs. This record scans U1, today's
  S&P 500, where most of them are, plus the sector ETFs.
- **No catalyst feed.** The worker has none either, so H3 and H7 never fire.
- **Daily bars only.** When a bar touches both the stop and the target, the stop counts. Fills at the open or at the
  limit are approximations. The 10 bps a side match the measured cost of open entry and close exit (about 10.5 bps,
  `cost-study-1`). A thin stock's open can cost more.
- **No position sizing and no portfolio.** Each pick is scored on its own.

## Operation (owner)

| | |
|---|---|
| **Start** | Set the repository variable `APEX_PICKS_LANE` to `on`: Settings → Secrets and variables → Actions → Variables. Until then every run is a dry run: it records nothing to the ledger and publishes nothing to the branch (its job summary shows what it would have recorded). Decisions start on 2026-11-02 |
| Pause | Remove the variable. Later sessions are recorded as missed, never back-filled |
| Secrets | The Alpaca read-only keys of the `real-equities-research` environment, and the repository secret `APEX_SEC_USER_AGENT` (both already set for the other lanes) |
| Protect the branch | Recommended: a ruleset on `picks-ledger` that blocks deletion and force pushes (Settings → Rules → Rulesets) |
| Keep schedules alive | In a public repository, GitHub disables scheduled workflows after 60 days without activity. A commit to `main`, or re-enabling the workflow from the Actions tab, keeps them running |
| Verify | `apex research forward-shadow verify --lane picks --store github`, with `GITHUB_TOKEN` and `GITHUB_REPOSITORY` set |
