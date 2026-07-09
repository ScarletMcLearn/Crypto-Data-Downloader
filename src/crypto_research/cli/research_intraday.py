"""Short-horizon mean reversion on intraday (4h/1h) bars.

Signal (as of bar t): z-score of close vs its rolling `zscore_window`-bar
mean; buy an equal-weight basket of the `bottom_n` most oversold eligible
symbols with z <= max_zscore, re-evaluated every bar. Execution follows the
project-wide convention: signal at bar t's close, fill at bar (t+1)'s open
(applied by the engine).

Universe: the same point-in-time monthly universes as the daily studies
(built from daily data), forward-filled onto the intraday grid — intraday
bars never see a universe decided with future information.

Liquidity: trailing 30-day quote volume computed from the intraday bars
themselves (rolling sum over 30d worth of bars), same convention as the
daily runner (rolling 30d SUM passed to the engine's capacity cap).
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
from crypto_research.universe.builder import (
    build_universe,
    discover_all_symbols,
    load_config,
    load_daily_panel,
)

PROCESSED_ROOT = Path("data/processed/binance/spot/klines")
REPORT_DIR = Path("reports/tables")

BARS_PER_DAY = {"4h": 6, "1h": 24}


def _wide_intraday_frame(symbols: list[str], interval: str, column: str) -> pd.DataFrame:
    con = connect()
    frames = {}
    for symbol in symbols:
        path = PROCESSED_ROOT / f"interval={interval}" / f"symbol={symbol}" / "data.parquet"
        if not path.exists():
            continue
        df = con.execute(
            f"SELECT open_time, {column} FROM read_parquet('{path.as_posix()}') ORDER BY open_time"
        ).df()
        frames[symbol] = df.set_index("open_time")[column]
    return pd.DataFrame(frames)


def run_intraday_reversion(
    interval: str,
    universe_id: str,
    cost_tier_name: str,
    start: str,
    end: str,
    zscore_window: int = 20,
    bottom_n: int = 10,
    max_zscore: float = -1.0,
) -> dict:
    bars_per_day = BARS_PER_DAY[interval]
    grid = pd.date_range(start, end, freq=interval.replace("h", "h"), tz="UTC")

    # Monthly point-in-time universe from daily data, ffilled to the grid.
    monthly_dates = list(pd.date_range(start, end, freq="MS", tz="UTC"))
    con = connect()
    load_daily_panel(con)
    config = load_config()
    all_symbols = discover_all_symbols()
    universe_map = build_universe(universe_id, monthly_dates, con, config, all_symbols)
    universe_by_month = {pd.Timestamp(k): v for k, v in universe_map.items()}

    symbols = sorted({s for syms in universe_by_month.values() for s in syms})
    if not symbols:
        raise ValueError("Empty universe")

    close = _wide_intraday_frame(symbols, interval, "close")
    open_ = _wide_intraday_frame(symbols, interval, "open")
    qvol = _wide_intraday_frame(symbols, interval, "quote_asset_volume")

    close = close.reindex(grid)
    open_ = open_.reindex(grid)
    qvol = qvol.reindex(grid)

    # Membership mask on the intraday grid (ffill monthly membership).
    member = pd.DataFrame(False, index=grid, columns=symbols)
    month_starts = sorted(universe_by_month.keys())
    for i, mdate in enumerate(month_starts):
        upper = month_starts[i + 1] if i + 1 < len(month_starts) else grid[-1] + pd.Timedelta(seconds=1)
        in_window = (member.index >= mdate) & (member.index < upper)
        syms = [s for s in universe_by_month[mdate] if s in member.columns]
        if syms:
            member.loc[in_window, syms] = True

    # Rolling z-score of close, per symbol, on the intraday grid. NaN closes
    # (gaps/pre-listing) stay NaN and are never selected.
    rmean = close.rolling(zscore_window, min_periods=zscore_window).mean()
    rstd = close.rolling(zscore_window, min_periods=zscore_window).std()
    z = (close - rmean) / rstd

    eligible = member & z.notna() & (z <= max_zscore)
    ranked = z.where(eligible).rank(axis=1, ascending=True)
    selected = ranked <= bottom_n
    counts = selected.sum(axis=1)
    target_weights = selected.astype(float).div(counts.replace(0, pd.NA), axis=0).fillna(0.0)

    trailing_vol = qvol.rolling(30 * bars_per_day, min_periods=bars_per_day).sum()

    cost_tier = load_cost_tier(cost_tier_name)
    liquidity = load_liquidity_config()

    result = run_backtest(target_weights, open_, close, trailing_vol, cost_tier, liquidity)
    perf = summarize(result.net_return, result.equity_curve)
    gross_perf = summarize(result.gross_return)

    return {
        "strategy": f"intraday_mean_reversion_{interval}",
        "universe": universe_id,
        "cost_tier": cost_tier_name,
        "interval": interval,
        "zscore_window": zscore_window,
        "bottom_n": bottom_n,
        "max_zscore": max_zscore,
        "start": start,
        "end": end,
        "final_equity": float(result.equity_curve.iloc[-1]),
        "n_trades": len(result.trade_log),
        "total_fee_cost": float(result.fee_cost.sum()),
        "total_slippage_cost": float(result.slippage_cost.sum()),
        "avg_turnover_per_rebalance": float(result.turnover.mean()) if len(result.turnover) else 0.0,
        "capacity_breaches": len(result.capacity_breaches),
        "performance": vars(perf),
        "gross_performance": vars(gross_perf),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", choices=["4h", "1h"], default="4h")
    parser.add_argument("--universe", default="top20")
    parser.add_argument("--cost-tier", default="base")
    parser.add_argument("--start", default="2019-07-01")
    parser.add_argument("--end", default="2026-07-01")
    parser.add_argument("--zscore-window", type=int, default=20)
    parser.add_argument("--bottom-n", type=int, default=10)
    parser.add_argument("--max-zscore", type=float, default=-1.0)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    result = run_intraday_reversion(
        args.interval,
        args.universe,
        args.cost_tier,
        args.start,
        args.end,
        args.zscore_window,
        args.bottom_n,
        args.max_zscore,
    )

    out_path = (
        Path(args.out)
        if args.out
        else REPORT_DIR / f"result_intraday_reversion_{args.interval}_{args.universe}_{args.cost_tier}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
