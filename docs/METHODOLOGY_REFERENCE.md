# APEX methodology reference: stock picking and price levels (code-accurate)

**Snapshot: commit 18224ee (2026-10-21). Code wins over docs; DOC≠CODE marks disagreements.**

All paths are relative to `apps/pi-worker/src/apex_worker/` unless they start with `docs/` or `apps/web/`. Every `path:line` is valid at `18224ee`.

---

## 1. Data

### 1.1 Bars and adjustment

| Item | Live (Pi worker) | Research: synthetic | Research: real (CI) |
|---|---|---|---|
| Source | Alpaca `/v2/stocks/bars`, `1Day` (`market/adapters/alpaca.py:208-227`) | `demo/synthetic.generate` (`demo/synthetic.py:386`) | Alpaca through the history cache (`market/history.py:229`) |
| Adjustment | `adjustment="split"` (`runtime/engine.py:100`). Dividends are not adjusted. | `SyntheticMarket.adjusted()`: prices before the split ÷ ratio, volume × ratio (`demo/synthetic.py:116-128`) | `HistorySpec.adjustment="split"` (`market/history.py:51`) |
| Feed | `feed="sip"`, the adapter default (`alpaca.py:209`). The Protocol default says `iex` (`market/adapters/base.py:56`), but the concrete adapter wins. | n/a (`provider=replay`, `feed=demo`) | `feed="sip"` (`history.py:50`) |
| Index | NY session date (`alpaca.py:223`) | XNYS sessions (`synthetic.py:395`) | same as live |
| History window | fetch `lookback_sessions*1.6` = 512 calendar days (`engine.py:99`), then truncate to the last **320** rows (`engine.py:55,105`, `features/panel.py:67-68`) | full history, 2016-01-04 → 2026-10-16 (`research/pipeline.py:71-72`, `synthetic.py:26`) | `years=8` (`history.py:48`), at most 300 requests and 200 symbols (`history.py:51-53`) |
| Returns | price-only | price-only | price-only (`research/dataset.py:8`, `research/training_data.py:13`) |

Cache integrity (CI only):
- On an incremental fetch, the last 5 overlapping sessions are compared with relative tolerance `1e-4` (`history.py:42,52,161-167`).
- If they disagree, the symbol is re-fetched in full. Bad rows are dropped: non-finite values, `low>high`, or `close<=0` (`history.py:137`).

### 1.2 Point-in-time handling

- **Decision instant** = session close + `DECISION_LAG` of 15 min (`signals/context.py:22,31`).
- **Feature row t** uses only bars dated ≤ t:
  - every window is trailing and there is no back-fill (`features/compute.py:1-6`);
  - setup triggers read row t plus explicitly `.shift(1)` columns (`signals/strategies/library.py:68-70,133-135,227-230`).
- **Catalysts** count at session k only if `available_at <= decision_instant(k)`:
  - first visible session = `searchsorted(decisions, avail, "left")` (`context.py:59`);
  - each catalyst is counted for 5 sessions (`context.py:64`).
  - `available_at` (`catalysts/models.py:12-14,219-241`):
    - LIVE = max(first_seen, published);
    - BACKFILL = published + lag (15 min for SEC, 60 min for news).
- **Entry** is never on the decision bar. Fills are tried on sessions t+1..t+`entry_valid_sessions` (`research/execution.py:67`).
- **Earnings**:
  - Research treats every scheduled synthetic date as known in advance. The flag is set if a confirmed date lies in (t, t+20] (`dataset.py:85-96`).
  - DEMO live uses the same window (`demo/flow.py:84-100`).
  - Real live has **no earnings provider wired** (see §17).
- **Adjusted prices are adjusted "as of now"**:
  - Alpaca split adjustment rewrites history.
  - `SyntheticMarket.adjusted()` applies every split, including ones later than the replay clock (`synthetic.py:119-127`, `market/adapters/replay.py:122`).
  - So price-level filters see split information from the future (see §17).

### 1.3 Live scanner vs research

| Aspect | Live | Research |
|---|---|---|
| Scan time | close + `eod_delay_minutes=15` (`runtime/worker.py:89,450`). Fails closed if the benchmark bar for the day is missing (`worker.py:474`). | every session in the window |
| Symbols | nightly universe (top `max_symbols=400` by 20-session median $ volume; `runtime/universe_job.py:37,59`, env `APEX_UNIVERSE_MAX`, `worker.py:612`), plus SPY, the 11 SPDR sector ETFs, open-signal symbols and monitored positions (`worker.py:463-465`) | synthetic: 3 benchmarks + 11 sector ETFs + 24 stocks × 11 sectors + 12 special cases. CI: SPY + 11 ETFs + 66 fixed large caps (`training_data.py:28-54`). |
| Benchmark | `SPY` (`worker.py:56`) | `DMSPY` (`panel.py:122`) / `SPY` (`training_data.py:28`) |
| Sector map | `sectors.json` in the state dir (`worker.py:607-609`). **Nothing in the repo writes this file.** Alpaca assets carry `sector=None` (`alpaca.py:145`). | from the generator / fixed map |
| Earnings | `earnings_status=None` → always `"UNKNOWN"` (`worker.py:236-237,619-626`; `signals/candidates.py:309`) | synthetic: known dates. CI: `None` → UNKNOWN (`training_data.py:151`). |
| Catalysts | `catalysts_provider` is not passed → `[]` (`engine.py:85,115`; `worker.py:236-237`) | `[]` (`pipeline.py:56`; `training_data.py:151`) |
| Gates | default `GateConfig`, not configurable from the server (`candidates.py:253`; `engine.py:82`) | default `GateConfig` (`dataset.py:55`) |
| Policy | `ServerSettings.policy()` reads `min_quality`, `max_new_per_session`, `max_open_per_sector`, `research_setups_enabled`, `research_min_quality`, and `entry_valid_sessions` clamped to 1..5 (`worker.py:129-140`) | n/a: research applies no policy |

### 1.4 Quote validation (not used for picking; used only for intraday annotations and assessments)

`market/validation.py:33-91`:

| Level | Rule |
|---|---|
| 0, quarantined | price missing, non-finite or ≤ 0 → `INVALID_PRICE`; no timestamp → `MISSING_TIMESTAMP`; `ts > now+5 s` → `FUTURE_TIMESTAMP` (`:17,49-54`) |
| 1, quarantined | `OUT_OF_ORDER` against the last accepted timestamp (`:55-57`) |
| Quarantine flags | `HALTED`; `NEGATIVE_QUOTE`; `CROSSED_QUOTE` when `bid > ask*(1+0.0005)`; `OUTLIER_VS_REFERENCE` when the move from the last accepted (or reference) price is > **12 %** (`:18-19,60-82`) |
| Non-quarantine flags | `LOCKED_QUOTE`, `EMPTY_BOOK_SIDE`, `ZERO_VOLUME` |
| 2 | passes every check above (`:83-84`) |
| 3 | within 1 % of a consolidated reference (`:85-90`). The live caller never passes one. |

- **Decision class** (`:100-108`):
  - `UNAVAILABLE` when there is no timestamp;
  - `STALE` when the market is open and age > `fresh_sla_seconds=120` (`engine.py:56`);
  - `FRESH_VERIFIED` when level ≥ 2;
  - else `FRESH_UNVERIFIED`.
- **Cache**: quarantined or level-0 prints are not stored (`market/quote_cache.py:111-114`).

### 1.5 Synthetic DEMO market (`demo/synthetic.py`): what is planted

**Defaults.** `GENERATOR_VERSION="synthetic-market-1"`, seed `20261020`, 2019-01-02 → 2026-10-16 (`:23-26`). Research uses `stocks_per_sector=24` and `start=2016-01-04` (`pipeline.py:71-72`). The DEMO universe uses 8 per sector.

**Latent market regime.** A 5-state Markov chain that starts in `bull` (`:374-383`).
- Daily (drift, vol) per regime (`:30-36`):

  | Regime | Drift | Vol |
  |---|---|---|
  | bull | +0.0005 | 0.0085 |
  | chop | 0 | 0.0100 |
  | riskoff | −0.0010 | 0.0165 |
  | rebound | +0.0012 | 0.0145 |
  | shock | −0.0060 | 0.0330 |

- Transition matrix: `:37-46`.
  - Expected durations (sessions): bull 40, chop 22.2, riskoff 20, rebound 20, shock 2.5.
  - Stationary shares: bull .437, chop .274, riskoff .165, rebound .116, shock .007 (computed from the matrix).
  - The latent label is ground truth only and is never a feature (`:111`).

**Market factor.** `market_ret = mu + sig * vol_mult * N(0,1)`, where `vol_mult = exp(conv(N(0,0.18), ones(10)/10, mode="same"))` (`:404-405`). This is a **centred**, non-causal smoothing of the log-vol shocks.

**Sector factors.** For each of 11 sectors: `drift_t = 0.985*drift_{t-1} + N(0, 6e-5)`, plus `N(0, 0.0055)` noise (`:410-416`).
- The AR(1) drift has stationary sd ≈ 3.5e-4/day (about 8.8 %/yr) and half-life ≈ 46 sessions.
- This **plants persistent sector leadership** (rotation/momentum).

**Per-asset returns** (`:431-439`): `ret = beta*market + sector*(1.0 for stocks | 0.8 for ETFs)`.
- `DMQQQ` adds `0.6*tech + 0.2*comm`; `DMIWM` adds `N(0, 0.004)`.
- Sector ETFs and benchmarks have `idio_vol=0`.
- Stock parameters (`:214-218`):
  - `base_price ~ U(18,260)`;
  - `base_volume ~ U(1.4e6, 1.2e7)`, rounded to 1000;
  - `beta = SECTOR_BETA × U(0.75,1.3)` (sector betas at `:61-73`);
  - `idio_vol ~ U(0.010, 0.022)`.
- The first 8 names per sector draw from an RNG seeded with `DEFAULT_SEED`, not the `seed` argument (`:200,210`). Names past 8 use `_symbol_seed(DEFAULT_SEED, f"{sector}|{i}")` (`:207-208`).

**Earnings** (stocks only, not leveraged; `:445-452`):
- first date ~ U{5..59}, then every **63** sessions;
- the gap is `earn_gap ~ N(0, 0.055)` and is **zero-mean**, with no post-event drift;
- volume ×2.8 on that day;
- the dates are published in `market.earnings`.

**Planted VCP base → breakout** (stocks only; `:453-485`):
1. First base start i ~ U{60..139}. The loop runs while `i < n-45`. Base length `base_len ~ U{15..29}` over `[lo, hi)`.
2. In the base: `idio_scale *= 0.38`, and `ret = ret*0.45 + beta*mu*0.2`. The market and sector part is compressed too.
3. The high-low range is **not** compressed: it uses `spec.idio_vol` (`:498`). So ATR shrinks less than close-to-close vol.
4. Success probability uses the **latent** regime at `hi`: bull .62, chop .50, riskoff .28, rebound .58, shock .20. Then `p_up = min(0.9, p_up + 0.012*(base_len-22))` (`:460-463`), i.e. ±0.084 across the length range.
5. `run ~ U{8..17}`.
   - Success: add `U(0.004,0.008)` per day to `ret[hi:hi+run]`; volume ×`U(2.2,3.5)` on `hi..hi+2` (`:466-468`).
   - Failure: +0.008 per day on `hi, hi+1` (a fake breakout), then −`U(0.0035,0.0065)` per day for `run` days; volume ×`U(1.1,1.5)` on `hi, hi+1` (`:469-472`).
