"""Phase 7: walk-forward validation and cost-tier sensitivity for a single
strategy/universe combination.

Splits full history into non-overlapping calendar-year windows (chronological,
never shuffled) and reports per-year performance plus a rolling walk-forward
view (train window not literally "fit" here since btc_trend has only one
fixed parameter set, but the split still demonstrates whether performance is
consistent across distinct market regimes rather than driven by one period).

Also runs the strategy across all 4 cost tiers (optimistic/base/conservative/
stress) to test cost sensitivity, and across a grid of neighboring SMA
windows to test parameter-plateau stability (a strategy that only works at
one exact parameter is treated as suspect per the project's overfitting
guardrails).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from crypto_research.backtest.engine import load_cost_tier, load_liquidity_config, run_backtest
from crypto_research.backtest.metrics import summarize
from crypto_research.cli.research import _wide_price_frame
from crypto_research.strategies.btc_trend import btc_trend_target_weights

REPORT_DIR = Path("reports/tables")

YEAR_WINDOWS = [
    ("2019", "2019-07-01", "2019-12-31"),
    ("2020", "2020-01-01", "2020-12-31"),
    ("2021", "2021-01-01", "2021-12-31"),
    ("2022", "2022-01-01", "2022-12-31"),
    ("2023", "2023-01-01", "2023-12-31"),
    ("2024", "2024-01-01", "2024-12-31"),
    ("2025", "2025-01-01", "2025-12-31"),
    ("2026 YTD", "2026-01-01", "2026-07-08"),
]


def _run_btc_trend(start: str, end: str, cost_tier_name: str, sma_window: int, rebalance_freq: str = "D") -> dict:
    btc_close = _wide_price_frame(["BTCUSDT"], "close")["BTCUSDT"]
    btc_close = btc_close.sort_index()

    # Compute the SMA signal over the FULL history (not just this window),
    # since a walk-forward slice must not restart the rolling window at the
    # slice boundary -- that would bias the SMA to be undefined right at the
    # start of every sub-period. We slice AFTER the rolling computation.
    full_weights = btc_trend_target_weights(btc_close, sma_window=sma_window)

    dates = pd.date_range(start, end, freq=rebalance_freq, tz="UTC")
    target_weights = full_weights.reindex(dates).ffill().fillna(0.0)

    open_prices = _wide_price_frame(["BTCUSDT"], "open").reindex(target_weights.index)
    close_prices = _wide_price_frame(["BTCUSDT"], "close").reindex(target_weights.index)
    quote_vol = _wide_price_frame(["BTCUSDT"], "quote_asset_volume")
    trailing_vol = quote_vol.rolling(30).sum().reindex(target_weights.index)

    cost_tier = load_cost_tier(cost_tier_name)
    liquidity = load_liquidity_config()

    result = run_backtest(target_weights, open_prices, close_prices, trailing_vol, cost_tier, liquidity)
    perf = summarize(result.net_return, result.equity_curve)
    return {
        "final_equity": float(result.equity_curve.iloc[-1]),
        "n_trades": len(result.trade_log),
        "total_cost": float(result.fee_cost.sum() + result.slippage_cost.sum()),
        **vars(perf),
    }


def run_year_by_year(cost_tier_name: str, sma_window: int) -> pd.DataFrame:
    rows = []
    for label, start, end in YEAR_WINDOWS:
        perf = _run_btc_trend(start, end, cost_tier_name, sma_window)
        rows.append({"period": label, **perf})
    return pd.DataFrame(rows)


def run_cost_sensitivity(sma_window: int) -> pd.DataFrame:
    rows = []
    for tier in ["optimistic", "base", "conservative", "stress"]:
        perf = _run_btc_trend("2019-07-01", "2026-07-08", tier, sma_window)
        rows.append({"cost_tier": tier, **perf})
    return pd.DataFrame(rows)


def run_parameter_sensitivity(cost_tier_name: str) -> pd.DataFrame:
    rows = []
    for sma_window in [100, 150, 180, 200, 220, 250, 300]:
        perf = _run_btc_trend("2019-07-01", "2026-07-08", cost_tier_name, sma_window)
        rows.append({"sma_window": sma_window, **perf})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sma-window", type=int, default=200)
    parser.add_argument("--cost-tier", default="base")
    args = parser.parse_args()

    print("=== Year-by-year (walk-forward) performance, BTC 200d trend filter ===")
    yby = run_year_by_year(args.cost_tier, args.sma_window)
    print(yby[["period", "cagr", "sharpe", "max_drawdown", "n_trades"]].to_string(index=False))
    yby.to_csv(REPORT_DIR / "btc_trend_walkforward_by_year.csv", index=False)

    print("\n=== Cost-tier sensitivity ===")
    costs = run_cost_sensitivity(args.sma_window)
    print(costs[["cost_tier", "cagr", "sharpe", "max_drawdown", "total_cost", "final_equity"]].to_string(index=False))
    costs.to_csv(REPORT_DIR / "btc_trend_cost_sensitivity.csv", index=False)

    print("\n=== Parameter sensitivity (SMA window) ===")
    params = run_parameter_sensitivity(args.cost_tier)
    print(params[["sma_window", "cagr", "sharpe", "max_drawdown", "n_trades"]].to_string(index=False))
    params.to_csv(REPORT_DIR / "btc_trend_parameter_sensitivity.csv", index=False)


if __name__ == "__main__":
    main()
