"""BTC trend-following: long-only risk-on/risk-off exposure to BTC.

Signal: BTC close (at t) vs its trailing simple moving average (at t).
  - close_t > sma_t  -> risk-on: hold 100% BTC
  - close_t <= sma_t -> risk-off: hold 0% BTC (cash)

Execution: the signal is observed at candle t's close; the resulting
target weight is applied starting at candle (t+1)'s open (see
crypto_research.backtest.engine module docstring for the shared execution
convention). This is the simplest possible trend filter and is used here
as an economically-motivated baseline, not a "final answer".
"""

from __future__ import annotations

import pandas as pd


def btc_trend_target_weights(btc_close: pd.Series, sma_window: int = 200) -> pd.DataFrame:
    """Returns a single-column ('BTCUSDT') DataFrame of target weights
    indexed by the same dates as btc_close.
    """
    sma = btc_close.rolling(sma_window).mean()
    weight = (btc_close > sma).astype(float)
    weight[sma.isna()] = 0.0  # no signal until the SMA has enough history
    return weight.to_frame("BTCUSDT")