6. Next base starts at `i = hi + run + U{40..109}` (`:483`).
7. `ret += inject - inject.mean()` (`:485`). The unconditional drift is removed, so **the edge is timing only**.

**Idiosyncratic noise.** `idio_scale * t5 / sqrt(5/3)` (`:487`). Leveraged ETF: `3*market + N(0, 2e-4)` (`:489-490`).

**OHLC** (`:491-500`):
- `close = base_price*exp(cumsum(total))`.
- `open = prev_close*exp(N(0,0.0035) + 0.85*earn_gap)`. So a gap ≥ 2.5 % happens essentially only on earnings days.
- `high/low = max|min(open,close) * exp(±range*U(.3,1))`, with `range = |N(0,1)|*(0.6*idio_vol + 0.4*sig) + 0.002`.

**Volume** (`:501-509`):
- `base_volume * exp(N(0,0.28)) * (1 + 0.35*min(|total|/max(idio_vol, 0.006), 6)) * vol_surge * regime_mult`.
- `regime_mult`: bull .95, chop 1, riskoff 1.25, rebound 1.15, shock 1.8.
- Volume is therefore coupled to |return| and to the latent regime. That makes the benchmark's distribution days informative.

**Data-quality fixtures** (`:233-370`): `DMPENNY1` ($4.2), `DMTHIN1` (60k shares/day), `DMLEV3X` (leveraged), `DMOTC1`, `DMSPAC.WS` (warrant), `DMBANK.PR` (preferred), `DMDELIST1` (bars end 2024-03-15), `DMSPLIT1` (2:1 on 2023-06-12), `DMRSPLIT1` (1:10 on 2022-11-07), `DMHALT1` (rows 2026-08-10/11 removed), `DMNEW1` (listed 2026-03-02), `DMADR1` (thin ADR).
- Rounding: prices to cents; low ≥ 0.01; `vwap=(H+L+2C)/4`; `trade_count = vol/U(80,160)` (`:530-535`).

**Plainly, what it plants:**
1. Compression bases whose breakout succeeds more often in bull and rebound regimes and with longer bases. Successful breakouts carry a **2.2–3.5× volume surge on day 1**, against 1.1–1.5× for failures. On day 1 the return push is the same or larger for failures, so **volume is the observable discriminator**.
2. Persistent sector drift.
3. Regime-persistent market drift and vol, plus vol clustering.

It does **not** plant:
- post-earnings drift;
- gap continuation;
- short-term mean reversion;
- calendar effects;
- catalysts (the synthetic research catalyst list is empty).

Consequences: H1 (and the `breakout_basic` baseline), H5 (sector persistence) and, partly, momentum-style H2 can show an edge on synthetic data. H3, H4, H6, H7 and H8 have nothing to find. The docstring claim of "longer/**tighter** bases" (`:5-7,462`) is **DOC≠CODE**: only length changes `p_up`, and the compression factor is a fixed 0.38.

**Red-team planted dataset** (`research/redteam.py:19-59`, a separate table):
- n=3000, seed 99, strength 1.6.
- Logits: `logit_T = -0.9 + 1.6*(0.8*x1 + 0.6*x2)`; `logit_S = -0.2 - 0.96*x1`; `logit_E = 0`.
- Features: `squeeze_prev = -x1`, `rel_volume_1 = x2`, `quality = 50 + 10*(0.8x1 + 0.6x2) + N(0,3)`; every other MODEL_FEATURE is N(0,1) noise.
- Gross returns: TARGET +6 %, STOP −3 %, TIME N(0.4 %, 2 %).

---

## 2. Universe filters and vetoes

### 2.1 Nightly universe (live; `market/universe.py`, `runtime/universe_job.py`)

**Pre-filter on metadata** (`universe_job.py:40-42`): `status=="active"`, `tradable`, exchange in `LISTED_EXCHANGES`, instrument not in {warrant, unit, right, preferred} (`:23`), not owner-excluded, not leveraged when `exclude_leveraged` is on. Bars are fetched over `min_history*1.6+10` = 298 calendar days of SIP, in chunks of 100 (`:44,37`).

| Filter | Default | Reason code | Ref |
|---|---|---|---|
| status ≠ active | — | `INACTIVE` | `universe.py:67-68` |
| OTC or not in `LISTED_EXCHANGES` {NYSE, NASDAQ, ARCA, NYSEARCA, AMEX, NYSEAMERICAN, BATS, CBOE} | — | `NOT_LISTED_EXCHANGE` | `:17,69-70` |
| instrument ∈ {warrant, unit, right, preferred} | — | `{INSTRUMENT}_EXCLUDED` | `:71-72` |
| ETF when `include_etfs=False` | `include_etfs=True` | `ETF_EXCLUDED` | `:26,73-74` |
| leveraged | `exclude_leveraged=True` | `LEVERAGED_EXCLUDED` | `:27,75-76` |
| no bars | — | `NO_BARS` | `:78-79` |
| `len(df) < min_history_sessions` | 180 | `INSUFFICIENT_HISTORY` | `:25,82-83` |
| last close < `min_price` | $10 | `PRICE_BELOW_MIN` | `:22,84-85` |
| median(close×vol) over the last 20 < `min_median_dollar_volume` | $30 M | `LOW_DOLLAR_VOLUME` | `:23,86-87` |
| mean(vol) over the last 20 < `min_avg_volume` | 1 M shares | `LOW_SHARE_VOLUME` | `:24,88-89` |
| owner `include` | — | drops every reason except `NO_BARS` / `INACTIVE` | `:90-91` |
| owner `exclude` | — | `OWNER_EXCLUDED` | `:92-93` |

- **Ranking**: the top 400 by 20-session median $ volume are kept (`universe_job.py:54,59`).
- **Tradability** (`universe.py:47-54`): `excluded` if the owner excluded it; `user_verified` if the owner verified it; `likely_eligible` if listed, tradable and active; otherwise `unknown`.
- **Asset kind**: instrument `etf` → asset_kind `etf`, else `stock` (`universe_job.py:87`). **ETFs are scannable candidates.**

### 2.2 Candidate gates (`signals/candidates.py:88-132`, `GateConfig` `:33-44`)

| Gate | Default | Code | Effect |
|---|---|---|---|
| no metadata, or asset_kind `benchmark` | — | `NOT_TRADABLE_UNIVERSE` | VETO |
| exchange `OTC` | — | `OTC_EXCLUDED` | VETO |
| instrument ∈ `EXCLUDED_INSTRUMENTS` {warrant, preferred, unit, right} (`:30`) | — | `WARRANT_EXCLUDED` etc. | VETO |
| leveraged | — | `LEVERAGED_ETF_EXCLUDED` | VETO |
| latest bar ≠ session | — | `STALE_OR_MISSING_BAR` | VETO. Inside the loop this never fires (`latest==session` is guaranteed by `:303`); stale symbols are counted at `:303-306`. |
| close < `min_price` | 10.0 | `PRICE_BELOW_MIN` | VETO |
| `dollar_vol_med_20` < `min_dollar_volume` | 30e6 | `LOW_DOLLAR_VOLUME` | VETO |
| `avg_vol_20` < `min_avg_volume` | 1e6 | `LOW_SHARE_VOLUME` | VETO |
| `bars_available` < `min_history` | 180 | `INSUFFICIENT_HISTORY` | VETO. Live this counts rows in the 320-row panel. |
| `dist_sma20_atr` > `max_extension_atr` | 3.5 | `OVEREXTENDED` | VETO. NaN passes. |
| `p_risk_off + p_volatility_shock` ≥ `risk_off_breaker` | 0.6 | `RISK_OFF_CIRCUIT_BREAKER` | VETO. Missing values count as 0. |
| earnings `WITHIN_HORIZON` and `earnings_policy=="veto_within_horizon"` | default policy; horizon 20 | `EARNINGS_WITHIN_HORIZON` | VETO |
| tradability `unknown` and not `allow_unknown_tradability` | False | `TRADABILITY_UNKNOWN` | WATCH: monitored, never published |
| tradability `excluded` | — | `TRADABILITY_EXCLUDED` | VETO |
| strategy's `allowed_regimes` does not contain the regime label | per strategy | `REGIME_NOT_ALLOWED_FOR_STRATEGY` | WATCH (`:334-336`) |
| quality is None (coverage < 0.6 or setup NaN) | — | `INSUFFICIENT_EVIDENCE_COVERAGE` | WATCH (`:337-339`) |
| more than `max_per_sector` PASS candidates in one sector | 3 | `SECTOR_CROWDING` | WATCH (`:407-414`) |
| quality penalties | — | `PENALTY_<code>` | informational (`:340`) |

**Research gate** (`research/dataset.py:62-82`) is the same numeric gate, vectorised. Differences from live:
- no WATCH states (`tradability_status=="excluded"` is the only tradability check);
- no regime veto;
- no sector crowding;
- earnings veto only when the earnings dict is not None.

---

## 3. Feature factory

### 3.1 Per-symbol features (`features/compute.py:159-265`, `FEATURE_VERSION="apex-features-1"` `:21`)

Notation: c = close, o = open, h = high, l = low, v = volume, r1 = Δlog c.
- Rolling windows use `min_periods = n`; incomplete windows give NaN; ±inf becomes NaN (`:265`).
- Every value is stored as float32 (`panel.py:76`).

