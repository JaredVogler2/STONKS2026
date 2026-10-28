# Personal $1000 method: Discord alerts for manual trading

> **Personal and unvalidated. Not investment advice.** A bot in **your own private Discord channel** suggests
> whole-share trades for a $1000 account. **You place every trade yourself** (at Ally Invest or any broker) and tell
> the bot what you did. Nothing here places, routes or simulates an order with a broker.

**The method** is whichever one the pre-registered studies adopt (`PERSONAL_METHOD` in `personal/runner.py`). Each alert shows its status.

**Current method: `sso-trend200`, adopted by [`personal-study-2`](../research-results/discovery-v3/personal-study-2-2026-10-28/REVIEW.md).**

- **The rule:** hold **SSO** (a 2× daily S&P 500 fund) while SPY closes above its 200-day average; otherwise hold **BIL** (1–3 month T-bills). This is the published rule from Gayed & Bilello (2016), with nothing tuned.
- **When it trades:** the bot checks every close and alerts you only when the signal flips. At month-end it also tells you to put idle cash to work.
- **Backtest:** on real Alpaca data from Oct 2019 to Sept 2026, with dividends on both sides, $1000 became **$3,461 against $2,841 in SPY**: +19.7% a year against +16.3%. The worst drop was −35.8% against SPY's −33.8%. It was ahead of SPY in 58% of rolling 12-month windows, and it trailed SPY in 2020, 2022, 2023 and 2025.
- **Caveat:** that edge is **not statistically significant**, and the rule family was picked after the first study failed, by someone who knew those years.

**Leveraged-fund risks you take on:**

- **Daily reset:** SSO moves about twice the index *each day*, so a bad day costs twice as much. Choppy markets also wear it down.
- **The filter acts late:** it acts at the next open after a close below the average, not during the day.
- **Taxes:** each switch realizes a gain or loss.
- **Ally:** you may need to acknowledge Ally's leveraged-ETF disclosure before buying SSO.

**Earlier result:** [`personal-study-1`](../research-results/discovery-v3/personal-study-1-2026-10-27/REVIEW.md) found that the monthly sector-ETF rotation did **not** beat SPY (−4.0% a year).

**Compared with ten more methods** ([`personal-study-3b`](../research-results/discovery-v3/personal-study-3b-2026-10-28/REVIEW.md), 2017–2026 on real funds; [`personal-study-4`](../research-results/discovery-v3/personal-study-4-2026-10-28/REVIEW.md), 1927–2026 rebuilt):
- **No method passed every test on 2017–2026.**
  - Only three beat SPY at all: 2× buy-and-hold (+6.8% a year, but a −59% worst drop), the momentum ETF (+1.4%), and this method (+1.4%, drop −36% against SPY's −34%).
  - Seven lost to SPY: the defensive, calendar and mean-reversion rules.
- **Over a century, this method is the only one that held up:** +4.4% a year over the market, ahead in 8 of 9 decades, worst drop −68% against the market's −84%. 2× buy-and-hold beat the market too, but lost 98% in 1929–32.
- **The choice of direction is yours.** The alerts keep this method unless you pick another (`PERSONAL_METHOD`).

History on old data is a diagnostic only. **The live record against SPY bought with the same cash is the real test.**

## What the bot does

| When | What you get |
|---|---|
| After `start 1000` | **Starting allocation**: what to buy now, in whole shares, sized on the latest closes |
| A trend method: any close where its signal flips (about 6 PM ET) | **Signal change**: sell what you hold, buy the signal's asset, with the signal in words (e.g. "SPY is 3.1% below its 200-day average") |
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
2. **Invite it to your server.** Copy the **Application ID** from **General Information**. Open this link with that id in place of `APP_ID`, then pick your server:
   `https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot&permissions=68608`
   The number 68608 grants exactly **View Channels**, **Send Messages** and **Read Message History**. You can build the same link in **OAuth2 → URL Generator**.
3. **Make a private channel** (for example `#my-trades`). Only you and the bot should be able to see it: open the channel settings, then **Permissions**, and add the bot with the three permissions above.
4. **Copy the ids.**
   - Turn on Discord **Developer Mode**: User Settings → Advanced.
   - Right-click the channel and choose **Copy Channel ID**.
   - Optionally, right-click your own name and choose **Copy User ID**. With it, only your messages count.
5. **Add two secrets on GitHub.** In the repository **Settings → Environments**, open `real-equities-research`. It already holds your Alpaca data keys, `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`; only market data is read with them. Add these **environment secrets**:
   - `DISCORD_BOT_TOKEN`: the token from step 1;
   - `APEX_PERSONAL_CHANNEL_ID`: the channel id;
   - optionally `APEX_PERSONAL_USER_IDS`: your user id; separate several ids with commas.
6. **Check the setup.**
   - Go to **Actions → apex-personal → Run workflow** and pick mode **dry**. A dry run posts nothing. It reads the channel and Alpaca, and the log ends with a line of counts. `"market_data": true` means Alpaca works; the run reaching that line at all means Discord works.
   - Then run it once in mode **live**. The bot posts its welcome in your channel.
7. **Turn on the schedule.** In **Settings → Secrets and variables → Actions → Variables**, add the repository variable `APEX_PERSONAL_ALERTS` with the value `on`. To turn the daily summaries off, also add `APEX_PERSONAL_DAILY_SUMMARY` = `off`.
8. **Start.** In the channel, send `start 1000` once the money is in your account.

## If a run fails

The Actions log shows one line, such as `personal run failed: DiscordError: Discord HTTP 403 (code 50001)`. It never contains message text.

| Line | Usual cause |
|---|---|
| `BLOCKED` / `… not set` | A secret is missing from the `real-equities-research` environment, or its name is misspelled |
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
