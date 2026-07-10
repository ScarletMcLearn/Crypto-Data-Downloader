"""Cross-check the pass-3 chandelier strategy in an independent engine
(Backtesting.py) instead of VectorBT, to catch engine-specific bugs (numba
signal-shift errors, off-by-one fills, etc.) that a single-engine result
can't rule out on its own.

Same signal as vbt_chandelier_wf.py: long when close > SMA(50) AND
close > rolling_max(22) - 4.0*ATR(14); flat otherwise. Same cost tier
(18bps one-way approximated as commission+slippage).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy

DATA_DIR = Path("data/processed/binance/spot/klines/interval=1d")
START = "2019-07-01"
END = "2026-07-01"
COMMISSION = 0.0018  # approximate one-way 18bps round-tripped into Backtesting.py's per-trade commission


def load_ohlc(symbol: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA_DIR / f"symbol={symbol}" / "data.parquet").set_index("open_time")
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
    df = df.loc[START:END, ["open", "high", "low", "close", "volume"]].astype(float)
    df.columns = ["Open", "High", "Low", "Close", "Volume"]
    return df


def atr(high, low, close, window=14):
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    return pd.Series(tr).rolling(window).mean().values


class ChandelierTrend(Strategy):
    trend_w = 50
    atr_mult = 4.0
    atr_w = 14
    extreme_w = 22

    def init(self):
        close = self.data.Close
        high = self.data.High
        low = self.data.Low
        self.sma = self.I(lambda x: pd.Series(x).rolling(self.trend_w).mean().values, close)
        self.atr = self.I(atr, high, low, close, self.atr_w)
        self.rolling_max = self.I(lambda x: pd.Series(x).rolling(self.extreme_w).max().values, close)

    def next(self):
        price = self.data.Close[-1]
        stop = self.rolling_max[-1] - self.atr_mult * self.atr[-1]
        want_long = price > self.sma[-1] and price > stop

        if want_long and not self.position:
            self.buy()
        elif not want_long and self.position:
            self.position.close()


def main():
    for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]:
        df = load_ohlc(symbol)
        bt = Backtest(df, ChandelierTrend, cash=100_000, commission=COMMISSION, exclusive_orders=True)
        stats = bt.run()
        bh_return = (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
        years = (df.index[-1] - df.index[0]).days / 365.25
        bh_cagr = ((1 + bh_return / 100) ** (1 / years) - 1) * 100
        print(
            f"{symbol}: strat CAGR={stats['Return (Ann.) [%]']:.1f}% "
            f"Sharpe={stats['Sharpe Ratio']:.2f} MaxDD={stats['Max. Drawdown [%]']:.1f}% "
            f"trades={stats['# Trades']} | buy_hold CAGR={bh_cagr:.1f}%"
        )


if __name__ == "__main__":
    main()