| Feature | Formula | Lookback | Ref |
|---|---|---|---|
| `close` | c | — | `:171` |
| `ret_{5,10,20,60,120}` | log c_t − log c_{t−n} | n | `:172-173` |
| `rs_bench_{20,60}`, `rs_sector_20` | ret_n(stock) − ret_n(benchmark or sector ETF), on the stock's index. NaN for the benchmark itself and when the sector ETF is the symbol. | n | `:175-183`; `panel.py:73-75` |
| `mtf_consistency` | share of {ret_5, ret_10, ret_20, ret_60} > 0; NaN unless all four exist | 60 | `:184-185` |
| `atr_14` | Wilder EWM (α=1/14, adjust=False, min 14) of TR = max(h−l, \|h−c₋₁\|, \|l−c₋₁\|) | 14 (EWM) | `:83-84,188-190` |
| `atr_pct_14` | atr/c | 14 | `:191` |
| `dist_sma{20,50,200}_atr` | (c − SMA_n)/atr | n | `:192-195` |
| `above_sma200` | 1[c > SMA200] (NaN before 200 bars) | 200 | `:196` |
| `sma50_slope_20` | SMA50_t / SMA50_{t−20} − 1 | 70 | `:197` |
| `trend_slope_50` | OLS slope of log c over 50 bars ÷ (atr/c) | 50 | `:87-99,198` |
| `pct_from_high_60` | c / max₆₀(c) − 1 (close-based, includes today) | 60 | `:200-201` |
| `days_since_high_{20,60}` | n−1−argmax over the trailing window of closes | n | `:117-123,202-203` |
| `efficiency_ratio_20` | \|c−c₋₂₀\| / Σ₂₀\|Δc\| | 20 | `:204-206` |
| `autocorr_60` | rolling corr(r1, r1₋₁) | 60 | `:207` |
| `clv_1` | ((c−l) − (h−c))/(h−l), NaN when h=l | 1 | `:209-211` |
| `clv_5` | mean of 5 clv | 5 | `:212` |
| `gap_1` | log o − log c₋₁ | 1 | `:213,215` |
| `overnight_ret_20`, `intraday_ret_20` | Σ₂₀ gap, Σ₂₀ log(c/o) | 20 | `:216-217` |
| `rv_{5,10,20}` | std(r1)·√252 | n | `:219-221` |
| `parkinson_20` | √(mean₂₀ ln(h/l)² / (4 ln2))·√252 | 20 | `:222-223` |
| `garman_klass_20` | √(mean₂₀[0.5 ln(h/l)² − (2ln2−1) ln(c/o)²], clipped ≥ 0)·√252 | 20 | `:224-226` |
| `vol_expansion` | rv_5/rv_20 | 20 | `:227` |
| `squeeze_pct_120` | percentile of BBwidth = 4·std₂₀(c)/SMA20 within its trailing 120 values: (rank_max−1)/(count−1); min periods max(20, 60) = 60 | 20+120 | `:102-109,228-229` |
| `range_pct_1` | (h−l)/c | 1 | `:230` |
| `dollar_vol_med_20` | median₂₀(c·v) | 20 | `:232-233` |
| `avg_vol_20` | mean₂₀(v) | 20 | `:234` |
| `rel_volume_1` | v / mean(v over t−20..t−1) | 21 | `:235-236` |
| `volume_z_1` | (ln v − μ)/σ of ln v over t−20..t−1 | 21 | `:237-240` |
| `up_down_vol_ratio_20` | Σ₂₀ v[r1>0] / Σ₂₀ v[r1<0] | 20 | `:241-243` |
| `amihud_20` | mean₂₀(\|r1\|/(c·v))·1e9 | 20 | `:244` |
| `base_length` | number of trailing lengths L ≤ 60 (ending at t, contiguous) whose close range ≤ **1.5·ATR(t)** | ≤ 60 | `:126-140,246-248` |
| `base_depth_pct` | (max−min)/max of closes over the current base | ≤ 60 | `:249-255` |
| `pivot_high_20` | max(h over t−20..t−1) | 20 | `:256-257` |
| `dist_pivot_atr` | (c − pivot)/atr | 20 | `:258` |
| `breakout_today` | 1[c > pivot_high_20] | 20 | `:259` |
| `failed_breakouts_60` | number of breakouts b whose close went back below `pivot[b]` within 3 sessions. Each is recorded on day b+3 and summed over 60 sessions. | 60 (+3) | `:143-156,260-262` |
| `bars_available` | 1..N (row count). **Live: ≤ 320.** | — | `:263` |

### 3.2 Panel and context additions

| Column | Formula | Ref |
|---|---|---|
| `xs_rank_ret_20`, `xs_rank_ret_60`, `xs_rank_rs_bench_20` | same-date percentile rank (pct=True) across **asset_kind=="stock"** symbols in the panel. ETFs get NaN. | `compute.py:268-279`; `panel.py:78-79` |
| `sector_rank_20` | same-date percentile rank of the sector ETF's `ret_20` across sector ETFs | `panel.py:82-90` |
| `sector_rank_change_20` | sector_rank − sector_rank shifted 20 | `panel.py:84,90` |
| `regime_p_{state}`, `regime_label` | from §4, joined by date | `context.py:83-86` |
| `regime_breadth` | `breadth_above_sma50` | `context.py:87` |
| `squeeze_prev` | `squeeze_pct_120.shift(1)` | `context.py:88` |
| `cat_count_5d`, `cat_positive_recent_5d`, `cat_negative_recent_5d`, `cat_positive_recent_2d`, `cat_impact_max_5d` | counts over the 5 sessions starting at a catalyst's first visible session. The 2-day variant covers sessions `k−first < 2`. Max impact; NaN if there is none. | `context.py:39-75` |

**Causality guarantee.**
- Trailing windows only; pivot and rel-volume baselines use `shift(1)`.
- Failed breakouts are recorded only when knowable, and `base_length` uses ATR(t) only.
- Same-date cross-sectional ranks and the regime frame are computed from benchmark and breadth at t.
- `tests/test_features.py` recomputes on truncated histories (`compute.py:3-6`).

### 3.3 `MODEL_FEATURES` (`research/dataset.py:30-49`; 18 inputs)

| Feature | Definition |
|---|---|
| `setup_score` | strategy setup score ∈ [−1,1] (§5) |
| `quality` | Quality 0–100 (§7) |
| `g_price`, `g_sector`, `g_volume`, `g_volatility`, `g_regime`, `g_catalyst` | Quality group scores (§7). There is no `g_catalyst` without catalysts. |
| `atr_pct_14`, `rs_bench_20`, `rel_volume_1`, `dist_sma20_atr` | §3.1 |
| `squeeze_prev` | §3.2 |
| `regime_p_trending_up`, `regime_p_risk_off` | §4 |
| `rr_net` | `Levels.reward_to_risk_net` (§8) |
| `stop_dist_atr` | (mid − stop)/atr_14 |
| `earnings_unknown` | 1.0 if the earnings status is UNKNOWN |

- **One assembler for both paths** (`signals/model_features.py`):
  - `ROW_INPUTS` (`:18-27`) are the context-row columns `atr_14, atr_pct_14, rs_bench_20, rel_volume_1, squeeze_prev, dist_sma20_atr, regime_p_trending_up, regime_p_risk_off`;
  - `row_inputs(row)` takes them unrounded, mapping NaN to None (`:31-41`);
  - `assemble(...)` builds the 18-key vector (`:44-67`).
- Research: `_row_features` → `assemble(row_inputs(row), …)` (`dataset.py:99-101`).
- Live:
  - the candidate stores `_internal.model_inputs = row_inputs(row)` (`candidates.py:373`);
  - `policy.model_row` → `assemble(...)` (`policy.py:66-76`);
  - if `model_inputs` is missing, `model_row` returns None and the forecast fails closed to UNVALIDATED (`policy.py:72-74,100-106`).
- Remaining None inputs are imputed to the training mean by the model (§12).
- Fixed in `bd3dbdf`: live `squeeze_prev` was None, and regime probabilities were rounded to 4 dp.

---

## 4. Regime classifier (`features/regime.py`, `REGIME_VERSION="regime-rules-1"` `:16`)

States: `trending_up, choppy_neutral, risk_off, rebound, volatility_shock` (`:17`).

**Breadth** (`:20-41`) is computed per session over panel stocks, and is NaN when fewer than **20** names exist:
- share with `dist_sma50_atr > 0`;
- share `above_sma200`;
- share with `days_since_high_20 == 0`.

**Distribution days** (`:44-48`): over 25 sessions, count days where benchmark `pct_change ≤ −0.2 %` and volume is above the prior day's.

**Inputs** (benchmark features; `:59-68`):

| Input | Formula |
|---|---|
| `trend` | clip(dist_sma200_atr/4, ±1) |
| `slope` | clip(sma50_slope_20·25, ±1) |
| `breadth_c` | clip((breadth_above_sma50 − 0.5)·2.5, ±1) |
| `vol` | rv_20 (annualised) |
| `vol_z` | clip((vol − 0.16)/0.10, −1.5, 3) |
| `drawdown` | 1 − c / max₁₂₀(c), min 120 |
| `ret5`, `ret10` | ret_5, ret_10 |
| `dd` | distribution days |

**Logits** (`:70-83`):

```
trending_up      = 1.6·trend + 1.2·slope + 1.2·breadth_c − 0.8·max(vol_z,0)
choppy_neutral   = 0.6 − 1.2·|trend| − 0.8·|slope| − 0.3·max(vol_z,0)
risk_off         = −1.5·trend − 1.0·slope − 1.2·breadth_c + 0.2·(dd.fillna(6) − 6) + 0.5·max(vol_z,0)
rebound          = 2.5·1[drawdown>0.07 ∧ ret10>0.02] + 0.8·clip(15·ret10, ±1) − 0.8·max(trend,0) − 1.0
volatility_shock = 2.2·clip((vol−0.32)/0.08, 0, 3) + 1.5·clip(−ret5/0.06, 0, 2) − 1.5
```

**Probabilities** (`:84-95`):
- Valid only where `dist_sma200_atr`, `rv_20` and breadth are not NaN and every logit is finite. That needs ≥ 200 benchmark bars.
- Probabilities = softmax of the logits; label = argmax; NaN when invalid.
- Live: label NaN → `"unknown"` (`candidates.py:292-298`). The weights are hand-specified, not fitted (`:3-6`).

---

## 5. Strategy plugins H1–H8 (`signals/strategies/library.py`)

**Common to all** (`:17-31,269-281`):
- version `"1.0.0"`;
- `allowed_regimes` default (`trending_up, choppy_neutral, rebound`);
- `requires_catalysts=False`, `default_enabled=True`, `weak_prior=False`;
- `setup_score` is clipped to [−1,1] and `tanh_s(x) = tanh(x/s)` (`:53-54`).

Every plugin returns `trigger`, `setup_score`, `pivot` and `invalidation_hint`. **`pivot` is never consumed by the level builder or policy**; it is only stored as `_internal.pivot` (`candidates.py:377`). Vetoes are the common gates of §2.2 plus the regime-allowed WATCH. Plugins have no extra vetoes of their own beyond their trigger conditions. Horizon is 20 sessions from fill and the entry window is 2 sessions for every plugin (§8).

### H1 `h1_vcp_breakout` — "Volatility contraction breakout" (`:63-93`; spec `:287-313`)

- **Hypothesis**: after a tight, shallow base with compressed volatility, an expansion through the pivot on above-normal participation and positive RS continues over 5–20 sessions more often than unconditional entries.
- **Failure modes**:
  - false breakout back into the base;
  - breakout on low breadth;
  - earnings gap inside the horizon;
  - IEX-only volume misstates participation.
- **Flags**: allowed = default; weak_prior False; default_enabled True.
- **Params**: the only plugin with tunable `params`: `max_squeeze=0.35, min_base=10, max_depth=0.15, min_relvol=1.3` (`:67`).
- **Entry** (all must hold, `:71-79`):
  - `breakout_today==1`;
  - `squeeze_pct_120[t−1] ≤ 0.35`;
  - `base_length[t−1] ≥ 10`;
  - `base_depth_pct[t−1] ≤ 0.15`;
  - `rel_volume_1 ≥ 1.3`;
  - `rs_bench_20 > 0`;
  - `dist_sma50_atr > 0`.
- **Score** (`:80-85`): `0.35·tanh((0.5−sq_prev)/0.25) + 0.25·tanh((relvol−1)/0.8) + 0.2·tanh((base_prev−10)/10) + 0.2·tanh(clv_1/0.6)`.
- **Pivot**: `pivot_high_20`. **Invalidation**: `close − 1.6·ATR` (`:91`).
- The `eligibility_rules` strings (`:302`) are descriptive only.

