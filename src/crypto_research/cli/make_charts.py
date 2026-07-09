"""Generate equity curve, drawdown, and monthly-return-heatmap figures for
the strategies that survived far enough in the pipeline to be worth
visualizing: buy-and-hold BTC/ETH/50-50 (benchmarks) and the BTC trend
filter (the only active strategy not rejected outright).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from crypto_research.backtest.engine import load_cost_tier, load_liquidity_config, run_backtest
from crypto_research.backtest.metrics import monthly_returns_table
from crypto_research.cli.research import _wide_price_frame, buy_and_hold_weights
from crypto_research.strategies.btc_trend import btc_trend_target_weights

FIG_DIR = Path("reports/figures")
START, END = "2019-07-01", "2026-07-01"


def _run(symbols: list[str], target_weights: pd.DataFrame) -> pd.Series:
    open_prices = _wide_price_frame(symbols, "open").reindex(target_weights.index)
    close_prices = _wide_price_frame(symbols, "close").reindex(target_weights.index)
    quote_vol = _wide_price_frame(symbols, "quote_asset_volume")
    trailing_vol = quote_vol.rolling(30).sum().reindex(target_weights.index)
    cost_tier = load_cost_tier("base")
    liquidity = load_liquidity_config()
    result = run_backtest(target_weights, open_prices, close_prices, trailing_vol, cost_tier, liquidity)
    return result.net_return


def main() -> None:
    dates = pd.date_range(START, END, freq="D", tz="UTC")

    btc_close = _wide_price_frame(["BTCUSDT"], "close")["BTCUSDT"].sort_index()

    series = {}
    series["Buy & Hold BTC"] = _run(["BTCUSDT"], buy_and_hold_weights(["BTCUSDT"], dates))
    series["Buy & Hold ETH"] = _run(["ETHUSDT"], buy_and_hold_weights(["ETHUSDT"], dates))
    series["50/50 BTC-ETH"] = _run(["BTCUSDT", "ETHUSDT"], buy_and_hold_weights(["BTCUSDT", "ETHUSDT"], dates, 0.5))
    series["BTC Trend (200d SMA)"] = _run(["BTCUSDT"], btc_trend_target_weights(btc_close).reindex(dates).ffill().fillna(0.0))

    equity_curves = {name: (1 + r).cumprod() for name, r in series.items()}

    # Equity curve chart (log scale)
    fig, ax = plt.subplots(figsize=(11, 6))
    for name, eq in equity_curves.items():
        ax.plot(eq.index, eq.values, label=name)
    ax.set_yscale("log")
    ax.set_title("Equity Curves (net of base costs), 2019-07 to 2026-07")
    ax.set_ylabel("Growth of $1 (log scale)")
    ax.legend()
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "equity_curves.png", dpi=150)
    plt.close(fig)

    # Drawdown chart
    fig, ax = plt.subplots(figsize=(11, 6))
    for name, eq in equity_curves.items():
        dd = eq / eq.cummax() - 1.0
        ax.plot(dd.index, dd.values, label=name)
    ax.set_title("Drawdowns")
    ax.set_ylabel("Drawdown")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "drawdowns.png", dpi=150)
    plt.close(fig)

    # Monthly return heatmap for BTC trend filter
    monthly = monthly_returns_table(series["BTC Trend (200d SMA)"])
    fig, ax = plt.subplots(figsize=(12, 4))
    im = ax.imshow(monthly.values, cmap="RdYlGn", vmin=-0.3, vmax=0.3, aspect="auto")
    ax.set_xticks(range(len(monthly.columns)))
    ax.set_xticklabels(monthly.columns)
    ax.set_yticks(range(len(monthly.index)))
    ax.set_yticklabels(monthly.index)
    ax.set_title("BTC Trend Filter: Monthly Returns")
    for i in range(monthly.shape[0]):
        for j in range(monthly.shape[1]):
            val = monthly.values[i, j]
            if pd.notna(val):
                ax.text(j, i, f"{val:.0%}", ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, label="Monthly return")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "btc_trend_monthly_heatmap.png", dpi=150)
    plt.close(fig)

    print("Wrote:")
    for f in ["equity_curves.png", "drawdowns.png", "btc_trend_monthly_heatmap.png"]:
        print(f"  {FIG_DIR / f}")


if __name__ == "__main__":
    main()
