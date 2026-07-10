"""Intra-day stop-quality check: does the daily-bar chandelier backtest hide
bad fills? Daily bars only see close-to-close; a chandelier stop level could
be pierced and recovered within a day (whipsaw the daily bar can't see), or
the exit fill could be far worse than assumed if the stop triggers mid-candle
during a fast move (gap-through, not a clean touch).

Approach: recompute the same signal on 4h bars (same lookback windows scaled
by 6x since 4h = 1/6 of a day: trend_w=300 four-hour bars ~= 50 days, ATR
window 84 ~= 14 days, extreme window 132 ~= 22 days) and compare CAGR/Sharpe/
MaxDD/trade count against the daily version. If the edge survives at finer
granularity with realistic per-bar execution, the daily result is not an
artifact of coarse bars hiding bad stop fills.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

FEES = 0.0010
SLIPPAGE = 0.0008
DATA_DIR_4H = Path("data/processed/binance/spot/klines/interval=4h")
DATA_DIR_1D = Path("data/processed/binance/spot/klines/interval=1d")
START = "2019-07-01"
END = "2026-07-01"


def load(data_dir: Path, symbol: str, col: str) -> pd.Series:
    df = pd.read_parquet(data_dir / f"symbol={symbol}" / "data.parquet").set_index("open_time")
    s = df[col].astype(float)
    s.index = pd.to_datetime(s.index, utc=True).tz_localize(None)
    return s.loc[START:END]


def run_chandelier(close, high, low, open_, atr_mult, trend_w, atr_w, extreme_w, bars_per_year):
    atr = vbt.ATR.run(high, low, close, window=atr_w).atr
    trend_ma = close.rolling(trend_w).mean()
    long_signal = close > trend_ma
    stop = close.rolling(extreme_w).max() - atr_mult * atr
    in_pos = (long_signal & (close > stop)).fillna(False)
    entries = (in_pos & ~in_pos.shift(1).fillna(False)).shift(1).fillna(False).astype(bool)
    exits = (~in_pos & in_pos.shift(1).fillna(False)).shift(1).fillna(False).astype(bool)
    freq = pd.Timedelta(days=365) / bars_per_year
    pf = vbt.Portfolio.from_signals(
        open_.values, entries.values, exits.values, fees=FEES, slippage=SLIPPAGE, init_cash=100.0,
        freq=freq,
    )
    n_bars = len(close)
    years = n_bars / bars_per_year
    tr = pf.total_return()
    tr = float(np.ravel(tr)[0]) if hasattr(tr, "__len__") else float(tr)
    cagr = (1 + tr) ** (1 / years) - 1
    stats = pf.stats()
    return cagr, stats.get("Sharpe Ratio", np.nan), stats.get("Max Drawdown [%]", np.nan), stats.get("Total Trades", 0)


def main():
    print("=== Stop-quality check: daily vs 4h bar granularity, BTC ===\n")

    # Daily baseline (reproduce pass-3 result for reference)
    c, h, l, o = load(DATA_DIR_1D, "BTCUSDT", "close"), load(DATA_DIR_1D, "BTCUSDT", "high"), \
                 load(DATA_DIR_1D, "BTCUSDT", "low"), load(DATA_DIR_1D, "BTCUSDT", "open")
    cagr, sharpe, dd, trades = run_chandelier(c, h, l, o, 4.0, 50, 14, 22, bars_per_year=365)
    print(f"1d  bars: CAGR={cagr:.1%} Sharpe={sharpe:.2f} MaxDD={dd:.1f}% trades={trades}")

    # 4h version: scale windows by 6 (6 x 4h bars = 1 day)
    c4, h4, l4, o4 = load(DATA_DIR_4H, "BTCUSDT", "close"), load(DATA_DIR_4H, "BTCUSDT", "high"), \
                      load(DATA_DIR_4H, "BTCUSDT", "low"), load(DATA_DIR_4H, "BTCUSDT", "open")
    cagr4, sharpe4, dd4, trades4 = run_chandelier(c4, h4, l4, o4, 4.0, 50 * 6, 14 * 6, 22 * 6, bars_per_year=365 * 6)
    print(f"4h  bars: CAGR={cagr4:.1%} Sharpe={sharpe4:.2f} MaxDD={dd4:.1f}% trades={trades4}")

    # Intrabar stop breach check: for each daily bar where the position was
    # supposed to be open, did low pierce the chandelier stop level even
    # though close stayed above it (would mean the daily backtest missed a
    # stop-out that should have happened intrabar)?
    atr = vbt.ATR.run(h, l, c, window=14).atr
    trend_ma = c.rolling(50).mean()
    long_signal = c > trend_ma
    stop_level = c.rolling(22).max() - 4.0 * atr
    in_pos = (long_signal & (c > stop_level)).fillna(False)
    # Days where position is held (per daily close logic) but low < stop_level
    breach_intrabar = in_pos & (l < stop_level)
    n_breach = int(breach_intrabar.sum())
    n_held = int(in_pos.sum())
    print(f"\nIntrabar stop-breach check (daily bars): {n_breach} of {n_held} held-days "
          f"had low < chandelier stop while close stayed above it "
          f"({n_breach / max(n_held,1):.1%}) — these are potential 'hidden' whipsaws "
          f"the daily backtest doesn't react to.")

    # How much worse would a same-day stop-out (using low instead of close) have been?
    # Rough estimate: on breach days, what was the gap between stop_level and close?
    if n_breach:
        gap = (c[breach_intrabar] - stop_level[breach_intrabar]) / c[breach_intrabar]
        print(f"Median same-day close vs stop-level gap on breach days: {gap.median():.2%} "
              f"(close recovered above the stop by this much intraday)")


if __name__ == "__main__":
    main()