### H2 `h2_rs_pullback` — "Relative-strength pullback" (`:96-124`; spec `:314-327`)

- **Hypothesis**: 60-session cross-sectional leaders that pull back 3–11 % toward a rising SMA50 with a bullish reversal bar resume their trend.
- **Failure modes**: momentum crash on a regime flip; sector-wide distribution; the pullback deepens into a breakdown.
- **Flags**: allowed = default; weak_prior False; default_enabled True.
- **Entry** (`:102-111`):
  - `xs_rank_ret_60 ≥ 0.75`;
  - `above_sma200==1`;
  - `−0.11 ≤ pct_from_high_60 ≤ −0.03`;
  - `−1.2 ≤ dist_sma20_atr ≤ 0.3` (inclusive);
  - `sma50_slope_20 > 0`;
  - `clv_1 > 0.25`;
  - `ret_5 < 0`.
- **Score** (`:112-116`): `0.4·tanh((rank60−0.5)/0.25) + 0.3·tanh(sma50_slope_20/0.04) + 0.3·tanh((−|pull+0.06|+0.03)/0.03)`. It peaks at a 6 % pullback.
- **Pivot**: max₆₀(close). **Invalidation**: `close − 1.4·ATR` (`:122`).
- **DOC≠CODE**: the condition measures distance to **SMA20**, not SMA50.

### H3 `h3_post_event_drift` — "Post-event drift" (`:127-145`; spec `:328-339`)

- **Hypothesis**: a positive, point-in-time catalyst followed by a held gap drifts further. Entry is the next session within the zone. The spec says it is compared against a placebo event set.
- **Failure modes**: upside consumed by the gap; event misclassified; post-event fade.
- **Flags**: `requires_catalysts=True`; allowed = default; weak_prior False; default_enabled True.
- **Entry** (`:131-136`):
  - `cat_positive_recent_2d ≥ 1`;
  - `0.03 ≤ gap_1[t−1] ≤ 0.12`;
  - `close_t ≥ 0.99·close_{t−1}`;
  - `rel_volume_1[t−1] ≥ 2.0`.
- **Score** (`:137`): `0.5·tanh(gap_prev/0.05) + 0.5·tanh((cat_impact_max_5d−50)/25)`.
- **Pivot**: close_{t−1}. **Invalidation**: `close_{t−1}/exp(gap_1[t−1])·0.995` (`:143`). This is close_{t−1}·close_{t−2}/open_{t−1}·0.995, not the pre-gap close.
- **Not implemented**: the placebo comparison. **Never fires**: see §16.

### H4 `h4_oversold_recovery` — "Oversold recovery in uptrend" (`:148-168`; spec `:340-350`)

- **Hypothesis**: short-term oversold names inside a long-term uptrend with a reversal bar mean-revert toward SMA20.
- **Failure modes**: falling knife (trend change); news-driven breakdown.
- **Flags**: allowed = `trending_up, choppy_neutral` (`:348`); weak_prior False; default_enabled True.
- **Entry** (`:152-159`):
  - `above_sma200==1`;
  - `ret_5 ≤ −0.06`;
  - `dist_sma20_atr ≤ −1.5`;
  - `clv_1 ≥ 0.3`;
  - `ret_60 > −0.05`;
  - `failed_breakouts_60 ≤ 2`.
- **Score** (`:160`): `0.5·tanh(−dist_sma20_atr − 1.5) + 0.5·tanh(clv_1/0.5)`.
- **Pivot**: SMA20, unused. The target is 2R, not SMA20. **Invalidation**: `close − 1.2·ATR` (`:166`).

### H5 `h5_sector_rotation` — "Sector rotation leadership" (`:171-194`; spec `:351-361`)

- **Hypothesis**: stocks "ranking highly within a sector" whose sector rank improved sharply outperform while breadth is supportive.
- **Failure modes**: rotation reverses; crowding into one theme.
- **Flags**: allowed = default; weak_prior False; default_enabled True.
- **Entry** (`:175-181`):
  - `sector_rank_change_20 ≥ 0.3`;
  - `sector_rank_20 ≥ 0.7` (with 11 sectors, the top 4);
  - `xs_rank_ret_20 ≥ 0.65`;
  - `regime_breadth ≥ 0.5`;
  - `−0.5 ≤ dist_sma20_atr ≤ 2.0`.
- **Score** (`:182-186`): `0.4·tanh(rank_chg/0.3) + 0.3·tanh((xs_rank_20−0.5)/0.25) + 0.3·tanh((breadth−0.5)/0.2)`.
- **Pivot**: `pivot_high_20`. **Invalidation**: `close − 1.5·ATR`.
- **DOC≠CODE**: `xs_rank_ret_20` ranks across **all** stocks, not within the sector.

### H6 `h6_gap_and_hold` — "Gap and hold" (`:197-220`; spec `:362-372`)

- **Hypothesis**: gap-ups of 2.5–10 % that close in the top half of the range on heavy volume continue. Entry is in the next session's zone.
- **Failure modes**: gap fill; news reversal; spread widening at the open.
- **Flags**: allowed = default; weak_prior False; default_enabled True.
- **Entry** (`:201-207`):
  - `0.025 ≤ gap_1 ≤ 0.10`;
  - `clv_1 ≥ 0.5`;
  - `rel_volume_1 ≥ 1.8`;
  - `dist_sma50_atr > 0`.
- **Score** (`:208-212`): `0.4·tanh(clv_1/0.5) + 0.3·tanh((relvol−1.5)/1.0) + 0.3·tanh(rs_bench_20/0.05)`.
- **Pivot**: close. **Invalidation**: `close/exp(gap_1)·0.995` (`:218`).

### H7 `h7_catalyst_compression` — "Catalyst + compression concurrence" (`:223-243`; spec `:373-383`)

- **Hypothesis**: a compression base plus a fresh positive catalyst beats either alone. The docstring says it "must beat H1 alone AND catalyst alone"; that comparison is **not implemented**.
- **Failure modes**: the intersection is too rare; catalyst classification noise.
- **Flags**: `requires_catalysts=True`; allowed = default; weak_prior False; default_enabled True.
- **Entry** (`:227-234`):
  - `squeeze_pct_120[t−1] ≤ 0.4`;
  - `base_length[t−1] ≥ 8`;
  - `cat_positive_recent_5d ≥ 1`;
  - `dist_pivot_atr ≥ −0.5`;
  - `rs_bench_20 > 0`.
- **Score** (`:235`): `0.5·tanh((0.5−sq_prev)/0.25) + 0.5·tanh((impact−50)/25)`.
- **Pivot**: `pivot_high_20`. **Invalidation**: `close − 1.5·ATR`. **Never fires**: see §16.

### H8 `h8_turn_of_month` — "Turn-of-month seasonality (weak prior)" (`:246-266`; spec `:384-397`)

- **Hypothesis**: entries on the last session of the month in uptrending names capture a turn-of-month effect.
- **Failure modes**: data-mined calendar effect; the effect decayed after publication.
- **Flags**: `default_enabled=False`, `weak_prior=True`; allowed = default.
- **Entry** (`:254-257`):
  - the next session falls in a different month;
  - `above_sma200==1`;
  - `−1 ≤ dist_sma20_atr ≤ 1.5`.
- **Score**: constant **0.1** (`:258`). **Invalidation**: `close − 1.5·ATR`.
- **Not implemented**: the spec's "multiple-testing penalty". `weak_prior` is metadata only (`pipeline.py:124`), and H8 is excluded from default training (`pipeline.py:103`).

**Enabling.** `enabled_strategies(overrides)` reads the server `strategies` dict `{sid: bool}` and defaults to `default_enabled` (`:402-404`; `worker.py:274`).

---

## 6. Candidate engine (`signals/candidates.py`, `CandidateEngine.scan` `:277-419`)

1. Symbols are iterated in sorted order (`:300`).
   - If a symbol has no row for the session, a non-benchmark symbol is counted `STALE_OR_MISSING_BAR` (`:303-306`).
   - The context frame is cached per (panel.token, symbol, catalyst ids) (`:259-267`).
2. Earnings status = `earnings_status(sym, day, 20)`, or `"UNKNOWN"` if there is no provider (`:309`).
3. `gate_row` (§2.2) runs; a VETO is counted per code and the symbol is skipped (`:311-315`).
4. Symbol catalysts are those with `available_at <= asof_iso` (ISO string comparison), newest first (`:318-322`).
5. For each enabled strategy:
   - skip if `requires_catalysts` and the symbol has no catalysts;
   - otherwise evaluate the cached full-frame trigger at the session (`:323-328`);
   - Quality = `quality(row, setup, earnings)` (`:331`);
   - WATCH if the regime is not allowed or quality is None.
   - The candidate's `_internal` carries the unrounded model inputs (`:373`), pivot, invalidation hint and resistance (`:371-396`).
6. Rank by `(−(quality or −1), symbol, strategy_id)` (`:398`). Quirk: a quality of 0.0 is falsy and sorts as None.
7. **One candidate per symbol**: keep the first, i.e. the highest-quality strategy (`:400-406`). This happens **before** gate status is considered, so a higher-quality WATCH candidate (for example, regime not allowed) can hide a lower-quality PASS candidate on the same symbol.
8. Sector crowding: walking in rank order, the 4th and later PASS candidates in a sector become WATCH with `SECTOR_CROWDING` (`:407-414`). All `sector=None` names share one bucket.
9. If nothing PASSes: `NO_QUALIFIED_CANDIDATE (most common veto: X)` (`:415-418`).
10. There is no numeric cap on candidates. Evidence lists are capped at 16, reason codes at 32 (`:241,361`), and `feature_snapshots` at 12 (`:422`).

**Resistance passed to levels** (`:386-393`): `max(close over the prior 120 sessions, excluding today)` when more than 60 rows exist; otherwise NaN.

**Evidence** (`build_evidence` `:141-241`) is explanatory only; it does not affect ranking. Thresholds: RS sign; `squeeze_prev ≤ 0.4`; `rel_volume ≥ 1.3`; trend-slope sign; regime label in {trending_up, rebound}.

---

## 7. Quality score (`signals/quality.py`, `QUALITY_VERSION="quality-1"` `:15`)

Notation: `t(x, s, m) = tanh((x − m)/s)`. A member input that is NaN or None is dropped. A group is None if all its members are None. Its score is the mean of the available members (`:40-46`).

| Group | Weight (`:17-24`) | Members (`:57-99`) |
|---|---|---|
| price | 1.0 | t(trend_slope_50, 0.15); t(dist_sma50_atr, 2.0); t(xs_rank_ret_60, 0.25, 0.5); above_sma200 → ±0.6 |
| sector | 0.8 | t(rs_bench_20, 0.06); t(rs_sector_20, 0.05); t(sector_rank_20, 0.3, 0.5); t(sector_rank_change_20, 0.3) |
| volume | 0.8 | t(rel_volume_1, 0.8, 1.0); t(volume_z_1, 2.0); tanh(ln(up_down_vol_ratio_20)/0.4) if the ratio is truthy |
| volatility | 0.6 | tanh((0.5 − squeeze_prev)/0.25); t(vol_expansion, 0.5, 1.0) |
| regime | 1.0 | clip(p_up + 0.5·p_rebound − p_risk_off − 1.5·p_shock, ±1); needs all four values |
| catalyst | 0.6 | only if `cat_count_5d` > 0: clip(0.6·tanh(pos5 − neg5) + 0.4·tanh((impact − 50)/25), ±1). A missing impact counts as **0**, which gives −0.386. |

