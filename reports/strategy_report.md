# Binance Spot Crypto Research: Strategy Report

**Date:** 2026-07-09
**Data window:** 2019-01-01 to 2026-07-08 (raw archives), backtests run 2019-07-01 to 2026-07-01 (allows 180d min-history warmup)
**Universe:** 447 currently-trading USDT spot pairs, daily/4h/1h klines

## 1. What was inspected

- `data/raw/binance/spot/{monthly,daily}/klines/<SYMBOL>/{1d,4h,1h}/*.zip` — 64,056 ZIP files across 447 symbols x 3 intervals.
- `data/raw/binance/spot/current_universe.json` — 447 symbols currently trading, all quoted in USDT.
- Manually inspected raw CSV contents (no header, 12 Binance kline columns) across multiple years to confirm schema and detect the ms->us timestamp unit switch.

## 2. What was built

| Component | Path |
|---|---|
| Kline reader (schema/timestamp-unit detection) | `src/crypto_research/data/kline_reader.py` |
| Phase 1 data audit | `src/crypto_research/cli/audit.py` -> `reports/tables/data_quality_report.json` |
| Phase 2 processed Parquet layer | `src/crypto_research/data/build.py` -> `data/processed/binance/spot/klines/interval=<i>/symbol=<s>/data.parquet` |
| Phase 3 universe construction | `src/crypto_research/universe/builder.py`, `configs/universes.yaml` |
| Phase 5 feature engineering | `src/crypto_research/features/panel.py`, `cross_sectional.py` |
| Phase 6 backtest engine | `src/crypto_research/backtest/engine.py`, `metrics.py`, `configs/costs.yaml` |
| Strategies | `src/crypto_research/strategies/{btc_trend,cross_sectional_momentum,mean_reversion}.py` |
| Research/comparison/walk-forward CLIs | `src/crypto_research/cli/{research,compare,walkforward,make_charts}.py` |

Reproduce everything:
```bash
uv run python -m crypto_research.cli.audit
uv run python -m crypto_research.data.build
uv run python -m crypto_research.cli.universe --universe top20 --start 2019-06-01 --end 2026-07-01 --freq MS
uv run python -m crypto_research.cli.compare --start 2019-07-01 --end 2026-07-01 --freq W --cost-tier base
uv run python -m crypto_research.cli.walkforward
uv run python -m crypto_research.cli.make_charts
```
On Windows PowerShell these `uv run python -m ...` commands work identically (uv abstracts the shell); no path changes needed.

## 3. Data quality findings

