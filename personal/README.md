# Personal $1000 method: Discord alerts for manual trading

> **Personal and unvalidated. Not investment advice.** A bot in **your own private Discord channel** suggests
> whole-share trades for a $1000 account. **You place every trade yourself** (at Ally Invest or any broker) and tell
> the bot what you did. Nothing here places, routes or simulates an order with a broker.

The method was chosen by a pre-registered study, [`personal-study-1`](../docs/discovery-v3/PERSONAL_STUDY_1.md):

- **Universe.** The 11 Select Sector SPDR ETFs.
- **Selection.** The top 3 by 6-month return, checked after the last close of each month. A holding stays while it ranks in the top 5.
- **Sizing.** Whole shares, a third of the account per ETF.

Its history on old data is a diagnostic only. **Its live record against SPY bought with the same cash is the real test.**

## What the bot does

| When | What you get |
|---|---|
| After `start 1000` | **Starting allocation**: what to buy now, in whole shares, sized on the latest closes |
| Last trading day of each month, after the close (about 6 PM ET) | **Month-end rebalance**. **EXIT** sells a holding that left the top 5. **TRIM** sells a holding grown above 1.5× its share of the account. **BUY** is a new pick. **ADD** tops up a holding below its share, which is where deposits go. If nothing needs to change, you get a short "no trades" check |
| Next trading morning, before the open | A **reminder** of the alert's orders you haven't reported yet |
| Every trading day after the close | A **summary**: account value, each position's P/L, cash, realized P/L, dividends, and **SPY with the same cash** (ahead or behind) |
| Within about 30 minutes of any message | A reply recording your trade or cash, or answering your question |

**Account values.** Positions are valued from Alpaca market data: daily closes, and IEX last trades while the market is open. The bot rebuilds your account from its own confirmations in the channel every run. Your positions and P/L are never stored in this repository, in the Actions logs or anywhere else.

## Talking to the bot

| You send | Meaning |
|---|---|
| `start 1000` | The account starts with $1000 cash. Send it once the money is in. |
| `bought 2 XLK at 245.10` | You bought 2 shares at $245.10 each. Also: `buy`, `add`. |
| `sold 3 XLE at 90.20` | You sold. Also: `sell`, `exit`, `trim`. |
| `… on 10/30 9:45` or `… yesterday 3:55pm` | When it happened, in Eastern time. Without it, the time is when you sent the message. |
| `deposit 500` · `withdraw 200` | Cash in or out. The SPY comparison gets the same flow. |
| `dividend XLU 3.12` | A cash dividend landed in your account. |
| `split XLK 2:1` | Your broker split the shares (2 new for 1 old). The bot also tells you when the data shows a split. |
| `skip XLK` · `skip all` | You won't take that order (or the rest) from the latest alert. |
| `undo` | Removes your last record. Use it to fix a typo, then send the corrected line. |
| `status` | Positions, P/L, cash, the SPY comparison, open orders and the next check date. |
| `plan` | What the method would do if the month ended today. This is information only: it trades at month-end. |
| `trades` · `help` | Your recent records · the command list. |

**Rules the bot applies:**

- **Whole shares only.** Ally has no fractional shares.
- **No overselling.** A sell larger than what you hold is refused.
- **Warnings, not refusals.** A price far from that day's trading range is flagged as a likely typo, and so is negative cash (a forgotten deposit?). Both are still recorded.

**Ignore the small grey line** (`apex1 {…}`) under each bot message: that is the record itself. Don't delete the bot's messages. If you delete one, the bot simply records your message again on the next run. Use `undo` instead.

## One-time setup (about 15 minutes, free)

1. **Create the bot.**
   - Go to <https://discord.com/developers/applications> and choose **New Application** (any name, e.g. "APEX personal").
   - In the **Bot** tab, choose **Reset Token** and copy the token. Keep it secret.
   - Still in the **Bot** tab, turn on **Message Content Intent**. Without it the bot can't read your replies, and it will tell you so in the channel.
   - Optionally, turn off **Public Bot** so only you can add it to servers.
2. **Invite it to your server.** In **OAuth2 → URL Generator**, tick the scope **bot** and the permissions **View Channels**, **Send Messages** and **Read Message History**. Open the generated URL and pick your server.
3. **Make a private channel** (for example `#my-trades`). Only you and the bot should be able to see it: open the channel settings, then **Permissions**, and add the bot with the three permissions above.
4. **Copy the ids.**
   - Turn on Discord **Developer Mode**: User Settings → Advanced.
   - Right-click the channel and choose **Copy Channel ID**.
   - Optionally, right-click your own name and choose **Copy User ID**. With it, only your messages count.