**Coverage** (`:121-125`):
- `total_w = 4.8`, minus 0.6 if the catalyst group is None.
- `coverage = Σ w(available) / total_w`.
- **Rule**: if coverage < `MIN_COVERAGE` 0.6 (`:26`), no groups are available, or the setup score is None, the score is None (`:127-128`).

**Blend** (`:129-135`):
- `composite = Σ w·s / Σ w` over the available groups;
- `blended = 0.4·setup + 0.6·composite` (`SETUP_WEIGHT` `:25`);
- `raw = (50 + 50·blended) × (0.85 + 0.15·coverage)`. With coverage ≥ 0.6 the factor lies in [0.94, 1.0];
- `raw −= Σ penalties`; then clamp to [0,100] and round to 2 dp.

**Penalties** (`:102-117`):

| Code | Rule | Effective max after gates |
|---|---|---|
| `THIN_LIQUIDITY` | 6·(1 − dv/5e7) if dv < 5e7 | 2.4 (the gate requires dv ≥ 3e7) |
| `EXTENDED_FROM_SMA20` | min(10, 4·(ext − 2.5)) if ext > 2.5 | 4.0 (the gate vetoes > 3.5) |
| `REPEATED_FAILED_BREAKOUTS` | 3·(fb − 1) if fb ≥ 2 | uncapped |
| `EARNINGS_DATE_UNKNOWN` | 5 if earnings status == UNKNOWN | 5 (**always applied live**) |
| `NEGATIVE_CATALYST` | 8 if `cat_negative_recent_5d` > 0 | 8 |

**DOC≠CODE**: `docs/RISK_MODEL.md:44-45` gives "up to 6" and "up to 10", but the gates cap these at 2.4 and 4.0.

**Implied bars** (full coverage):
- Quality ≥ 70 needs `blended ≥ 0.4`, or **≥ 0.5 live** because of the earnings −5.
- Quality ≥ 75 (research setup) needs **≥ 0.6 live**.

Sector crowding is applied in the candidate engine (§6), not here.

---

## 8. Levels (`signals/levels.py`, `LEVELS_VERSION="levels-1"`; `LevelConfig` `:22-33`)

`compute_levels(close, atr, hint, resistance, cfg, r_multiple=None)` (`:66-107`) is the one implementation used live (`policy.py:186-192`), in research (`dataset.py:152-158`) and for baselines.

### 8.1 Entry zone and validity

- `above = min(0.5·ATR, 0.02·close)`; `entry_max = round_half_up(close + above)`; `entry_min = floor(close − 0.5·ATR)` (`:76-78`).
- `mid = (entry_min + entry_max)/2`. This equals close whenever ATR ≤ 4 % of close.
- Validity: `entry_valid_sessions=2`, i.e. sessions t+1 and t+2 (`:32`). Live, this is server-configurable to 1..5 (`worker.py:131,139`).

### 8.2 Stop

1. Structural level: the strategy's `invalidation_hint`, or `mid − 1.5·ATR` if it is missing (`:80`).
2. ATR clamp: `stop = min(max(hint, mid − 3·ATR), mid − 1·ATR)` (`:81`; `stop_min_atr=1.0`, `stop_max_atr=3.0`).
3. Tick: `floor` to $0.01 (`:57-63,82`). It is always 0.01; no sub-$1 tick.
4. Reject `STOP_INVALID` if `stop ≤ 0` or `stop ≥ entry_min` (`:83-84`).

### 8.3 Target

1. `k = r_multiple or 2.0` (`:85`). **No caller passes `r_multiple`**, so the docstring's "calibrated multiple" is **DOC≠CODE**.
2. `target = mid + k·(mid − stop)` (`:86-87`).
3. **Overhead-supply cap**: if `entry_max < R < target`, then `target = R − 0.25·ATR` and `target_secondary = round(uncapped target)` (`:94-99`).
   - R = max of the **prior 120 closes**: `rolling(120, min 60).max().shift(1)` (`dataset.py:134`) or `iloc[-121:-1].max()` when more than 60 rows (`candidates.py:388-390`).
   - **DOC≠CODE**: `docs/RISK_MODEL.md:60` and the `levels.py` docstring say "120-session high". The code uses closes, not intraday highs.
4. `target = round_half_up(target)` (`:100`).
5. Reject `TARGET_NOT_ABOVE_ENTRY` if `target ≤ entry_max` (`:103-104`).
6. The secondary target is only published (`policy.py:400-401`). It is never simulated.

### 8.4 Reward/risk, horizon, rounding

- `cost = 2·10 bps·mid`; `rr = (target − mid − cost)/(mid − stop + cost)` (`:101-102`).
- Reject `REWARD_RISK_TOO_LOW` if `rr < 1.5` (`:30,105-106`). Otherwise rr is rounded to 2 dp.
- **Implications**:
  - Uncapped with k=2: `rr ≥ 1.5 ⟺ (mid − stop) ≥ 5·cost = 1 % of mid`.
  - Capped: rejected unless `R ≥ mid + 1.5·r + 0.25·ATR + 0.5 %·mid`.
  - So **any prior-120-close high sitting between the zone top and about 1.5R is fatal**. This structurally rejects most pullback setups (H2, H4), whose prior high lies 3–11 % overhead.
- **Horizon**: `horizon_max_sessions=20` (`:33`). The signal's `min_sessions: 3` is hard-coded and cosmetic (`policy.py:343`).
- **Rounding**: tick rounding at `:57-63`. Published values use `dec_str`, ROUND_HALF_UP to 2 dp (`:110-111`).

---

## 9. Signal policy and lifecycle

### 9.1 Policy (`signals/policy.py`, `POLICY_VERSION="policy-1"` `:30`, `PolicyConfig` `:33-43`)

Candidates are processed in candidate-engine rank order (`decide` `:139-259`):

| Step | Rule | Rejection code |
|---|---|---|
| 1 | `gate_status == PASS` | `GATE_<status>` plus the first 4 reasons (`:168-177`) |
| 2 | quality ≥ `min_quality` **70** | `QUALITY_BELOW_THRESHOLD` (`:178-182`) |
| 3 | no open signal on the symbol | `OPEN_SIGNAL_EXISTS` (`:183-185`) |
| 4 | `compute_levels` succeeds | `LEVELS_INPUT_MISSING` / `STOP_INVALID` / `TARGET_NOT_ABOVE_ENTRY` / `REWARD_RISK_TOO_LOW` (`:186-195`) |
| 5 | open + new signals in the sector < `max_open_per_sector` **2** | `SECTOR_EXPOSURE_CAP` (`:197-199`) |
| 6 | new signals in the sector < `max_new_per_sector` **1** | `SECTOR_DAILY_CAP` (`:200-202`) |
| 7 | published < `max_new_per_session` **3** (counts research setups too) | `SESSION_ALERT_CAP` (`:203-205`) |
| 8 | model forecast (below) | — |
| 9 | for RESEARCH_SETUP: `publish_research_setups` must be on (default True), quality ≥ `research_min_quality` **75**, and fewer than `research_max_per_session` **1** published | `RESEARCH_QUALITY_BELOW_THRESHOLD` / `RESEARCH_SESSION_CAP` (`:224-240`) |

**Sector key** (`:154-156,196`): open signals are counted under `risk.sector`, which is `"unknown"` when a name has no sector (`:391`). New candidates use `internal.get("sector") or "unknown"`. All sectorless names therefore share one bucket for both caps. Fixed in `18224ee`: new candidates were keyed `None`, so the open cap never saw sectorless names.

**Forecast** (`confidence_block` `:79-126`):
1. With no model, or a manifest that is UNVALIDATED, expired (`validation_expires_at ≤ now`, `:89-90`), or a `synthetic_fixture` outside DEMO (`:91-92`): `UNVALIDATED` with reason `MODEL_UNVALIDATED`.
2. If `model_row` is None (no `_internal.model_inputs`): `UNVALIDATED`, labelled "model input vector unavailable". This is fail-closed (`:100-106`).
3. Otherwise `p = model.predict(model_row)` (`:107`), with `threshold = float(threshold_p_target or 1.0)` (`:126`).
   - `confidence` = status, label, `p_target_first`, `p_stop_first`, `p_expire`, `sample_count = holdout_n`, model id (`:113-125`).
   - Label: "Calibrated estimate" or "EXPERIMENTAL estimate", plus " (interval not established)".
   - **No interval and no effective sample size** are published. They were removed in `bd3dbdf` (APEX-002).
4. **EV** = `p_T·(target − mid) − p_S·(mid − stop) − 2·10 bps·mid` (`:211-212`). It is in **price units**; the TIME outcome contributes 0.
5. **HIGH_CONVICTION** if (`status == CALIBRATED`, or `allow_experimental_high_conviction` (default False) and DEMO) and `p_T ≥ threshold` and `EV > 0` (`:213-215`).
6. Otherwise the tier is RESEARCH_SETUP, with reason `FORECAST_BELOW_THRESHOLD`, else `NEGATIVE_EXPECTED_VALUE`, else `MODEL_NOT_CALIBRATED` (`:216-221`). With no forecast the reason is `MODEL_UNVALIDATED` (`:222-223`).
   - **DOC≠CODE**: `docs/RISK_MODEL.md:98` says research setups carry no probability. A RESEARCH_SETUP from a non-UNVALIDATED model **does carry `p_target_first`** in `confidence`.
7. **NO TRADE**: if nothing is published, `no_trade_reason = scan reason or "NO_CANDIDATE_PASSED_POLICY"` (`:257-258`).
8. **DOC≠CODE**: `docs/RISK_MODEL.md:87` still says "expected value in R after costs". The code computes it in price units. The same line's "point estimate labelled *interval not established*" now matches the code.

**Expiry** (`:162-164`): the entry expires at the close of `add_sessions(decision_session, entry_valid_sessions)`.

### 9.2 Lifecycle (`signals/lifecycle.py`, `signals/monitor.py`)

- **States** (`lifecycle.py:42-49`): DISCOVERED → EVALUATING → QUALIFIED → PUBLISHED → ACTIVE → CLOSED. Side exits: DISCARDED, NO_TRADE, EXPIRED, INVALIDATED. Signal id = sha256(symbol|strategy|session|policy) (`:206-209`).
- **End of day** (authoritative; `monitor.py:32-145`): the signal's own levels are replayed through `research.execution.simulate` with `ExecConfig` built from the signal's cost, entry-valid and max-session values (`:46-53`).
  - **Activation on fill**: PUBLISHED → ACTIVE at the fill session's close, with payload `theoretical_entry` (`:87-103`).
  - **Unfilled after the window** (`:59-86`):
    - if min(low) over sessions t+1..t+evs ≤ stop → INVALIDATED (`THESIS_INVALIDATED`);
    - else if min(open) ≥ target → EXPIRED ("upside consumed");
    - else EXPIRED (no fill).
    - INVALIDATED is produced only for unfilled signals. ACTIVE → INVALIDATED is allowed by the state machine but never emitted.
  - **Exits**: TARGET, STOP or TIME → CLOSED with the modelled exit, R and net return (`:104-130`). `DATA_END` is not terminal.
  - `TIME_WARNING` annotation when 0 < remaining ≤ 3 (`:24,131-144`).
