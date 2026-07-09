"""End-to-end research runner: build universe -> features -> strategy weights
-> backtest -> metrics, for one strategy/universe/cost-tier combination.

This is the workhorse used by the Phase 7 walk-forward harness and by ad
hoc single-strategy runs. It intentionally avoids hidden global state: all
inputs are explicit arguments so a walk-forward loop can call it repeatedly
with different date windows without any leakage between calls.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from crypto_research.backtest.engine import (
    load_cost_tier,
    load_liquidity_config,
    run_backtest,
)
from crypto_research.backtest.metrics import summarize
from crypto_research.data.db import connect
from crypto_research.features.panel import build_feature_panel
from crypto_research.strategies.btc_trend import btc_trend_target_weights
from crypto_research.strategies.cross_sectional_momentum import (
    build_cross_sectional_momentum_weights,
)
from crypto_research.strategies.mean_reversion import build_mean_reversion_weights
from crypto_research.strategies.volume_breakout import build_volume_breakout_weights
from crypto_research.universe.builder import (
    build_universe,
    discover_all_symbols,
    load_config,
    load_daily_panel,
)

PROCESSED_ROOT = Path("data/processed/binance/spot/klines")
REPORT_DIR = Path("reports/tables")


def _wide_price_frame(symbols: list[str], column: str) -> pd.DataFrame:
    con = connect()
    frames = {}
    for symbol in symbols:
        path = PROCESSED_ROOT / "interval=1d" / f"symbol={symbol}" / "data.parquet"
        if not path.exists():
            continue
        df = con.execute(f"SELECT open_time, {column} FROM read_parquet('{path.as_posix()}')").df()
        frames[symbol] = df.set_index("open_time")[column]
    return pd.DataFrame(frames)


def buy_and_hold_weights(symbols: list[str], dates: pd.DatetimeIndex, per_symbol_weight: float | None = None) -> pd.DataFrame:
    w = per_symbol_weight if per_symbol_weight is not None else 1.0 / len(symbols)
    return pd.DataFrame(w, index=dates, columns=symbols)


def run_strategy(
    strategy: str,
    universe_id: str,
    cost_tier_name: str,
    start: str,
    end: str,
    rebalance_freq: str = "W",
    top_n: int = 10,
) -> dict:
    rebalance_dates = list(pd.date_range(start, end, freq=rebalance_freq, tz="UTC"))

    con = connect()
    load_daily_panel(con)
    config = load_config()
    all_symbols = discover_all_symbols()

    universe_map_raw = build_universe(universe_id, rebalance_dates, con, config, all_symbols)
    universe_by_date = {pd.Timestamp(k): v for k, v in universe_map_raw.items()}

    symbols_needed = sorted({s for syms in universe_by_date.values() for s in syms}) or ["BTCUSDT", "ETHUSDT"]

    if strategy == "btc_trend":
        btc_close = _wide_price_frame(["BTCUSDT"], "close")["BTCUSDT"]
        target_weights = btc_trend_target_weights(btc_close)
        target_weights = target_weights.reindex(rebalance_dates).ffill().fillna(0.0)
        universe_symbols = ["BTCUSDT"]
    elif strategy.startswith("cross_sectional_momentum") or strategy == "mean_reversion":
        panel = build_feature_panel(symbols_needed)
        if strategy == "mean_reversion":
            target_weights = build_mean_reversion_weights(panel, universe_by_date, bottom_n=top_n)
        else:
            # Variant flags encoded in the strategy name suffix.
            momentum_col = "momentum_30d_skip7" if "skipweek" in strategy else "momentum_30d"
            target_weights = build_cross_sectional_momentum_weights(
                panel,
                universe_by_date,
                momentum_col=momentum_col,
                top_n=top_n,
                require_btc_uptrend="gated" in strategy,
                weighting="inverse_vol" if "ivol" in strategy else "equal",
            )
        universe_symbols = symbols_needed
    elif strategy.startswith(("volume_breakout", "donchian_breakout")):
        panel = build_feature_panel(symbols_needed)
        if strategy.startswith("donchian_breakout"):
            confirm = "none"
        elif "volz" in strategy:
            confirm = "volume_z"
        else:
            confirm = "taker"
        target_weights = build_volume_breakout_weights(
            panel,
            universe_by_date,
            confirm=confirm,
            max_positions=top_n,
            require_btc_uptrend="gated" in strategy,
        )
        universe_symbols = symbols_needed
    elif strategy == "buy_and_hold_btc":
        universe_symbols = ["BTCUSDT"]
        target_weights = buy_and_hold_weights(universe_symbols, pd.DatetimeIndex(rebalance_dates))
    elif strategy == "buy_and_hold_eth":
        universe_symbols = ["ETHUSDT"]
        target_weights = buy_and_hold_weights(universe_symbols, pd.DatetimeIndex(rebalance_dates))
    elif strategy == "buy_and_hold_50_50":
        universe_symbols = ["BTCUSDT", "ETHUSDT"]
        target_weights = buy_and_hold_weights(universe_symbols, pd.DatetimeIndex(rebalance_dates), per_symbol_weight=0.5)
    elif strategy == "equal_weight_universe":
        universe_symbols = symbols_needed
        target_weights = pd.DataFrame(0.0, index=pd.DatetimeIndex(rebalance_dates), columns=universe_symbols)
        for d, syms in universe_by_date.items():
            if syms:
                target_weights.loc[d, syms] = 1.0 / len(syms)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")

    open_prices = _wide_price_frame(universe_symbols, "open").reindex(target_weights.index)
    close_prices = _wide_price_frame(universe_symbols, "close").reindex(target_weights.index)
    quote_vol = _wide_price_frame(universe_symbols, "quote_asset_volume")
    trailing_vol = quote_vol.rolling(30).sum().reindex(target_weights.index)

    cost_tier = load_cost_tier(cost_tier_name)
    liquidity = load_liquidity_config()

    result = run_backtest(target_weights, open_prices, close_prices, trailing_vol, cost_tier, liquidity)
    perf = summarize(result.net_return, result.equity_curve)

    return {
        "strategy": strategy,
        "universe": universe_id,
        "cost_tier": cost_tier_name,
        "start": start,
        "end": end,
        "final_equity": float(result.equity_curve.iloc[-1]),
        "n_trades": len(result.trade_log),
        "total_fee_cost": float(result.fee_cost.sum()),
        "total_slippage_cost": float(result.slippage_cost.sum()),
        "avg_turnover_per_rebalance": float(result.turnover.mean()) if len(result.turnover) else 0.0,
        "capacity_breaches": len(result.capacity_breaches),
        "performance": vars(perf),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--cost-tier", default="base")
    parser.add_argument("--start", default="2019-07-01")
    parser.add_argument("--end", default="2026-07-01")
    parser.add_argument("--freq", default="W")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    result = run_strategy(
        args.strategy, args.universe, args.cost_tier, args.start, args.end, args.freq, args.top_n
    )

    out_path = Path(args.out) if args.out else REPORT_DIR / f"result_{args.strategy}_{args.universe}_{args.cost_tier}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
