"""Third research pass: VectorBT-based sweeps on BTC/ETH.

Two prior passes (custom engine) rejected cross-sectional momentum, mean
reversion (daily/4h/1h), and volume-confirmed breakout (regime artifact).
This pass uses vectorbt for fast, wide parameter sweeps on BTC/ETH only,
sidestepping the altcoin survivorship-bias blind spot. Base cost tier:
18bps one-way (10bps taker fee + 3bps spread + 5bps slippage).

Run: uv run python -m crypto_research.cli.vbt_research
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

FEES = 0.0010  # 10bps taker fee
SLIPPAGE = 0.0008  # 3bps spread + 5bps slippage combined, applied as slippage

DATA_DIR = Path("data/processed/binance/spot/klines/interval=1d")
START = "2019-07-01"
END = "2026-07-01"


def load_close(symbol: str) -> pd.Series:
    df = pd.read_parquet(DATA_DIR / f"symbol={symbol}" / "data.parquet")
    df = df.set_index("open_time").sort_index()
    s = df["close"].astype(float)
    s.index = pd.to_datetime(s.index, utc=True).tz_localize(None)
    return s.loc[START:END]


def summarize(pf: vbt.Portfolio, label: str) -> dict:
    stats = pf.stats()
    total_ret = pf.total_return()
    return {
        "label": label,
        "total_return": float(np.ravel(total_ret)[0]) if hasattr(total_ret, "__len__") else float(total_ret),
        "sharpe": float(stats.get("Sharpe Ratio", np.nan)),
        "max_dd": float(stats.get("Max Drawdown [%]", np.nan)),
        "n_trades": int(stats.get("Total Trades", 0)),
    }


def cagr_from_total_return(total_return: float, years: float) -> float:
    return (1 + total_return) ** (1 / years) - 1


def main() -> None:
    btc = load_close("BTCUSDT")
    eth = load_close("ETHUSDT")
    years = (btc.index[-1] - btc.index[0]).days / 365.25

    results = []

    # Baseline: buy & hold
    bh_btc = vbt.Portfolio.from_holding(btc, init_cash=100.0)
    bh_eth = vbt.Portfolio.from_holding(eth, init_cash=100.0)
    results.append(summarize(bh_btc, "buy_hold_BTC"))
    results.append(summarize(bh_eth, "buy_hold_ETH"))

    # 1. Dual EMA crossover sweep on BTC (fast/slow grid)
    fast_windows = [10, 20, 30, 50]
    slow_windows = [50, 100, 150, 200]
    for fast_w in fast_windows:
        for slow_w in slow_windows:
            if fast_w >= slow_w:
                continue
            fast_ema = vbt.MA.run(btc, fast_w, ewm=True).ma
            slow_ema = vbt.MA.run(btc, slow_w, ewm=True).ma
            entries = (fast_ema > slow_ema) & (fast_ema.shift(1) <= slow_ema.shift(1))
            exits = (fast_ema < slow_ema) & (fast_ema.shift(1) >= slow_ema.shift(1))
            pf = vbt.Portfolio.from_signals(
                btc, entries, exits, fees=FEES, slippage=SLIPPAGE, init_cash=100.0
            )
            results.append(summarize(pf, f"ema_cross_BTC_{fast_w}_{slow_w}"))

    # 2. Donchian breakout on BTC only (no altcoin survivorship issue)
    for entry_w in [10, 20, 30, 55]:
        for exit_w in [10, 20]:
            if exit_w >= entry_w:
                continue
            upper = btc.rolling(entry_w).max().shift(1)
            lower = btc.rolling(exit_w).min().shift(1)
            entries = btc > upper
            exits = btc < lower
            pf = vbt.Portfolio.from_signals(
                btc, entries, exits, fees=FEES, slippage=SLIPPAGE, init_cash=100.0
            )
            results.append(summarize(pf, f"donchian_BTC_{entry_w}_{exit_w}"))

    # 3. Volatility-targeted 50/50 BTC/ETH (inverse-vol overlay on passive holding)
    combined = pd.DataFrame({"BTC": btc, "ETH": eth}).dropna()
    ret = combined.pct_change().dropna()
    vol20 = ret.rolling(20).std() * np.sqrt(365)
    target_vol = 0.60  # annualized target
    inv_vol_weight = (target_vol / vol20).clip(upper=2.0)  # cap leverage-like scaling at 2x
    inv_vol_weight = inv_vol_weight.div(inv_vol_weight.sum(axis=1), axis=0)  # normalize cross-section
    weighted_ret = (inv_vol_weight.shift(1) * ret).sum(axis=1).dropna()
    vt_equity = 100.0 * (1 + weighted_ret).cumprod()
    vt_total_return = vt_equity.iloc[-1] / 100.0 - 1
    vt_sharpe = weighted_ret.mean() / weighted_ret.std() * np.sqrt(365)
    vt_dd = (vt_equity / vt_equity.cummax() - 1).min()
    results.append(
        {
            "label": "vol_target_5050_BTC_ETH",
            "total_return": float(vt_total_return),
            "sharpe": float(vt_sharpe),
            "max_dd": float(vt_dd * 100),
            "n_trades": None,
        }
    )

    # 4. BTC 200d SMA trend filter re-verified in vectorbt (cross-check pass-1 result)
    sma200 = btc.rolling(200).mean()
    entries = (btc > sma200) & (btc.shift(1) <= sma200.shift(1))
    exits = (btc < sma200) & (btc.shift(1) >= sma200.shift(1))
    pf = vbt.Portfolio.from_signals(btc, entries, exits, fees=FEES, slippage=SLIPPAGE, init_cash=100.0)
    results.append(summarize(pf, "sma200_trend_BTC_crosscheck"))

    # 5. ATR-based trailing stop trend-following on BTC (Chandelier-exit style)
    high = pd.read_parquet(DATA_DIR / "symbol=BTCUSDT" / "data.parquet").set_index("open_time")["high"].astype(float)
    low = pd.read_parquet(DATA_DIR / "symbol=BTCUSDT" / "data.parquet").set_index("open_time")["low"].astype(float)
    high.index = pd.to_datetime(high.index, utc=True).tz_localize(None)
    low.index = pd.to_datetime(low.index, utc=True).tz_localize(None)
    high, low = high.loc[START:END], low.loc[START:END]
    atr = vbt.ATR.run(high, low, btc, window=14).atr
    for mult in [2.0, 3.0, 4.0]:
        for trend_w in [20, 50]:
            trend_ma = btc.rolling(trend_w).mean()
            long_signal = btc > trend_ma
            chandelier_stop = btc.rolling(22).max() - mult * atr
            in_pos = long_signal & (btc > chandelier_stop)
            entries = in_pos & ~in_pos.shift(1).fillna(False)
            exits = ~in_pos & in_pos.shift(1).fillna(False)
            pf = vbt.Portfolio.from_signals(
                btc, entries, exits, fees=FEES, slippage=SLIPPAGE, init_cash=100.0
            )
            results.append(summarize(pf, f"chandelier_BTC_atr{mult}_trend{trend_w}"))

    df = pd.DataFrame(results)
    df["cagr"] = df["total_return"].apply(lambda tr: cagr_from_total_return(tr, years) if pd.notna(tr) else np.nan)
    df = df.sort_values("cagr", ascending=False)
    out_path = Path("reports/tables/vbt_pass3_results.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(df.to_string(index=False))
    print(f"\nyears={years:.2f}, saved to {out_path}")


if __name__ == "__main__":
    main()