- **Intraday** (advisory; `:148-199`), ACTIVE only:
  - `STALE_DATA` (one per hour) when the quote is missing or older than 120 s while the market is open;
  - requires `usable_for_exit_alerts` (level ≥ 2 and not quarantined);
  - price ≤ stop → `INTRADAY_STOP_TOUCH`; price ≥ target → `INTRADAY_TARGET_TOUCH` (one per day each).

---

## 10. Research execution and outcome model (`research/execution.py`)

`ExecConfig`: `cost_bps_per_side=10`, `entry_valid_sessions=2`, `horizon_sessions=20` (`:25-29`). `slip = cost/1e4`.

**Entry** (`:67-77`): for d in t+1..t+2 (bars with NaN are skipped but still use up the window):
- `entry_min ≤ open ≤ entry_max` → fill at `open·(1+slip)`;
- `open > entry_max` and `low ≤ entry_max` → fill at `entry_max·(1+slip)`;
- `open < entry_min` → **no fill that session**, even if price later trades inside the zone;
- no fill → `NO_FILL`, or `DATA_END` if the data ends.
- **DOC≠CODE**: `docs/RISK_MODEL.md:108` and `docs/BACKTEST_REPORT.md:38` say "fills only if price trades inside the zone".

**Exit** (`:79-119`): loop d = fill .. min(n−1, fill+20).
- Fill session: **only the stop** is checked. If `low ≤ stop`, exit at `stop·(1−slip)`.
- Later sessions, in order:
  1. `open ≤ stop` → STOP at `open·(1−slip)`, `gap_through_stop`;
  2. `open ≥ target` → TARGET at `open·(1−slip)`;
  3. low ≤ stop **and** high ≥ target → **STOP** at `stop·(1−slip)`, `ambiguous_same_bar`;
  4. low ≤ stop → STOP at the stop;
  5. high ≥ target → TARGET at `target·(1−slip)`.
- Time: if neither was hit and `fill+20 ≤ n−1` → TIME at `close[fill+20]·(1−slip)`. Otherwise DATA_END at the last close, flagged `ENDED_BEFORE_HORIZON`.

**Returns** (`:120-131`):
- `return_gross = (exit/(1−slip)) / (fill/(1+slip)) − 1`;
- `return_net = exit_px/fill_px − 1`, which already includes 10 bps per side;
- `R = (exit_px − fill_px)/(fill_px − stop)`;
- MFE and MAE are measured from the fill, including the fill-day high and low.

**Evaluation costs**: gates re-price at `net_at_cost(gross, bps) = (1+g)(1−c)/(1+c) − 1` (`research/evaluate.py:41-43`), at 25 bps and on the grid {0, 10, 25, 50} (`:20,31`).

---

## 11. Dataset construction (`research/dataset.py`)

- **Decision dates** (`build_events` `:104-196`): every session t (optionally inside `[start, end]`) where the strategy trigger fires and `gate_mask` passes (`:139`).
  - **No regime filter**: `regime_allowed` is recorded but never used (`:140,169`).
  - **No min-quality filter, no per-symbol dedup, no sector or session caps.**
  - Rows are dropped only when quality is None (`:150`) or levels are rejected (`:159`).
  - Catalyst strategies are skipped if the symbol has no catalysts (`:136`).
  - There is one row per (symbol, strategy, t), so firings on consecutive days overlap.
- **Labels**: `simulate()` outcome ∈ {TARGET, STOP, TIME, NO_FILL, DATA_END} (`:161`). For training: TIME ∪ DATA_END → TIME (`research/validation.py:59`), and `y = 1[TARGET]`. Only filled rows are used (`validation.py:107`).
- **Label window**: `[entry_date, exit_date]`. Unfilled rows get `exit_date = idx[t+2]` (`:176-179`).
- **Horizons**: entry 2 sessions, then 20 from the fill. Gate earnings window (t, t+20] (`GateConfig.horizon_sessions` `candidates.py:43`). That window misses earnings at t+21..t+22 inside the true exposure window.
- **Matched baselines** (`baseline_events` `:199-271`, seed 7). They use the same `compute_levels` and `simulate`, with a fixed hint `close − 1.5·ATR` and the same 120-close resistance (`:221-227`). Gate masks are built **without** the earnings veto (`:214`).

  | Baseline | Definition |
  |---|---|
  | `random_qualified` | for each reference decision date, one uniformly random gate-passing non-benchmark symbol (`:259-260`) |
  | `momentum_20` | on each reference date, the gate-passing symbol with the highest `ret_20` (NaN → −9; ties by symbol) (`:261-262`) |
  | `breakout_basic` | every gate-passing `breakout_today==1` day per symbol, thinned to every 3rd firing (`[::3]`) (`:263-270`) |
  | `always_flat` | n=0, mean 0 (`evaluate.py:187`) |

  - The reference dates are the **first strategy's** events only, computed once and reused for every strategy (`research/pipeline.py:113-115`).
  - Baselines are summarised on the holdout window (`evaluate.py:177-187`).
- **Benchmark**: `benchmark_window_returns` (`:274-285`) is buy-and-hold from the close before the entry date to the exit close. It is **not wired**: `bench_excess` is never passed (`pipeline.py:116-118`), so `benchmark_same_windows` never appears.
- **Label definition stamped on models**: TARGET_BEFORE_STOP, 20 sessions, STOP_FIRST, 2-session entry, 10 bps (`pipeline.py:28-34`).

---

## 12. Model (`research/model.py`, `MODEL_KIND="logistic_competing_v1"` `:18`)

- **Form**: multinomial logistic regression over {TARGET, STOP, TIME} (`:19`): `P = softmax(Xs·W + b)`. Features are `MODEL_FEATURES` (§3.3).
- **Preprocessing** (`train` `:162-187`):
  - NaN → the training-column mean (or 0 if the column is entirely NaN);
  - standardise with training mean and std; std < 1e-9 → 1;
  - inference imputes with the stored means (`:95-101`).
- **Regularisation**:
  - loss = mean cross-entropy + `0.5·l2/n·‖W‖²`, with the bias unpenalised (`:39-41`);
  - `l2 = 4.0` (`:168`; `ValidationConfig.l2` at `validation.py:29`).
  - Because the penalty is scaled by 1/n, the prior strength is fixed and shrinkage weakens as n grows.
- **Fitting** (`fit_multinomial` `:28-60`):
  - W starts at 0; `b = log(class counts + 1)`, centred;
  - full-batch gradient descent, 400 iterations;
  - step starts at 0.5, ×1.25 after a success (capped at 4.0), ÷2 on backtracking down to 1e-6;
  - stops when `max|∇W| < 1e-6`. Deterministic.
- **Calibration** (`:63-81,106-117,183-186`):
  - isotonic regression (PAV, merging on ≥) of raw `P(TARGET)` against the hit indicator;
  - fitted on the last 25 % of the training rows, by row order (`validation.py:28,68`), only if there are ≥ 30 rows;
  - prediction: `np.interp` over the knots (flat outside them), clipped to [0.001, 0.999];
  - P(STOP) and P(TIME) are rescaled proportionally to sum to 1 − p_T;
  - the 75/25 fit/calibration split is **not purged**.
- **Artifact**: JSON only. The Pi checks that the feature list equals `MODEL_FEATURES` (`models/store.py:75-76`) and that the schema hash matches (`models/manifest.py:26-28,129-130`).

---

## 13. Validation (`research/validation.py`, `ValidationConfig` `:22-30`)

**Walk-forward**:

| Parameter | Value |
|---|---|
| `holdout_fraction` | 0.2 |
| `n_folds` | 4 |
| `embargo_sessions` | 5 |
| `min_train` | 150 |
| `calibration_fraction` | 0.25 |
| `l2` | 4.0 |
| `seed` | 20261020 |

1. Keep only filled events, sorted by decision date (`:107`). Fewer than 10 distinct dates → empty result (`:113-114`).
2. **Holdout**: `holdout_start = dates[int(len(dates)·0.8)]` over that strategy's distinct decision dates (`:115`), so each strategy gets its own holdout start.
3. **Folds**: `np.array_split(dev_dates, 5)`. Folds k = 1..4 test on block k and train on every dev event before block k (expanding window) (`:119-130`).
4. **Purge and embargo**: keep training events whose `exit_date` (or decision date, if unfilled) < `embargo_limit(test_start, 5)` (`_purge` `:94-98`).
   - `embargo_limit` steps back 5 **NYSE sessions** with `MarketCalendar.previous_session` (`:81-91`).
   - It falls back to `BDay` only outside the pinned calendar (`:89-90`).
   - Fixed in `fc5b8c9`: the margin was pandas `BDay`, so holidays shortened it.
5. Folds with fewer than 150 training rows are skipped (`:131`).
6. **Final model**: trained on dev, purged against the holdout start (`:157-170`), then scored once on the holdout (`:171-185`).

**Shuffle** (`:108-111`): if `shuffle_labels`, `outcome` is permuted over **all** filled events (dev and holdout) with `default_rng(seed)` before the split. Returns are not permuted. `evaluate_strategy` runs 20 shuffles with seeds 20261020+i (`evaluate.py:111-114`).

**`choose_threshold`** (`evaluate.py:46-58`):
- grid `0.20, 0.22, …, 0.68` (25 values);
- uses development OOS predictions only;
- each candidate needs ≥ 30 selected rows;
- maximise the mean `net_at_cost(return_gross, 25 bps)`; ties go to the lower threshold;
- None if no threshold qualifies.

**Metrics** (`research/metrics.py`):
- **Brier** (`:11-12`).
- **Log-loss**, clipped at 1e-6 (`:15-17`).
- **Brier skill** = `1 − Brier/Brier(constant base)` (`:45-47`). The base rate is the **training** TARGET rate: the fold's training set, or the final training set for the holdout (`validation.py:136,173`). It is not the test set's own rate.
- **ECE**: 10 equal-width bins (the last bin includes 1.0), Σ(n_b/n)·|mean_pred − obs| (`:20-42`).
- **Bootstrap** (`:50-68`):
  - resamples whole **calendar-week** groups (`to_period("W")`, `evaluate.py:65`; `metrics.py:167`); there is no fixed block length;
  - `n_boot=2000`, `seed=11`, quantiles .05/.10/.50/.90/.95.
- **Profit factor**: only when n ≥ 30 (`:165`).
- `effective_sample_size` (`:87-92`) is **unused**. Live signals publish only `sample_count = holdout_n` (`policy.py:122`); the `effective_sample_size` field was removed in `bd3dbdf`.

---

## 14. Promotion policy (`research/evaluate.py`, `POLICY_VERSION="promotion-policy-2"` `:19`)

