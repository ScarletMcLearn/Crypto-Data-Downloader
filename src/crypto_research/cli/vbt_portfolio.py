"""Portfolio-level chandelier strategy: combine BTC/ETH/SOL/BNB with real
position sizing instead of testing each asset at 100% capital independently.

Prior check (vbt_chandelier_wf.py) proved the per-asset signal generalizes.
This answers the next question: what happens when you actually have to
split one account across 4 correlated assets whose signals often fire
together (trend regimes are broad-market, not idiosyncratic)?

Two sizing schemes compared against equal-weight-when-active:
1. Equal weight among currently-active (in-position) assets, capped at 25% each.
2. Inverse-vol weight among currently-active assets (lesson from pass 2).
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
ATR_MULT = 4.0
TREND_W = 50


def load(symbol: str, col: str) -> pd.Series:
    df = pd.read_parquet(DATA_DIR / f"symbol={symbol}" / "data.parquet").set_index("open_time")
    s = df[col].astype(float)
    s.index = pd.to_datetime(s.index, utc=True).tz_localize(None)
    return s.loc[START:END]


def chandelier_in_position(close, high, low) -> pd.Series:
    atr = vbt.ATR.run(high, low, close, window=14).atr
    trend_ma = close.rolling(TREND_W).mean()
    long_signal = close > trend_ma
    stop = close.rolling(22).max() - ATR_MULT * atr
    return (long_signal & (close > stop)).fillna(False)


def main() -> None:
    closes, in_pos = {}, {}
    for sym in SYMBOLS:
        c, h, l = load(sym, "close"), load(sym, "high"), load(sym, "low")
        closes[sym] = c
        in_pos[sym] = chandelier_in_position(c, h, l)

    close_df = pd.DataFrame(closes).dropna()
    pos_df = pd.DataFrame(in_pos).reindex(close_df.index).fillna(False)
    ret_df = close_df.pct_change().fillna(0)

    # Signals lag by 1 day for realistic next-day execution (see pass-3 lookahead check).
    pos_exec = pos_df.shift(1).fillna(False)

    n_active = pos_exec.sum(axis=1)

    # Scheme 1: equal weight among active, capped 25% each (= 100% when all 4 active).
    eq_weight = pos_exec.div(n_active.replace(0, np.nan), axis=0).fillna(0.0)

    # Scheme 2: inverse-vol weight among active (20d realized vol), renormalized to sum<=1.
    vol20 = ret_df.rolling(20).std().replace(0, np.nan)
    inv_vol = (1 / vol20) * pos_exec
    inv_vol_weight = inv_vol.div(inv_vol.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)

    def run_weighted(weights: pd.DataFrame, label: str, cost_bps_oneway: float = 18.0):
        # Turnover-based cost: charge cost_bps on the change in weight each day (rebalance cost).
        turnover = weights.diff().abs().sum(axis=1).fillna(0.0)
        gross_ret = (weights.shift(1).fillna(0.0) * ret_df).sum(axis=1)
        cost = turnover * (cost_bps_oneway / 10_000)
        net_ret = gross_ret - cost
        equity = 100.0 * (1 + net_ret).cumprod()
        years = (equity.index[-1] - equity.index[0]).days / 365.25
        total_ret = equity.iloc[-1] / 100.0 - 1
        cagr = (1 + total_ret) ** (1 / years) - 1
        sharpe = net_ret.mean() / net_ret.std() * np.sqrt(365) if net_ret.std() > 0 else np.nan
        dd = (equity / equity.cummax() - 1).min()
        print(f"{label}: CAGR={cagr:.1%} Sharpe={sharpe:.2f} MaxDD={dd:.1%} avg_n_active={n_active.mean():.2f}")
        return equity

    print("=== Portfolio: BTC/ETH/SOL/BNB chandelier, 4-asset combination ===")
    eq_curve = run_weighted(eq_weight, "equal_weight_active")
    iv_curve = run_weighted(inv_vol_weight, "inverse_vol_active")

    # Benchmark: 25/25/25/25 static buy-hold across all 4.
    static_weights = pd.DataFrame(0.25, index=ret_df.index, columns=SYMBOLS)
    bh_curve = run_weighted(static_weights, "static_2525_buyhold")

    # Benchmark: BTC-only chandelier (single asset, from prior pass).
    btc_only_weight = pos_exec[["BTCUSDT"]].rename(columns={"BTCUSDT": "BTCUSDT"})
    btc_weight_full = pd.DataFrame(0.0, index=ret_df.index, columns=SYMBOLS)
    btc_weight_full["BTCUSDT"] = pos_exec["BTCUSDT"].astype(float)
    run_weighted(btc_weight_full, "btc_only_chandelier")

    # Correlation of daily returns among active-signal assets (check concentration risk).
    corr = ret_df.corr()
    print("\nReturn correlation matrix:")
    print(corr.round(2).to_string())

    # How often are all 4 (or 0) active at once? Concentration/diversification check.
    print("\nDistribution of n_active days:")
    print(n_active.value_counts().sort_index().to_string())

    out = pd.DataFrame({"equal_weight_active": eq_curve, "inverse_vol_active": iv_curve, "static_buyhold": bh_curve})
    out.to_csv("reports/tables/portfolio_equity_curves.csv")


if __name__ == "__main__":
    main()