5. **Create the GitHub environment.** Go to the repository **Settings → Environments → New environment** and name it `personal-alerts`.
   - Under **Deployment branches**, choose **Selected branches** and add `main`.
   - Add these **environment secrets**:
     - `DISCORD_BOT_TOKEN`: the token from step 1;
     - `APEX_PERSONAL_CHANNEL_ID`: the channel id;
     - `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`: your Alpaca keys. The free market-data plan is enough, and only market data is read;
     - optionally `APEX_PERSONAL_USER_IDS`: your user id; separate several ids with commas.
6. **Check the setup.**
   - Go to **Actions → apex-personal → Run workflow** and pick mode **dry**. The log should end with a line of counts, such as `"messages_read": 0`. A dry run posts nothing.
   - Then run it once in mode **live**. The bot posts its welcome in your channel.
7. **Turn on the schedule.** In **Settings → Secrets and variables → Actions → Variables**, add the repository variable `APEX_PERSONAL_ALERTS` with the value `on`. To turn the daily summaries off, also add `APEX_PERSONAL_DAILY_SUMMARY` = `off`.
8. **Start.** In the channel, send `start 1000` once the money is in your account.

## If a run fails

The Actions log shows one line, such as `personal run failed: DiscordError: Discord HTTP 403 (code 50001)`. It never contains message text.

| Line | Usual cause |
|---|---|
| `BLOCKED` / `… not set` | A secret is missing from the `personal-alerts` environment, or its name is misspelled |
| `DiscordError: Discord HTTP 401` | The bot token is wrong or was reset: store the new one in `DISCORD_BOT_TOKEN` |
| `DiscordError: Discord HTTP 403` | The bot can't see or post in the channel: give it View Channel, Send Messages and Read Message History there |
| `DiscordError: Discord HTTP 404` | `APEX_PERSONAL_CHANNEL_ID` isn't the channel's id |
| `MarketUnavailable: AlpacaError 401/403` | The Alpaca keys are wrong or lack market data |
| `MarketUnavailable: …` (other) | Alpaca is briefly down: the bot posts one note in the channel and the next run retries |

## Good to know

- **Timing.** GitHub starts scheduled runs every 30 minutes on weekdays (7 AM–8:30 PM ET), hourly until 11 PM, and every 3 hours at weekends. A run can start a few minutes late. The month-end alert arrives the same evening, well before the next open.
- **Small-account limits.**
  - A third of $1000 is about $333. An ETF that costs more than that per share can't be bought; the alert says so, and that slot stays in cash until the account grows.
  - Expect some leftover cash after rounding to whole shares.
- **Taxes.** Monthly rotation can realize short-term gains in a taxable account. Results are pre-tax; an IRA avoids that.
- **Dividends.** Record your dividends with `dividend` so cash matches your broker. The SPY comparison includes SPY's dividends whenever the market data provides them; the summary says when it doesn't.
- **The schedule can pause.** GitHub disables scheduled workflows in a public repository after 60 days without repository activity. If the bot goes quiet, re-enable **apex-personal** in the Actions tab.
- **Privacy.**
  - The repository is public, so the workflow prints only counts: no symbol, amount, price or message text.
  - It writes no job summary and uploads no artifact.
  - Your account exists only in the private channel. Deleting the channel deletes it.
- **Cost.** Nothing: the repository is public, so Actions minutes are free; the Discord bot and the Alpaca free data plan cost nothing.

## Code

| Part | File |
|---|---|
| The method (one decision function for the backtest and the alerts) | `apps/pi-worker/src/apex_worker/personal/method.py` |
| The study's simulator | `apps/pi-worker/src/apex_worker/personal/backtest.py`, `discovery/v3/personal_study.py` |
| Commands, records, the account book (FIFO lots via `portfolio/ledger.py`) | `personal/commands.py`, `personal/records.py`, `personal/book.py` |
| A run: read the channel, answer, post what is due | `personal/runner.py`, `personal/view.py`, `personal/market.py`, `personal/discord_rest.py` |
| CLI | `apex personal run --mode dry\|live` and `apex personal smoke` (an offline rehearsal with made-up prices) |
| Workflow | `.github/workflows/apex-personal.yml` |