`PromotionPolicy` (`:23-38`). All gates are evaluated on the untouched holdout (`:127-172`):

| Gate | Pass condition | Ref |
|---|---|---|
| Holdout entries | ≥ 100. CALIBRATED needs ≥ 200. | `:145-146,174` |
| Distinct decision dates | ≥ 50 | `:147-148` |
| Regimes in the holdout (classifier label) | ≥ 2 | `:149-150` |
| Brier skill | > 0.0 | `:151-152` |
| ECE | ≤ 0.06 | `:153-154` |
| Threshold found and selected holdout trades | ≥ 30 | `:155-156` |
| p10 of the weekly-bootstrap mean net return of selected trades, at 25 bps | > 0 | `:157-161` |
| Mean shuffled-label holdout skill | ≤ 0.01 (the leakage alarm) | `:162-163` |
| Real skill vs shuffles | > the 95th percentile of 20 shuffled skills. `permutation_p = (1 + #(shuffled ≥ real))/21`, so the minimum is ≈ 0.048. | `:136-144,164-172` |

- **Status**: no failing gate → CALIBRATED if holdout n ≥ 200, else EXPERIMENTAL. Any failure → UNVALIDATED (`:173-176`).
- **Synthetic cap**:
  - `data_source=="synthetic_fixture"` and not UNVALIDATED → EXPERIMENTAL (`:200-206`);
  - the Pi refuses synthetic manifests outside DEMO (`models/manifest.py:133-134`);
  - the policy downgrades them to UNVALIDATED outside DEMO (`policy.py:91-92`).
- **`challenger_beats_champion`** (`:238-256`; server mirror `apps/web/src/server/services/models.ts:18,104-117`):
  1. Reject if the challenger is UNVALIDATED.
  2. Accept if there is no champion.
  3. Reject if either holdout skill is missing.
  4. Reject if `cs < ps + 0.005`.
  5. Reject if both p10 values exist and the challenger's is lower.
  6. Otherwise accept.
  - **DOC≠CODE**: the docstring says "on the SAME most-recent holdout window", but `ps` is the champion's **stored** skill from its own run and window.
- **Validity**: `validity_days=21` → `validation_expires_at = now + 21 d` (`:38`; `models/manifest.py:89`). The Pi rejects or downgrades expired manifests (`manifest.py:131-132`; `policy.py:89-90`).
- **Drift criteria** written into the manifest: `max_ece 0.08`, `min_brier_skill 0`, `max_feature_psi 0.25` (`manifest.py:88`). `apex drift` evaluates them (`release/drift.py:29-35`), but **the Pi does not enforce them**.

---

## 15. Member position assessments (`portfolio/assessment.py`, `ASSESSMENT_VERSION="assess-1"`)

- **Price class** (§1.4) comes first (`:110-116`):
  - ledger WATCHING → `MANUAL_WATCH`;
  - `STALE` or `UNAVAILABLE` → `STALE_DATA` (`:117-133`);
  - with the market closed there is **no age limit** (`market/validation.py:104`).
- **Status precedence** (`:176-246`):
  1. `PRICE_UNCERTAIN`: the price is FRESH_UNVERIFIED and ≤ stop.
  2. No signal → `MANUAL_WATCH`.
  3. `CONSIDER_EXIT`: stop breached and (gap below stop or regime ∈ {risk_off, volatility_shock}).
  4. `REVIEW_INVALIDATION`: stop breached, or the signal is INVALIDATED.
  5. `TARGET_TOUCHED_FILL_UNKNOWN`: price ≥ target.
  6. `TIME_EXIT_REVIEW`: sessions remaining ≤ 0.
  7. `CONSIDER_TRIM`: marginal R/R `(target−p)/(p−stop)` < **0.75**, or < **1.0** if the last notified status was CONSIDER_TRIM. This is the only hysteresis. Also triggers when the regime is bad and the position is in profit.
  8. `ADD_CRITERIA_MET`: signal ACTIVE, CALIBRATED, on plan, price inside the entry zone, marginal R/R ≥ 2, regime not bad, and earnings `NONE_WITHIN_HORIZON_CONFIRMED`. **Unreachable live**, because earnings is always UNKNOWN.
  9. Otherwise `HOLD_THESIS_INTACT`.
- **Flags** (`:167-202`): `TARGET_NEARING` when distance to target ≤ 1.5 %; `TIME_NEAR_EXPIRY` when remaining ≤ 3; `DRAWDOWN_GT_5PCT` when unrealised ≤ −5 %; `EARNINGS_UNKNOWN` / `EARNINGS_WITHIN_HORIZON`; `REGIME_RISK_OFF`; `OFF_PLAN_*` (first lot outside the zone, after expiry, or before publication; `:49-63`).
- **Gap below stop**: `GAP_BELOW_STOP` needs `ctx.session_open_price`, which the engine never sets (`engine.py:197-206`). So CONSIDER_EXIT happens live only via the regime condition.
- **Notify** (`:274-294`): on a change of materiality key (status, flags, `floor(unrealised/5 %)`, price status).
  - Urgent statuses that change bypass the **120 min** cooldown.
  - Repeated `STALE_DATA` is suppressed.
  - The first assessment is suppressed if it is HOLD or MANUAL_WATCH.
- **DOC≠CODE / not found in the worker**: `docs/RISK_MODEL.md:123` ("P&L hidden when the price is older than an hour"). Not verified in `apps/web`.

---

## 16. Catalyst inputs

**What could feed picking** (context columns, §3.2):
- H3 trigger and score: `cat_positive_recent_2d`, `cat_impact_max_5d`.
- H7 trigger and score: `cat_positive_recent_5d`, `cat_impact_max_5d`.
- The Quality catalyst group (§7).
- The `NEGATIVE_CATALYST` −8 penalty.
- Evidence items, and `requires_catalysts` skips.

**What actually feeds it today: nothing.**
- **Live**: `ApexEngine.catalysts_provider` defaults to `lambda: []` and the worker never passes one (`engine.py:85`; `worker.py:236-237,619-626`).
- **DEMO** `LocalDemo` passes none either (`demo/flow.py:102-110`).
- **Research**: synthetic and CI both pass `cats=[]` (`pipeline.py:56`; `training_data.py:151`).
- Catalysts are produced only for the DEMO web seed display (`demo/web_seed.py:214-223`).

**Even if wired**:
- `impact_score` is only set when `CatalystPipeline.run(score_at=...)` is called (`catalysts/pipeline.py:52,89-90`), and **no caller passes `score_at`**.
- So `cat_impact_max_5d` = NaN → the H3/H7 `setup_score` = NaN → Quality None → WATCH in live, and the row is dropped in research.
- In the Quality catalyst group, a missing impact is treated as 0, i.e. tanh(−2) ≈ −0.96.

**The score formula itself** (if used; `catalysts/scoring.py:86-141`):
- `100 · type_w · grade_w · clarity · materiality · 0.5^(age_h/72) · (1 + 0.1 multi-source) · remaining`;
- `remaining = max(0.15, 1 − (|pre_ret| − 0.05)/0.15)` when |pre_ret| ≥ 5 % (`:64-71`);
- type weights at `:21-46`; grade weights at `:47-53`.
- It is scored at **one** `score_at` for all history, so it is not point-in-time per decision.

**Earnings**: the catalyst `EarningsCalendarSource` exists, but nothing connects it to `earnings_status`.

---

## 17. Risks and refinement hooks

### 17.0 Fixed in this snapshot

| Fix | Commit | Effect |
|---|---|---|
| APEX-001 train/live parity and fail-closed vector | `bd3dbdf` | `signals/model_features.py` builds the model vector for both research and live from unrounded context-row inputs. Live `squeeze_prev` is no longer None. `model_row` returns None, and the forecast becomes UNVALIDATED, if inputs are missing (`policy.py:72-74,100-106`). |
| APEX-002 interval removed | `bd3dbdf` | No `interval_80/95` or `effective_sample_size` in `confidence`. Label "… estimate (interval not established)" (`policy.py:109-125`). `docs/RISK_MODEL.md:87` and `docs/README_USER.md` updated to match. |
| APEX-004 session-counted embargo | `fc5b8c9` | `embargo_limit` counts NYSE sessions (`research/validation.py:81-91`). |
| Sector key in the open-per-sector cap | `18224ee` | New candidates use `internal.get("sector") or "unknown"` (`policy.py:196`), matching the open signals' `risk.sector`. |

### 17.1 Magic numbers that look untuned or arbitrary

- **Regime logits**, every coefficient and scale (`features/regime.py:62-83`). The docstring calls them a starting hypothesis.
- **Quality**:
  - group weights (`signals/quality.py:17-24`), `SETUP_WEIGHT 0.4` (`:25`), `MIN_COVERAGE 0.6` (`:26`);
  - tanh scales and shifts (`:58-98`); regime mix 0.5/1.5 (`:92`); catalyst mix 0.6/0.4/50/25 (`:98`);
  - coverage factor 0.85/0.15 (`:132`); penalty slopes and caps (`:105-116`).
- **Strategy thresholds and setup-score weights**: every literal in `library.py:67-264`, including H8's constant score 0.1 (`:258`) and the invalidation multipliers 1.6/1.4/1.2/1.5 ATR and ×0.995.
- **Levels**: 0.5 ATR / 2 % / 0.5 ATR / 1–3 ATR / 2R / 0.25 ATR / 1.5 net / 10 bps (`signals/levels.py:23-31`); default hint `mid − 1.5·ATR` (`:80`).
- **Resistance**: 120 closes, min 60 (`dataset.py:134`; `candidates.py:388-390`).
- **Gates**: $10, $30 M, 1 M, 180 bars, 3.5 ATR, breaker 0.6, 3 per sector (`candidates.py:35-44`).
- **Policy**: 70 / 3 / 1 / 2 / 75 / 1 (`policy.py:35-43`).
- **Feature constructions**: base band 1.5·ATR and max 60 (`compute.py:126,138`); failed breakout 3/60 (`:143`); squeeze 120 / min 60 (`:104,229`); distribution day −0.2 %, 25 days (`regime.py:44-48`); breadth minimum 20 names (`regime.py:20`).
- **Validation and promotion**:
  - 0.2 / 4 / 5 / 150 / 0.25 / l2 4.0 (`validation.py:24-29`);
  - grid 0.20–0.68 step 0.02 (`evaluate.py:51`);
  - promotion gates (`evaluate.py:24-38`); ECE 10 bins (`metrics.py:20`); bootstrap 2000 / seed 11 / weekly (`metrics.py:53-54`).
- **Optimiser**: 400 iterations, lr 0.5, ×1.25, cap 4 (`model.py:29,55`).
- **Baselines**: hint 1.5 ATR (`dataset.py:224`) and `[::3]` thinning (`:269`).
- **Assessments**: 0.75 / 1.0 / 1.5 % / 5 % / 120 min (`assessment.py:174,196,221,281`).
- **Catalyst scoring**: weights and the 72 h half-life (`catalysts/scoring.py:21-71`).
- **Unused duplicates** that can silently drift apart: `MIN_HISTORY_SESSIONS=180` (`compute.py:23`), `LIVE_LOOKBACK_SESSIONS=320` (`panel.py:18`).
  - `ExecConfig` and `LevelConfig` duplicate `entry_valid_sessions` and `cost_bps_per_side` (`execution.py:27-28`; `levels.py:31-32`).