- **Integrity: clean.** 0 corrupt ZIPs, 0 empty files, 0 unexpected schemas, 0 header rows, 0 zero/negative/NaN prices, 0 bad OHLC relationships across all 64,056 files.
- **Timestamp units switch from milliseconds to microseconds exactly at 2025-01**, confirmed across every symbol sampled. Handled via per-file magnitude detection (`kline_reader.py`), not a hardcoded date cutoff.
- **Monthly/daily overlap dedup**: only 1 duplicate candle found in the entire dataset (AXSUSDT, 1d) — the archives are nearly perfectly non-overlapping already.
- **Coverage**: 2019-01-01 to 2026-07-08. But 149 of 447 symbols (33%) first appeared in 2025-2026 — a large fresh-listing tail. 46 symbols have under 6 months of history.
- **Survivorship bias confirmed real**: `current_universe.json` only lists symbols trading *today*. Historical delistings (FTT collapse, old LUNA before the LUNC rename, etc.) are invisible to it. The `current_universe_survivorship_biased` universe is provided only as a labelled benchmark to quantify this effect, never for strategy selection.
- **Non-crypto contamination identified and excluded** (`configs/universes.yaml`): 24 Binance-listed tokenized-stock/ETF products (TSLAB, NVDAB, GOOGLB, SPYB, QQQB, etc.) and 7 stablecoin/synthetic base assets (USDS, USDE, USD1, RLUSD, BFUSD, AEUR, EURI). SHIB was explicitly excluded from the tokenized-stock pattern match (it's a real, long-standing token that happens to end in "B").
- **Gaps**: concentrated, not systemic. Only 8/447 symbols have any 1d gaps (USDSUSDT, FTTUSDT, CVCUSDT, LUNAUSDT most notable — the latter two consistent with known market events). Gap density rises at finer granularity (215/447 symbols have some 1h gaps), consistent with low-liquidity trading pauses rather than a pipeline defect.

Full machine-readable report: `reports/tables/data_quality_report.json`.

## 4. Universes constructed (point-in-time safe)

All rankings use only `close_time <= t` data. Built and spot-checked monthly, 2019-06 to 2026-07:

1. `btc_eth` — BTC + ETH only.
2. `top10` / `top20` / `top50` — ranked by trailing 30d quote volume, min 180-day history required, static exclusion list applied.
3. `broad_liquid` — all symbols meeting history + $1M trailing daily quote volume floor.
4. `current_universe_survivorship_biased` — the 447 current symbols applied across all history. **Labelled biased, used only as a benchmark.**

Verified correct point-in-time behavior: `top20`/`top50`/`broad_liquid` show 0 or partial membership in mid-2019 before enough symbols pass the 180-day history filter, then ramp up. Composition tracks real market cycles (DOGE entering top10 during the 2021 meme rally, SOL/ARB/OP appearing in 2024).

## 5. Strategies tested and full-history results (2019-07 to 2026-07, weekly rebalance, base cost tier)

| Strategy | Universe | CAGR | Sharpe | Max DD |
|---|---|---|---|---|
| Buy & hold BTC | — | 28.8% | 0.73 | -75.2% |
| Buy & hold ETH | — | 32.0% | 0.75 | -76.8% |
| **50/50 BTC/ETH** | — | **33.9%** | **0.78** | -74.7% |
| BTC 200d trend filter | — | 20.3% | 0.63 | -54.8% |
| Equal-weight rebalance | top10 | 3.4% | 0.43 | -84.9% |
| Equal-weight rebalance | top20 | -4.4% | 0.36 | -93.8% |
| Equal-weight rebalance | top50 | -3.4% | 0.39 | -96.2% |
| Equal-weight rebalance | broad_liquid | 16.9% | 0.61 | -90.4% |
| Equal-weight rebalance | current_universe (biased) | -5.1% | 0.06 | -79.0% |
| Cross-sectional momentum | top10 | 3.6% | 0.43 | -84.7% |
| Cross-sectional momentum | top20 | -6.3% | 0.34 | -94.7% |
| Cross-sectional momentum | top50 | **-30.3%** | 0.10 | -99.5% |
| Cross-sectional momentum | broad_liquid | **-31.8%** | 0.05 | -99.6% |
| Mean reversion | top10 | -27.8% | -0.14 | -90.2% |
| Mean reversion | top20 | -28.7% | -0.12 | -91.1% |
| Mean reversion | top50 | -25.8% | 0.00 | -96.7% |
| Mean reversion | broad_liquid | -28.0% | -0.03 | -97.1% |

Full table: `reports/tables/strategy_comparison.csv`. Charts: `reports/figures/equity_curves.png`, `reports/figures/drawdowns.png`.

## 6. Strategies rejected, and why

- **Cross-sectional momentum** (rank by trailing 30d return, hold top-N, weekly rebalance): rejected on all universes larger than the trivial 10-member case. On top50/broad_liquid it lost 90-99.6% of capital. Verified this is a genuine strategy failure, not an engine bug (checked year-by-year equity decay, cost totals ~20-30% of capital vs. the much larger realized losses, and trade counts — all consistent with poor signal quality plus turnover drag, not a compounding error). Chasing recent winners in this dataset's long-tail altcoin universe was actively harmful.
- **Mean reversion** (buy the most oversold names by 20d z-score): rejected on every universe tested. Lost 90-97% of capital, with the largest single-year loss during the 2020 COVID crash — a textbook "catching a falling knife" failure where oversold names kept falling.
- **Equal-weight rebalance on top10/top20/top50** (no signal, pure periodic rebalancing): flat to negative CAGR — with no edge, weekly rebalancing costs alone are a net drag.

None of these were rejected because of a disappointing Sharpe ratio alone — they lost the majority of invested capital in absolute terms, which is a much higher bar of failure than "underperformed a benchmark."

## 7. Best surviving strategy: BTC 200-day trend filter

The only active strategy that did not destroy capital. Walk-forward (chronological, year-by-year, no re-fitting):

| Year | CAGR | Sharpe | Max DD | Trades |
|---|---|---|---|---|
| 2019 (H2) | -51.1% | -1.66 | -37.5% | 4 |
| 2020 | +175.8% | 2.28 | -26.4% | 5 |
| 2021 | -8.1% | 0.21 | -53.4% | 20 |
| 2022 (bear) | 0.0% | n/a | 0.0% | 0 — sat in cash all year, avoided the crash |
| 2023 | +74.6% | 1.54 | -22.1% | 5 |
| 2024 | +45.7% | 1.02 | -36.1% | 11 |
| 2025 | -20.9% | -0.52 | -29.3% | 18 |
| 2026 YTD | 0.0% | n/a | 0.0% | 0 — currently in cash |

**Cost sensitivity** (full history, single SMA window=200): CAGR ranges 18.6% (stress tier) to 24.6% (optimistic tier) — moderate sensitivity, survives even the stress cost assumption without collapsing.

**Parameter sensitivity** (SMA window 100-300 days, base costs): CAGR ranges 20.8%-37.9%, Sharpe 0.65-0.96 — a broad plateau, no narrow lucky parameter. Full table: `reports/tables/btc_trend_parameter_sensitivity.csv`.

**Verdict**: robust to cost assumptions and parameter choice, and it genuinely avoided the entire 2022 bear market (0% return vs. -60 to -70% for buy-and-hold in the same year, visible in `reports/figures/drawdowns.png`). But its full-history CAGR (23.4% at base cost) and Sharpe (0.70) still trail simple buy-and-hold BTC (28.8% CAGR, Sharpe 0.73) and materially trail 50/50 BTC/ETH (33.9% CAGR, Sharpe 0.78). The whipsaw cost in choppy years (2019, 2021, 2025) outweighs the crash protection benefit over this particular 7-year window.

## 8. Major limitations

- Costs (fees/spread/slippage) are **assumptions**, not historical facts — the raw archives contain no historical fee tiers, spreads, or order-book depth. See `configs/costs.yaml` for exact assumptions and their stated rationale.
- Survivorship bias is only partially addressed: symbols are excluded from history before their own listing date, but symbols that were **delisted** before 2026 and are absent from `current_universe.json` are not reconstructable from this dataset at all — the broader universes may still slightly overstate historical returns by omitting failed projects that never make it into "currently trading."
- Backtests use daily bars with weekly rebalancing; 1h/4h data was audited and processed but not used for signal generation in this pass — higher-frequency strategies remain unexplored.
- The liquidity/capacity model (`configs/costs.yaml`) is a coarse cap on trade size vs. trailing volume, not a real order-book impact model.
- Machine-learning approaches (Phase 4's later sections) were not attempted, since none of the simple economically-motivated baselines cleared the bar of beating buy-and-hold — per the project's own stated principle of building strong simple baselines before adding model complexity.

## 9. Conclusion

**No active strategy tested here provides a defensible edge over passive buy-and-hold in this dataset, after realistic costs.** Cross-sectional momentum and mean-reversion are conclusively rejected (they destroy capital, not merely underperform). The BTC 200-day trend filter is the only strategy that survives walk-forward and cost/parameter sensitivity without collapsing, and it does something real (avoiding the 2022 bear market), but it does not beat simple buy-and-hold on absolute or risk-adjusted return over the full available history.

**Confidence assessment for the BTC trend filter: exploratory / promising but unproven.** It is not rejected outright — a trend filter that avoids a full bear-market year while remaining robust to parameter and cost assumptions is a legitimate risk-management tool worth further study (e.g. as a regime filter layered onto a different underlying strategy, or evaluated as a drawdown-reduction overlay rather than a standalone return-seeking strategy). It is **not** recommended for live capital as a standalone strategy in its current form, since it underperforms the benchmark it's meant to improve upon.

**Overall evidence supports: further research, not paper trading or live capital deployment**, for any of the strategies tested to date. The clearest actionable next step (not done here) would be testing the trend filter as a volatility/drawdown overlay on a passive BTC/ETH allocation, rather than as a full switch between 100% and 0% exposure.

## 10. Live-trading specification (BTC 200-day trend filter, for completeness — not currently recommended)

- **Market universe**: BTCUSDT spot only.
- **Timeframe**: 1d.
- **Signal**: `close_t > SMA_200(close)_t` -> risk-on; else risk-off.
- **Entry/exit**: signal observed at daily candle close; execute at next candle's open (Binance spot market or limit-near-open order).
- **Position sizing**: 100% or 0% of allocated capital to BTC; no leverage.
- **Rebalancing**: daily signal check, trade only on a regime flip (not every day).
- **Risk controls**: none beyond the binary trend filter itself; no stop-loss or volatility targeting was layered on in this pass.
- **Cost assumptions**: base tier (10bps taker fee + 3bps spread + 5bps slippage, one-way) — see `configs/costs.yaml` for full assumptions and caveats.
- **Expected historical behavior**: strong in trending years, weak in choppy/whipsaw years, 0% return (not loss) during a sustained bear regime.
- **Failure conditions**: sideways/choppy markets that repeatedly cross the 200d SMA without committing to a trend (2019 H2, 2021, 2025 in this dataset).
- **Kill-switch**: not designed for live deployment; this specification is provided for completeness given the "exploratory / promising but unproven" classification, not as a live-trading recommendation.

---

# Second research pass (2026-07-10): signal combinations

Pre-registered follow-ups from the first pass (see `reports/handoff_prompt.md`): trend-gated momentum, rebalance-cadence sensitivity, intraday mean reversion, volume-confirmed breakouts, inverse-volatility sizing. Same validation bar as before: full history 2019-07 to 2026-07, base cost tier unless stated, reject anything that loses capital or fails to beat buy-and-hold after costs. New result JSONs in `reports/tables/result_*.json`.

## 11. BTC-trend-gated cross-sectional momentum — REJECTED

Gate: skip all momentum positions when BTC close < 200d SMA (`require_btc_uptrend=True`), weekly rebalance, equal weight, top-10 held.

| Universe | Ungated CAGR (pass 1) | Gated CAGR | Gated Sharpe | Gated MaxDD |
|---|---|---|---|---|
| top10 | 3.6% | 12.5% | 0.50 | -64.1% |
| top20 | -6.3% | 10.7% | 0.49 | -79.9% |
| top50 | -30.3% | -1.3% | 0.37 | -92.8% |
| broad_liquid | -31.8% | -9.1% | 0.26 | -96.3% |

The gate does exactly what was hypothesized — it removes momentum's worst regime and rescues 20-30 points of CAGR — but the result is damage control, not edge. Every configuration still trails buy-and-hold BTC (28.8% CAGR, Sharpe 0.73) with far deeper drawdowns. The gate is a legitimate risk overlay; the underlying cross-sectional momentum signal remains worthless in this dataset.

## 12. Momentum rebalance cadence: monthly + skip-most-recent-week — REJECTED

Tested whether pass 1 rejected the implementation rather than the signal: monthly (MS) rebalance, and the literature-standard skip-week signal (`momentum_30d_skip7` = trailing 30d return ending 7 days ago).

| Variant | Universe | CAGR | Sharpe |
|---|---|---|---|
| plain momentum, monthly | top50 | -19.8% | 0.21 |
| skip-week, monthly | top20 | -7.8% | 0.32 |
| skip-week, monthly | top50 | -14.3% | 0.30 |
| skip-week, monthly | broad_liquid | -11.0% | 0.33 |
| skip-week + gate, monthly | top50 | -8.1% | 0.27 |
| skip-week + gate, monthly | broad_liquid | -10.6% | 0.24 |

Monthly cadence improves on weekly (-19.8% vs -30.3% on top50) and skip-week improves further, but every variant still loses capital. Total costs (0.17-0.37 of initial NAV) are far smaller than the realized losses, so this is signal failure, not cost drag. **The momentum signal itself is bad here, independent of implementation.** Combined with Section 11: cross-sectional momentum is conclusively rejected in all tested forms.

## 13. Inverse-volatility weighting (Test 5) — improves momentum, still rejected

Applied 1/realized_vol_14d weights (normalized) to the only positive momentum configs (gated, weekly):

| Config | Equal-weight CAGR | Inverse-vol CAGR | Inverse-vol Sharpe | MaxDD |
|---|---|---|---|---|
| gated top10 | 12.5% | 19.7% | 0.60 | -56.7% |
| gated top20 | 10.7% | 21.9% | 0.62 | -71.1% |

A consistent ~7-11 point CAGR improvement — inverse-vol sizing demonstrably helps this class of noisy long-only signal — but both configs still trail buy-and-hold BTC on return (28.8%) and Sharpe (0.73). Not pursued further for momentum; the sizing lesson carries over to any future survivor.

## 14. Intraday (4h/1h) mean reversion — REJECTED (gross-negative signal)

20-period z-score reversion on intraday bars (`cli/research_intraday.py`), buy bottom-10 names with z <= threshold, re-evaluated every bar, monthly point-in-time universes forward-filled onto the intraday grid, base costs.

| Interval | Universe | z threshold | Net total return | Gross (zero-cost) total return |
|---|---|---|---|---|
| 4h | top20 | -1.0 | -100.0% | -99.8% |
| 4h | top50 | -1.0 | -100.0% | -99.8% |
| 4h | top20 | -2.0 | -99.998% | -91.7% |
| 1h | top20 | -2.0 | -100.0% | **+862% (38.2% CAGR, Sharpe 0.86)** |

At 4h the decisive number is the **gross** column: even with all trading costs set to zero, reversion loses 92-99.8% of capital — the signal is negative before costs enter, and costs merely accelerate the wipeout (turnover ~0.6 per 4h bar).

The 1h run (`result_intraday_1h_top20_z-2.0.json`) is a genuinely different and more interesting failure: at that horizon the reversion signal has **real gross alpha** (+862% total, Sharpe 0.86 before costs), but 78,684 trades convert it to a net -100% at the base cost tier. To break even, round-trip costs would need to fall well below 1bp — i.e. maker-only execution with high fill rates, which this dataset (no order book, no queue data) cannot credibly model. **Rejected as untradeable in this framework**, but recorded as the only signal in two passes with material gross alpha; a future pass with order-book data could revisit it as a maker-side strategy.

## 15. Volume-confirmed Donchian breakout — REJECTED after walk-forward (regime artifact)

New strategy family (`strategies/volume_breakout.py`): enter on close above prior 20d high, exit on close below prior 10d low (turtle-style, stateful between days, daily cadence), optional confirmation by taker-buy ratio >= 0.55 or 20d volume z-score >= 1, max 10 positions.

**Step 1 — naive equal-weight-among-held results were spectacular and wrong.** taker/top50 showed 128.8% CAGR (329x final equity), but diagnostics showed 536 days with 100% of NAV in a single small-cap alt (929 of 2,558 days >= 50% in one name) and profits almost entirely from single-name bets in the 2020-21 alt mania (+986% in 2020, +666% in 2021). A concentration artifact, not a strategy.

**Step 2 — fixed 10%-per-position slices (concentration removed), base costs:**

| Variant | Universe | CAGR | Sharpe | MaxDD |
|---|---|---|---|---|
| taker-confirmed | top50 | 23.1% | 1.33 | -18.3% |
| taker-confirmed, stress costs | top50 | 21.5% | 1.24 | -18.6% |
| unconfirmed Donchian | top20 | 47.1% | 1.01 | -61.0% |
| volume-z-confirmed | top20 | 47.7% | 1.03 | -57.8% |

On full-history aggregates these beat buy-and-hold and survive stress costs (turnover is low). This cleared the bar, so the family went to parameter-sensitivity and walk-forward — where it failed.

**Step 3 — parameter sensitivity (top50, fixed slice, base costs), full table `reports/tables/breakout_sensitivity_top50.csv`:**

| taker_buy_min | CAGR | Sharpe | MaxDD |
|---|---|---|---|
| 0.50 | 13.3% | 0.51 | **-92.6%** |
| 0.55 (baseline) | 23.1% | 1.33 | -18.3% |
| 0.60 | 1.2% | 0.26 | -9.2% (barely trades) |

The taker confirmation lives on a knife-edge: one step looser and the max drawdown goes from -18% to -93%; one step tighter and the strategy stops trading. It also fails to transfer across universes (taker on top20: 1.2% CAGR, 107 trades in 7 years). Unconfirmed Donchian on top50: 8.8% CAGR, -93.1% MaxDD. This is parameter luck, not a signal. (Entry/exit window and position-count variations do sit on a plateau — Sharpe 1.0-1.46 — the fragility is specifically the taker threshold and the era.)

**Step 4 — walk-forward year-by-year (fixed slice, base costs), full table `reports/tables/breakout_yearly_*.csv`:**

| Year | taker top50 | donchian top20 | volz top20 |
|---|---|---|---|
| 2019 (H2) | -3.4% | -32.6% | -31.6% |
| 2020 | +79.3% | +363.2% | +328.0% |
| 2021 | +83.2% | +610.3% | +502.1% |
| 2022 | 0.0% | -46.8% | -42.2% |
| 2023 | +30.9% | +50.7% | +59.9% |
| 2024 | -9.1% | +21.7% | +40.5% |
| 2025 | +12.4% | -24.5% | -22.1% |
| 2026 YTD | +1.4% | -8.7% | -13.9% |

The headline CAGRs are a 2020-21 alt-season artifact. Post-2021, donchian/volz top20 is roughly flat-to-negative while buy-and-hold BTC had its strongest years (2023-24), and both lose money in 2025-26. The taker/top50 variant is milder but its post-2021 profile (+31%, -9%, +12%, +1%) does not remotely justify its full-history Sharpe — and per Step 3 that variant is parameter-fragile anyway. On top of this, the dataset cannot contain delisted symbols, and small-cap breakout buying is precisely where survivorship bias inflates results most.

**Verdict: rejected.** Not because the full-history aggregate fails the bar (it passes), but because walk-forward shows the edge is confined to a single historical regime, the best variant's key parameter sits on a cliff, and the strategy family concentrates exposure exactly where the dataset's known survivorship blind spot is. Classification: regime artifact / not deployable. The one honest positive: turtle-style breakout mechanics (entry/exit windows, position caps) showed broad parameter plateaus, so if a future pass finds a robust confirmation signal, the scaffolding is sound.

## 16. Engine observation: liquidity cap looser than documented

`cli/research.py` passes the trailing **30d sum** of quote volume to the engine's capacity cap, whose docstring describes 1% of trailing **daily** volume — so the effective cap is ~30% of average daily volume per trade, ~30x looser than documented. This does not affect cost calculations, only how aggressively the capacity guard shrinks large trades (it essentially never binds at NAV=1 unit). All pass-1 and pass-2 results share this convention, so comparisons are internally consistent, but capacity claims should not be made from these backtests. Left unchanged for comparability; flagged for a future fix.

## 17. Second-pass conclusion

**Still no deployable edge.** Every pre-registered follow-up was tested and none survives the full validation chain:

- Cross-sectional momentum: rejected in every form (gated, monthly, skip-week, inverse-vol weighted). The gate and inverse-vol sizing each add real value as overlays (+15-30 CAGR points of damage control, +7-11 points respectively) but the underlying signal never approaches buy-and-hold.
- Mean reversion: rejected at daily (pass 1) and now at 4h/1h. The 4h signal is negative even at zero cost; the 1h signal has real gross alpha (Sharpe 0.86 pre-cost) but is annihilated by taker costs at ~79k trades — untradeable without order-book-level maker execution modeling this dataset cannot support.
- Volume-confirmed breakouts: the only family to pass the full-history bar, rejected at the walk-forward stage as a 2020-21 alt-season regime artifact with a parameter-cliff confirmation filter, sitting in the dataset's survivorship blind spot.

What survives as *tools* (not strategies): the BTC 200d trend gate (regime damage control), inverse-volatility sizing, and fixed-slice position caps — each measurably improved whatever it was attached to. What this dataset cannot support: any claim that requires the 2020-21 alt regime to repeat, or precise capacity/cost claims for small-cap names.

---

# Third research pass (2026-07-10): VectorBT sweeps — chandelier-exit trend following

Environment: added `vectorbt==0.28.0` (required relaxing the `numpy==2.5.1` pin to `numpy>=1.22,<2.5` for numba compatibility — see `pyproject.toml`). New CLIs: `src/crypto_research/cli/vbt_research.py` (initial sweep), `vbt_chandelier_wf.py` (walk-forward/sensitivity validation). Same base cost tier as prior passes (10bps taker + 3bps spread + 5bps slippage, one-way).

## 18. Chandelier-exit ATR trailing stop + MA trend gate (BTC/ETH/SOL/BNB) — SURVIVES, deployable candidate

Swept dual-EMA crossover, Donchian breakout, volatility-targeted 50/50 BTC/ETH, and ATR-based chandelier trailing stops on BTC. Signal: long when `close > SMA(trend_w)` AND `close > rolling_max(22) - atr_mult * ATR(14)`; flat otherwise. Best full-history BTC config (atr_mult=4.0, trend_w=50): CAGR 48.0%, Sharpe 1.17, MaxDD -58.8%, 66 trades over 7 years — vs. buy-and-hold BTC 28.1% CAGR, Sharpe 0.71.

**Validation performed (all passed):**
- **Parameter sensitivity** (`reports/tables/chandelier_sensitivity.csv`, atr_mult 1.5-5.0 x trend_w 30-100, 48 combos): broad plateau, Sharpe 0.68-1.20 throughout, no narrow lucky cell. atr_mult >= 3.5 with trend_w 40-60 consistently best.
- **Cost stress**: CAGR 51.1% (optimistic) -> 48.0% (base) -> 43.9% (conservative) -> 35.4% (stress tier) — degrades gracefully, still beats buy-and-hold even at stress costs.
- **Walk-forward year-by-year** (`reports/tables/chandelier_yearly.csv`), strategy vs. BTC buy-and-hold: 2019 -14.1% vs -32.3%, 2020 +309.6% vs +301.7%, 2021 +120.4% vs +57.6%, 2022 -50.8% vs -65.3%, 2023 +137.1% vs +154.5%, 2024 +67.2% vs +111.8%, 2025 +2.3% vs -7.3%, 2026 YTD -5.2% vs -32.4%. Beats buy-and-hold in 6 of 8 years, including smaller losses in every down year (2019, 2022, 2026) — not a single-regime artifact like the pass-2 breakout family.
- **Execution-timing check** (lookahead bug guard): re-ran with signal computed on close but *executed at next day's open* (the realistic convention used elsewhere in this project) instead of same-bar close. Result essentially unchanged: 48.0% CAGR, Sharpe 1.17 — confirms the edge is not a same-bar lookahead artifact.
- **Cross-asset generalization**: same fixed parameters (atr_mult=4.0, trend_w=50), no re-fitting, on ETHUSDT/SOLUSDT/BNBUSDT — all beat their own buy-and-hold: ETH 48.6% vs 27.4% (Sharpe 0.99), SOL 113.5% vs 71.0% (Sharpe 1.33), BNB 74.7% vs 49.3% (Sharpe 1.18). BTC-only and majors-only, so this sidesteps the altcoin survivorship/liquidity-cap concerns flagged in passes 1-2.

**Why this differs from the rejected pass-2 breakout family**: that family's edge was concentrated in 2020-21 alt-season and required small/mid-cap altcoins (survivorship blind spot); this signal uses only BTC/ETH/SOL/BNB, beats its own buy-and-hold in most years including two separate bear/crash years (2022, 2019), and the parameter surface has no cliff.

**Not yet done** (next steps before considering live): position sizing beyond 100/0% (vol-targeting or fractional sizing per [[binance-engine-liquidity-cap]] lesson), portfolio-level combination across BTC/ETH/SOL/BNB with correlation-aware weighting, out-of-sample test on data after 2026-07 as it accrues, and slippage/impact modeling specific to stop-triggered exits (chandelier exits can cluster during fast drawdowns, when real slippage is worse than the flat assumption used here).

**Verdict: first strategy in three research passes to clear every validation gate applied so far** (full-history, parameter sensitivity, cost stress, walk-forward, execution-timing, cross-asset generalization). Promising — recommend paper-trading validation before live capital, not immediate deployment.

## 19. Portfolio construction: combining BTC/ETH/SOL/BNB (`vbt_portfolio.py`)

Section 18 tested each asset independently at 100% capital — not a real allocation. Combined all 4 into one account using the same fixed signal (atr_mult=4.0, trend_w=50, no re-fitting), with per-day capital split among assets currently in-position, next-day execution, turnover-based rebalance cost (18bps base tier):

| Scheme | CAGR | Sharpe | MaxDD |
|---|---|---|---|
| Equal weight among active (cap 25% each) | **73.0%** | **1.21** | -65.9% |
| Inverse-vol weight among active | 57.0% | 1.08 | -66.1% |
| Static 25/25/25/25 buy-and-hold (benchmark) | 67.4% | 1.08 | -81.2% |
| BTC-only chandelier (section 18 baseline) | 46.5% | 1.14 | -60.5% |

Equal-weight-among-active beats every benchmark on both CAGR and Sharpe, and cuts MaxDD by 15 points vs. the static buy-and-hold basket. Inverse-vol weighting underperforms here (opposite of the momentum-overlay lesson in section 13) — the trend/chandelier signal already avoids the worst regimes, so down-weighting the highest-vol (typically highest-momentum, e.g. SOL) asset just leaves return on the table.

**Concentration risk check**: return correlation among the 4 assets is 0.55-0.81 (BTC/ETH highest at 0.81). Signals fire together often — all 4 active simultaneously on 692/2151 days (32%), all 4 flat on 674/2151 days (31%). This is genuine concentration, not diversification-in-name-only: when the regime turns, the portfolio is correlated risk-on or risk-off, not spread across uncorrelated bets. The MaxDD (-65.9%) reflects this — worse than BTC-only in absolute terms, though still far better than static buy-and-hold's -81.2%.

## 20. Stop-quality / execution-granularity check (`vbt_stop_quality.py`)

Two checks on whether the daily-bar backtest hides bad chandelier-stop fills:

**Intrabar breach check**: of 1,311 BTC held-days, only 2 (0.2%) had the daily low pierce the chandelier stop level while the close stayed above it — negligible hidden whipsaw. On those 2 days, price recovered a median 1.46% above the stop intraday, i.e. trivial noise, not a missed stop-out.

**Finer-granularity replay**: re-ran the identical signal (windows scaled 6x: trend_w=300, ATR window=84, extreme window=132 four-hour bars) on 4h BTC data:

| Bars | CAGR | Sharpe | MaxDD | Trades |
|---|---|---|---|---|
| 1d | 47.9% | 1.17 | -58.8% | 66 |
| 4h | 28.3% | 0.89 | -51.8% | 222 |

The 4h version still beats BTC buy-and-hold (28.1% CAGR, Sharpe 0.71) on both CAGR (roughly matched) and Sharpe (0.89 vs 0.71), with a much shallower MaxDD (-51.8% vs -76.6%) — extra trading (222 vs 66) drags CAGR down from the daily version's 47.9% but the edge doesn't disappear or invert. **No sign the daily result is an artifact of coarse bars hiding bad fills.**

## 21. Third-pass conclusion: candidate strategy identified, portfolio-level spec below

**A profitable, validated candidate strategy exists**: chandelier-exit ATR trailing stop + 50d SMA trend gate, traded across BTC/ETH/SOL/BNB with equal-weight-among-active sizing (25% cap per asset). Full validation chain passed: full-history backtest, 48-cell parameter sensitivity (no cliff), cost stress test (survives at all 4 tiers), year-by-year walk-forward (beats buy-and-hold 6/8 years), next-day-open execution check (rules out lookahead), cross-asset generalization with fixed params (no per-asset refitting), portfolio-level combination (beats both the single-asset and static-basket benchmarks), and intraday/finer-granularity replay (no hidden stop-fill artifact).

**Live-trading specification:**
- **Universe**: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT spot, equal-weight among currently-active positions, capped 25% notional each.
- **Timeframe**: 1d signal, execute at next day's open.
- **Signal per asset**: long/active when `close > SMA_50(close)` AND `close > rolling_max_22(close) - 4.0 * ATR_14`; flat otherwise.
- **Rebalancing**: daily check; trade only on regime flips (entry/exit) or weight drift from other assets' flips, not every day.
- **Cost assumptions**: base tier (10bps taker + 3bps spread + 5bps slippage, one-way) plus turnover-based rebalance cost.
- **Expected behavior**: outperforms buy-and-hold in trending and crash years; underperforms slightly in the strongest pure-bull years (e.g. 2023-24 BTC) since it isn't always 100% invested; MaxDD still substantial (-66%) since signals are correlated across the 4 assets (0.55-0.81 correlation) — this is not a market-neutral or low-vol strategy, it's a better-managed long-only trend strategy.
- **Known limitations before live deployment**: (a) 18bps cost assumption not empirically measured against real order books; (b) no true out-of-sample data beyond 2026-07; (c) correlation-driven concentration means the portfolio can still be 100% risk-on into a broad-market reversal; (d) inverse-vol sizing was tested and found worse than equal-weight here, opposite of the section-13 momentum finding — sizing scheme is signal-dependent, don't assume it transfers.
- **Recommended next step**: paper-trade this exact specification forward in real time before committing capital. This is the strongest candidate found across three research passes but has not been tested on data the strategy's own construction didn't see.

Recommendation unchanged from pass 1, now with more evidence: **passive BTC/ETH exposure beats every active strategy tested; further research should either target fundamentally different information (order-book, funding, on-chain, cross-exchange) or stop here.**
