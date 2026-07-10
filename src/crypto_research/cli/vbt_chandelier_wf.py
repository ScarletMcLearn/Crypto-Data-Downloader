"""Walk-forward + parameter-sensitivity validation for the chandelier-exit
trend strategy found in vbt_research.py (ATR trailing stop + MA trend gate,
BTC-only). Full-history result looked strong (48% CAGR, Sharpe 1.17 vs BTC
buy-hold 28%/0.71) -- validate before trusting it, per this project's
established bar (pass 2's breakout family looked great full-history and
turned out to be a regime artifact).
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


def load(symbol: str, col: str) -> pd.Series:
    df = pd.read_parquet(DATA_DIR / f"symbol={symbol}" / "data.parquet").set_index("open_time")
    s = df[col].astype(float)
    s.index = pd.to_datetime(s.index, utc=True).tz_localize(None)
    return s.loc[START:END]


def run_chandelier(close, high, low, atr_mult, trend_w, atr_w=14, extreme_w=22):
    atr = vbt.ATR.run(high, low, close, window=atr_w).atr
    trend_ma = close.rolling(trend_w).mean()
    long_signal = close > trend_ma
    chandelier_stop = close.rolling(extreme_w).max() - atr_mult * atr
    in_pos = long_signal & (close > chandelier_stop)
    entries = in_pos & ~in_pos.shift(1).fillna(False)
    exits = ~in_pos & in_pos.shift(1).fillna(False)
    return vbt.Portfolio.from_signals(close, entries, exits, fees=FEES, slippage=SLIPPAGE, init_cash=100.0)


def main():
    btc = load("BTCUSDT", "close")
    high = load("BTCUSDT", "high")
    low = load("BTCUSDT", "low")

    print("=== Parameter sensitivity (full history, base costs) ===")
    rows = []
    for atr_mult in [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]:
        for trend_w in [30, 40, 50, 60, 75, 100]:
            pf = run_chandelier(btc, high, low, atr_mult, trend_w)
            years = (btc.index[-1] - btc.index[0]).days / 365.25
            tr = pf.total_return()
            tr = float(np.ravel(tr)[0]) if hasattr(tr, "__len__") else float(tr)
            cagr = (1 + tr) ** (1 / years) - 1
            stats = pf.stats()
            rows.append(
                {
                    "atr_mult": atr_mult,
                    "trend_w": trend_w,
                    "cagr": cagr,
                    "sharpe": stats.get("Sharpe Ratio", np.nan),
                    "max_dd": stats.get("Max Drawdown [%]", np.nan),
                    "trades": stats.get("Total Trades", 0),
                }
            )
    sens = pd.DataFrame(rows)
    pd.set_option("display.width", 160)
    print(sens.to_string(index=False))
    sens.to_csv("reports/tables/chandelier_sensitivity.csv", index=False)

    print("\n=== Walk-forward year-by-year (atr_mult=4.0, trend_w=50) ===")
    yearly = []
    for year in range(2019, 2027):
        y_start = f"{year}-01-01"
        y_end = f"{year}-12-31"
        # Need lookback history for MA/ATR warmup, so compute on full series
        # then slice the equity curve to the year.
        pf = run_chandelier(btc, high, low, 4.0, 50)
        equity = pf.value()
        if hasattr(equity, "columns"):
            equity = equity.iloc[:, 0]
        year_eq = equity.loc[y_start:y_end]
        if len(year_eq) < 2:
            continue
        year_ret = year_eq.iloc[-1] / year_eq.iloc[0] - 1
        yearly.append({"year": year, "return": year_ret})
    yr_df = pd.DataFrame(yearly)
    print(yr_df.to_string(index=False))
    yr_df.to_csv("reports/tables/chandelier_yearly.csv", index=False)

    print("\n=== Cost stress test (atr_mult=4.0, trend_w=50) ===")
    for tier, fees, slip in [
        ("optimistic", 0.0004, 0.0003),
        ("base", 0.0010, 0.0008),
        ("conservative", 0.0010, 0.0023),
        ("stress", 0.0010, 0.0055),
    ]:
        atr = vbt.ATR.run(high, low, btc, window=14).atr
        trend_ma = btc.rolling(50).mean()
        long_signal = btc > trend_ma
        chandelier_stop = btc.rolling(22).max() - 4.0 * atr
        in_pos = long_signal & (btc > chandelier_stop)
        entries = in_pos & ~in_pos.shift(1).fillna(False)
        exits = ~in_pos & in_pos.shift(1).fillna(False)
        pf = vbt.Portfolio.from_signals(btc, entries, exits, fees=fees, slippage=slip, init_cash=100.0)
        years = (btc.index[-1] - btc.index[0]).days / 365.25
        tr = pf.total_return()
        tr = float(np.ravel(tr)[0]) if hasattr(tr, "__len__") else float(tr)
        cagr = (1 + tr) ** (1 / years) - 1
        print(f"{tier}: CAGR={cagr:.1%}")


if __name__ == "__main__":
    main()
