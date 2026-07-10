"""Literature-informed variant: EMA-crossover entry + Chandelier(22, mult) exit,
instead of pass-3's SMA(50)-gate + Chandelier exit. Web research on published
crypto trend-following approaches (Chuck LeBeau's chandelier exit, default
22-period/3x-ATR) commonly pairs it with an EMA crossover entry rather than a
simple price > SMA gate. Testing whether that entry variant improves on the
already-validated pass-3 result, using the literature's default 3x ATR
multiplier as well as the pass-3 sweep's best cell (4x).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

FEES = 0.0010
SLIPPAGE = 0.0008
DATA_DIR = Path("data/processed/binance/spot/klines/interval=1d")
START = "2019-07-01"
END = "2026-07-01"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]


def load(symbol: str, col: str) -> pd.Series:
    df = pd.read_parquet(DATA_DIR / f"symbol={symbol}" / "data.parquet").set_index("open_time")
    s = df[col].astype(float)
    s.index = pd.to_datetime(s.index, utc=True).tz_localize(None)
    return s.loc[START:END]


def run(close, high, low, open_, fast_w, slow_w, atr_mult, extreme_w=22, atr_w=14):
    fast_ema = vbt.MA.run(close, fast_w, ewm=True).ma
    slow_ema = vbt.MA.run(close, slow_w, ewm=True).ma
    atr = vbt.ATR.run(high, low, close, window=atr_w).atr
    trend_up = fast_ema > slow_ema
    stop = close.rolling(extreme_w).max() - atr_mult * atr
    in_pos = (trend_up & (close > stop)).fillna(False)
    entries = (in_pos & ~in_pos.shift(1).fillna(False)).shift(1).fillna(False).astype(bool)
    exits = (~in_pos & in_pos.shift(1).fillna(False)).shift(1).fillna(False).astype(bool)
    pf = vbt.Portfolio.from_signals(open_.values, entries.values, exits.values, fees=FEES, slippage=SLIPPAGE, init_cash=100.0, freq="1D")
    years = (close.index[-1] - close.index[0]).days / 365.25
    tr = pf.total_return()
    tr = float(np.ravel(tr)[0]) if hasattr(tr, "__len__") else float(tr)
    cagr = (1 + tr) ** (1 / years) - 1
    stats = pf.stats()
    return cagr, stats.get("Sharpe Ratio", np.nan), stats.get("Max Drawdown [%]", np.nan), stats.get("Total Trades", 0)


def main():
    print("=== EMA-crossover entry + chandelier exit, vs pass-3 SMA-gate baseline ===\n")
    for sym in SYMBOLS:
        c, h, l, o = load(sym, "close"), load(sym, "high"), load(sym, "low"), load(sym, "open")
        bh_cagr = (c.iloc[-1] / c.iloc[0]) ** (1 / ((c.index[-1] - c.index[0]).days / 365.25)) - 1
        for fast_w, slow_w, mult, label in [
            (20, 50, 3.0, "ema20/50_atr3.0(literature-default)"),
            (20, 50, 4.0, "ema20/50_atr4.0"),
            (10, 50, 3.0, "ema10/50_atr3.0"),
        ]:
            cagr, sharpe, dd, trades = run(c, h, l, o, fast_w, slow_w, mult)
            print(f"{sym:10s} {label:35s} CAGR={cagr:6.1%} Sharpe={sharpe:5.2f} MaxDD={dd:6.1f}% trades={trades:4.0f} (buy_hold={bh_cagr:.1%})")
        print()


if __name__ == "__main__":
    main()
