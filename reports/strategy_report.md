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
