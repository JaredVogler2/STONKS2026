# APEX for members: a plain-English guide

APEX is a private research assistant that lives in your Discord server.

- It scans US stocks once a day after the close, plus light checks during the session.
- It posts a small number of **research notes about setups**.
- It privately keeps track of trades **you tell it you made**.

## What APEX does

- **Posts setups with a full plan.** Each one has an entry zone, a stop (where the idea is wrong), a target, a time window, the evidence for it and the evidence against it.
- **Says "no trade" when nothing qualifies.** Most days end this way, and that is a valid answer.
- **Lets you record your own trades** with slash commands or buttons. These are *self-reported*: APEX writes down what you tell it and works out your average cost and profit or loss (FIFO).
- **Sends you private updates** about your own positions (for example "near the stop", "target touched", "holding window ending"), only if you opt in to DMs.

## What APEX does NOT do

- **It never places orders.** It never connects to Robinhood or any broker. Nothing happens to your money unless you do it yourself in your own app.
- **It is not personal financial advice.** By default private updates use informational wording ("exit criteria met"), not instructions.
- **It does not see your brokerage account.** It does not know your account size or your risk tolerance.
- **It does not know anything early.** Data is free and may be delayed. Insider and congressional trades are shown when they become public, which for Congress can be up to 45 days after the trade.

## How to read an alert

| You see | It means |
|---|---|
| **Research setup (not high-conviction)** | The pattern matched, but APEX has **no tested evidence** that this kind of setup works. Treat it as something to study. |
| **Quality 80/100 (NOT a probability)** | A ranking against other setups that day. A higher number is a cleaner match, **not** a higher chance of success. |
| **UNVALIDATED: no calibrated probability** | No model has passed out-of-sample testing for this strategy, so APEX states no odds. |
| **Calibrated estimate (interval not established): P(target before stop) = 58 %** | Only shown after a model passed strict out-of-sample tests. It is the model's estimate of how often similar setups reached the target before the stop. APEX shows no ± range because it has not yet measured one honestly. It is still not a promise. |
| **Entry zone / Stop / Target** | The planned buy range, the level where the idea is invalid, and the planned profit level. Gaps can jump past a stop: **it is not a guaranteed exit price.** |
| **Quote … (age 12 min)** | Where the price came from and how old it is. Old prices are labelled; APEX does not pretend a stale price is current. |
| **DEMO** | Fictional practice data (tickers starting with `DM`). Not real stocks. |

## Recording your own trades

| Do this | Command |
|---|---|
| I bought | `/apex_entry ticker price shares` (add `signal_id:` if it was an APEX setup), or press **Record My Entry** on the alert |
| I bought more | `/apex_add position price shares` |
| I sold some | `/apex_reduce position price shares` |
| I sold everything | `/apex_close position price` |
| I made a typo | `/apex_correct …` (asks you to confirm; the original stays in your history) |
| Just watch it | `/apex_watch` or the **Watch** button |
| See my positions | `/apex_positions` (only you can see the reply) |
| Download my data | `/apex_export` |
| Delete everything about me | `/apex_delete_my_data` (asks twice) |

**Privacy.**
- Your replies are visible only to you (Discord "only you can see this").
- Other members cannot see your entries. The server owner sees only counts, never your fills.
- The database itself enforces this, not just the app.

## Private notifications

Turn them on with `/apex_notifications dms:True`; APEX sends a test DM.

If your DMs are closed, APEX **never posts your update in a public channel**. It keeps it privately and shows it the next time you use any APEX command.

You can also opt in to @mentions for high-priority alerts, or mute a ticker you don't care about.

## Words you'll see

- **Self-reported**: you typed it; APEX can't verify it with a broker.
- **FIFO**: when you sell, your oldest shares count as sold first.
- **R multiple**: profit or loss measured in units of the risk you planned. +2R means twice what you risked to the stop.
- **Regime**: a rule-based description of the market's mood (trending up, choppy, risk-off…). It describes; it does not predict.
- **No trade**: nothing passed the checks that day. That's normal.

Questions or something looks wrong? `/apex_help`, or ask the server owner.
