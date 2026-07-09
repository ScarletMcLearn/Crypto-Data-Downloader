I have a Binance spot crypto research repo at `I:\Projects\Data-Science\Binance\1`. A previous session built the full data pipeline and tested baseline strategies; all of them lost to buy-and-hold. I want to continue the research with more sophisticated signal combinations before concluding no edge exists. Read `reports/strategy_report.md` first for full context, then proceed.

## What existed before this project
- `data/raw/binance/spot/{monthly,daily}/klines/<SYMBOL>/{1d,4h,1h}/*.zip` — 447 USDT spot pairs, 2019-2026, 888MB compressed.
- `data/raw/binance/spot/current_universe.json` — 447 currently-trading symbols (survivorship-biased if used naively).
- `uv`-managed Python env (pinned deps in `pyproject.toml`/`uv.lock`), plus a `pixi.toml` wrapper.

## What's done (all verified working, not just written)
1. **Data audit** (`src/crypto_research/cli/audit.py`) — scanned all 64,056 ZIP files. Clean data, but confirmed: timestamp units switch ms→µs exactly at 2025-01 (handled per-file), 33% of symbols listed only in 2025-2026, 24 tokenized-stock symbols (TSLAB, NVDAB, GOOGLB, etc.) and 7 stablecoins that must be excluded (`configs/universes.yaml`), survivorship bias confirmed real in `current_universe.json`.
2. **Processed Parquet layer** (`src/crypto_research/data/build.py`) — `data/processed/binance/spot/klines/interval=<i>/symbol=<s>/data.parquet`, resumable, provenance-tagged, deduped monthly/daily overlap. Built for 1d, 4h, 1h.
3. **Point-in-time universe construction** (`src/crypto_research/universe/builder.py`) — 6 universes (btc_eth, top10/20/50 by trailing 30d quote volume, broad_liquid, survivorship-biased benchmark), all using only `close_time <= t` data, verified no lookahead.
4. **Feature engineering** (`src/crypto_research/features/panel.py`, `cross_sectional.py`) — momentum (7/14/30/90d), ATR, realized vol, z-score, Donchian breakout distance, drawdown-from-peak, volume z-score, taker-buy ratio, BTC 200d trend regime, cross-sectional rank. All properly lagged (verified NaN warmup periods match window sizes exactly).
5. **Backtest engine** (`src/crypto_research/backtest/engine.py`) — vectorized, long-only, next-open execution (signal at close_t, fill at open_{t+1}), 4 cost tiers (`configs/costs.yaml`: optimistic/base/conservative/stress), liquidity caps from trailing volume, gross-vs-net NAV tracking. Validated against buy-and-hold sanity checks and a high-turnover stress test.
6. **Three baseline strategies tested** full history (2019-07 to 2026-07), weekly rebalance, base costs, across 17 strategy×universe combos:
   - **BTC 200d SMA trend filter**: CAGR 20.3%, Sharpe 0.63, avoided the entire 2022 bear market (0% return that year) but trails buy-and-hold BTC overall. Survived walk-forward (year-by-year 2019-2026), cost-tier sensitivity, and parameter sensitivity (SMA 100-300d all reasonable, no narrow lucky value). **Classified: exploratory / promising but unproven.**
   - **Cross-sectional momentum** (rank by 30d trailing return, hold top-N, equal weight): **rejected** — lost 90-99.6% of capital on top50/broad_liquid universes. Verified as genuine strategy failure via year-by-year equity decay, not an engine bug.
   - **Mean reversion** (buy lowest 20d z-score names): **rejected** — lost 90-97% of capital on every universe, worst in the 2020 COVID crash ("catching falling knives").
   - All benchmarks (buy-and-hold BTC/ETH/50-50, equal-weight rebalance) also computed for comparison.
7. One real bug found and fixed: DuckDB defaulted to local system timezone instead of UTC on `.df()` export. Fixed via `src/crypto_research/data/db.py`'s `connect()` helper (`SET TimeZone='UTC'`). Confirmed the blast radius was limited (tz-aware comparisons are instant-safe; only exact-index reindexing was at risk) and no prior results were corrupted.

Full report: `reports/strategy_report.md`. Comparison table: `reports/tables/strategy_comparison.csv`. Charts: `reports/figures/{equity_curves,drawdowns,btc_trend_monthly_heatmap}.png`.

## What I want you to check next
The three baseline signals were tested in isolation, daily bars, weekly rebalance only. Before concluding no edge exists, test these in order, rejecting anything that doesn't survive costs the same way the last session did (be honest, don't oversell):

1. **BTC-trend-gated cross-sectional momentum**: only take momentum long positions when `btc_trend_up` is true (skip entirely when BTC is below its 200d SMA). This directly targets momentum's worst drawdowns, which happened in choppy/declining regimes. Momentum crashed hardest on top50/broad_liquid — retest those universes with the gate applied.
2. **Rebalance cadence sensitivity for momentum**: weekly rebalance may have been a bad choice (excessive turnover/costs). Retest cross-sectional momentum at monthly rebalance with a skip-most-recent-week convention (standard momentum literature practice) before rejecting the signal itself vs. rejecting the implementation.
3. **Higher-frequency mean reversion on 4h/1h bars**: the 4h/1h processed Parquet data already exists and is audited but was never used for signals. Test a short-horizon z-score reversion (e.g. 20-period z-score on 4h bars) — daily reversion "catching falling knives" may not generalize to intraday reversion, which operates on a different noise/signal regime.
4. **Volume/order-flow features as signals**: `taker_buy_ratio` and `quote_volume_zscore` are computed in the feature panel but never used as standalone or combined signals. Test volume-confirmed breakouts (Donchian breakout + taker-buy imbalance confirmation) as a new strategy family.
5. **Position sizing**: retest any signal that shows a positive but noisy edge with inverse-volatility weighting instead of equal weight, since equal-weight punishes low-hit-rate/high-tail-loss strategies harshly.

For each, follow the same validation bar as before: full-history backtest across relevant universes, reject if it loses capital or fails to beat buy-and-hold after base costs, only proceed to walk-forward/cost-sensitivity for anything that clears that bar. Update `reports/strategy_report.md` with new sections rather than rewriting it, and be willing to conclude "still no edge" if that's what the evidence shows.