### 17.2 Live vs research divergences

Fixed in this snapshot (§17.0) and no longer listed: the live `squeeze_prev`/rounding vector mismatch (`bd3dbdf`) and the sector-key mismatch (`18224ee`).

1. **Regime gating**: live puts disallowed regimes and `unknown` on WATCH (`candidates.py:334-336`). Research trains and evaluates on them (`dataset.py:140,169`; `docs/BACKTEST_REPORT.md:139` lists 15 risk_off H1 holdout entries).
2. **Population**: research has no Quality ≥ 70/75 filter, no per-symbol dedup, no sector crowding, no session/sector/open caps and no `OPEN_SIGNAL_EXISTS` check. The holdout calibration therefore describes a population different from what live publishes.
3. **Earnings**:
   - live is always UNKNOWN (`worker.py:236-237,619-626`): a −5 penalty, `earnings_unknown=1`, and **no earnings veto ever**;
   - synthetic research uses known dates and a veto (`dataset.py:121-126`);
   - CI research is UNKNOWN (`training_data.py:151`);
   - DEMO live uses known dates (`demo/flow.py:84-100`).
4. **Catalysts**: absent everywhere (§16), so H3 and H7 are dead code paths.
5. **Sectors live**:
   - there is no producer of `sectors.json` (`worker.py:607-609`) and Alpaca assets have `sector=None` (`alpaca.py:145`);
   - so live stocks are sectorless: the sector group shrinks to rs_bench, and H5 never fires (sector rank NaN);
   - all sectorless names share one bucket: the candidate engine's crowding check keys `None`; the policy keys `"unknown"` (`policy.py:155,196`);
   - so live can publish at most 1 new signal per session (`max_new_per_sector=1`, `policy.py:37,200`) and hold at most **2 open signals in total** (`max_open_per_sector=2`, `policy.py:38,197`). The candidate engine moves the 4th and later PASS candidates to WATCH.
6. **Universe composition**: live is the top 400 liquid names (`universe_job.py:59`); CI is a fixed 66 large caps; synthetic is about 276 stocks.
   - Cross-sectional rank thresholds (H2 0.75, H5 0.65, quality `xs_rank_ret_60`) and breadth (and therefore regime) mean different things in each.
   - Live ETFs are scannable but have no xs ranks (`panel.py:78`).
7. **Gates not configurable**: `CandidateEngine` always uses the default `GateConfig` (`candidates.py:253`), while `market.universe` settings change only the nightly universe. **DOC≠CODE**: `docs/ADMIN_SETTINGS.md:75` says these settings are used by the "scan".
8. **Entry window**: live `entry_valid_sessions` is configurable 1..5 (`worker.py:131,139`), but labels and calibration assume 2 (`execution.py:28`).
9. **Decision-bar completeness**: the scan runs at close + 15 min (`worker.py:89,450`), but the SIP request end is clamped to now − 16 min (`alpaca.py:26,151-154`), i.e. before the close. The last minute and the closing auction may be missing from the live decision bar (affecting `close`, `rel_volume_1`, `clv_1`). **Needs verification against Alpaca behaviour.**
10. **Lookback**: live `bars_available` ≤ 320. The Wilder ATR initialisation effect is negligible after 300 rows. Any feature needing more than 320 rows would silently become NaN live.
11. **Corporate actions after publication**: the monitor replays stored levels against re-adjusted bars (`monitor.py:42-53`). A split during the holding window mis-scores the outcome.
12. **Champion comparison** uses stored skills from different holdout windows and data (`evaluate.py:246-251`; `apps/web/src/server/services/models.ts:104-117`).
13. **Quote outlier quarantine**: a print more than 12 % from the last accepted price is quarantined, and the last-good quote never updates (`quote_cache.py:96-114`; `market/validation.py:77-82`). After a >12 % gap, every print is rejected and the symbol goes `STALE_DATA`. This affects the lifecycle and assessments, not picking.
14. **Disabled assessment branches**: `session_open_price` is never set (`engine.py:197-206`), so `GAP_BELOW_STOP` and the gap-driven CONSIDER_EXIT never happen.

### 17.3 Look-ahead and leakage paths

- **Split adjustment as of today**:
  - `$10` / `$30 M` / `1 M`-share gates and tick rounding see future splits (Alpaca; `synthetic.py:119-127`; `replay.py:122`);
  - for example, a heavily split stock's early history falls below $10 and is excluded from research;
  - share-volume filters are inflated retroactively.
- **Survivorship and current sector labels** in the CI universe (`training_data.py:7-14,55-61`). The synthetic data includes a delisted name, but trades crossing delisting are labelled TIME.
- **Earnings schedule assumed known in advance** for every date in synthetic research (`dataset.py:85-96`).
- **Catalyst impact scored at one `score_at`** for all history (`catalysts/pipeline.py:89-90`); latent today.
- **Right-censoring**: DATA_END is labelled TIME (`validation.py:59`), so the last ~20 sessions of the holdout are biased toward TIME.
- **Unpurged fit/calibration split** inside each training set (`validation.py:68-69`). This adds optimism to the calibrator, not to the holdout.
- **Embargo**: fixed in `fc5b8c9`. It now counts NYSE sessions; the `BDay` fallback applies only outside the pinned calendar (`validation.py:89-90`).
- **Generator**: the centred vol smoother (`synthetic.py:404`) is non-causal by construction. It is harmless for features but makes vol predictability unlike a causal process.
- **Holdout reuse**:
  - promotion-policy-2 was adopted after inspecting holdout results (`docs/BACKTEST_REPORT.md:60-80`);
  - weekly retraining on a rolling 20 % holdout re-tests the same recent data repeatedly.

### 17.4 Selection bias and multiple testing

- **No family-wise control across strategies**: 7 enabled hypotheses are each evaluated independently and any one passing is promotable (`pipeline.py:112-133`).
- **Threshold choice**: 25 thresholds chosen to maximise mean net return on dev OOS (`evaluate.py:46-58`). This is optimistic for the dev statistic; the holdout is applied once per run.
- **Weak permutation gate**: 20 permutations, so p ≥ 1/21 (`evaluate.py:35-36,140`). The leakage alarm uses the mean.
- **H8 `weak_prior`**: no multiple-testing penalty is implemented (`library.py:389-395`).
- **Missing comparisons**: H3's placebo set and H7's "beat components" comparison are not implemented.
- **Mismatched baselines**:
  - built from the first strategy's dates only (`pipeline.py:113-115`);
  - no earnings veto (`dataset.py:214`);
  - include ETFs;
  - the benchmark baseline is never computed (`pipeline.py:116-118`).
- **Overlap**: events overlap (the same symbol fires on consecutive days, and several strategies fire on the same day), so the effective n is well below `holdout_n`. The weekly cluster bootstrap mitigates this only for the mean.
- **Generator designed with H1 in mind**: the VCP-plus-volume structure is planted (§1.5), so synthetic "skill" for H1 or breakout variants is partly self-fulfilling.
- **Per-strategy holdout start dates differ** (`validation.py:115`).

### 17.5 Natural search dimensions (file:line)

| Dimension | Location |
|---|---|
| H1 params `max_squeeze/min_base/max_depth/min_relvol` (the only exposed params dict) | `signals/strategies/library.py:67` |
| H2–H8 trigger thresholds | `library.py:102-111, 131-136, 152-159, 175-181, 201-207, 227-234, 254-257` |
| Setup-score weights and scales | `library.py:80-85, 112-116, 137, 160, 182-186, 208-212, 235` |
| Invalidation hints (ATR multiples, gap ×0.995) | `library.py:91, 122, 143, 166, 192, 218, 241, 264` |
| Target R multiple (and the unused `r_multiple` hook) | `signals/levels.py:28,72,85` |
| Stop clamp [1,3] ATR | `levels.py:26-27,81` |
| Entry zone 0.5 ATR / 2 % / 0.5 ATR | `levels.py:23-25,76-78` |
| Resistance definition (closes vs highs, 120, min 60) and buffer 0.25 ATR | `research/dataset.py:134`; `signals/candidates.py:388-390`; `levels.py:29,95-96` |
| Minimum net R/R 1.5; cost 10 bps | `levels.py:30-31` |
| Entry validity 2; horizon 20 | `levels.py:32-33`; `research/execution.py:27-29` |
| Gates | `signals/candidates.py:35-44` |
| Allowed regimes per strategy | `library.py:28,348` |
| Quality weights, coverage, scales, penalties | `signals/quality.py:17-26,58-116,132` |
| Policy thresholds and caps | `signals/policy.py:35-43` |
| Regime logits | `features/regime.py:62-83` |
| Feature windows (base band, squeeze, pivot, failed breakouts) | `features/compute.py:126,138,143,229,256` |
| Model feature set | `research/dataset.py:30-49`; assembler `signals/model_features.py:18-67` |
| Regularisation, calibration fraction, folds, embargo, holdout | `research/validation.py:24-29` |
| Threshold grid; evaluation cost | `research/evaluate.py:51,31` |
| Promotion gates | `research/evaluate.py:24-38` |

---

## Summary (10 lines)

1. Daily split-adjusted, price-only bars. Decisions at close + 15 min, entries only from the next session, so the feature path is strictly causal.
2. A universe filter plus hard gates ($10, $30 M, 1 M shares, 180 bars, ≤ 3.5 ATR extension, risk-off breaker 0.6, earnings veto) admit names. Eight hand-specified long-only trigger plugins then fire.
3. A rule-based softmax regime classifier and a six-group, coverage-aware Quality (0–100, ranking only) score each trigger; the engine keeps one candidate per symbol and a 3-per-sector PASS cap.
4. Levels come from ATR: zone −0.5 / +min(0.5 ATR, 2 %); stop = strategy hint clamped to [1,3] ATR below mid; target 2R, capped 0.25 ATR under the prior 120-close high; net R/R ≥ 1.5.
5. Publication: Quality ≥ 70, then caps (3 per session, 1 new and 2 open per sector), then HIGH_CONVICTION only with a CALIBRATED, unexpired champion where p ≥ threshold and EV > 0. Otherwise one RESEARCH_SETUP at Quality ≥ 75.
6. Outcomes use one conservative daily-bar simulator (open/limit fills, STOP-first on the same bar, gap exits at the open, 20-session time stop) in both research and live lifecycle tracking.
7. Research labels every gated trigger, fits an L2 multinomial logit with isotonic calibration under purged walk-forward, and promotes only through frozen holdout gates: skill, ECE, economic p10, 20-permutation control, 21-day validity.
8. The synthetic market plants regime- and length-dependent base breakouts with a volume tell, plus persistent sector drift. Only breakout and sector/momentum ideas can show an edge there.
9. Live diverges from research on regime filtering, the published population, earnings (always UNKNOWN live), catalysts (never wired), sectors (no live sector map) and universe composition.
10. Biggest refinement levers: wire earnings, catalysts and sectors live; align the research population with the live policy; revisit the close-based 120-session supply cap that rejects most pullbacks; control multiple testing across strategies and repeated holdouts.
