"""Feature engineering on the processed daily kline panel.

Execution convention used throughout this project (stated once, here):
  - A feature/signal computed "as of" candle t uses only information known
    by t's close (open, high, low, close, volume, etc. of candle t and
    earlier).
  - That signal is tradable starting at candle (t+1)'s open — i.e. we
    assume a conservative one-bar delay between observing a fully closed
    candle and acting on it. This rules out same-candle lookahead (using
    candle t's close price as both the signal input and the fill price).
  - Consequently every column in `build_feature_panel` is named for the
    information date t, and the backtest engine is responsible for shifting
    execution to t+1's open when it turns a feature into a trade.

All rolling windows below operate strictly on rows up to and including t
(pandas default trailing rolling window), so no column here needs an
additional internal shift -- the one-bar execution delay is applied once,
uniformly, at the point features become entry signals.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from crypto_research.data.db import connect

PROCESSED_ROOT = Path("data/processed/binance/spot/klines")

MOMENTUM_WINDOWS = (7, 14, 30, 90)
VOLATILITY_WINDOW = 14
ZSCORE_WINDOW = 20
BREAKOUT_WINDOW = 20
VOLUME_ZSCORE_WINDOW = 20


def load_symbol_frame(con: duckdb.DuckDBPyConnection, symbol: str) -> pd.DataFrame:
    path = PROCESSED_ROOT / "interval=1d" / f"symbol={symbol}" / "data.parquet"
    return con.execute(f"SELECT * FROM read_parquet('{path.as_posix()}') ORDER BY open_time").df()


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def compute_symbol_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute single-symbol features. Input must be one symbol's daily
    OHLCV rows sorted by open_time ascending, as produced by
    load_symbol_frame. Returns the same rows with feature columns added.
    """
    out = df.copy()
    out["log_return_1d"] = np.log(out["close"] / out["close"].shift(1))

    for w in MOMENTUM_WINDOWS:
        out[f"momentum_{w}d"] = out["close"] / out["close"].shift(w) - 1.0

    # Skip-most-recent-week momentum (standard momentum-literature convention):
    # trailing 30d return measured up to 7 days ago, so the most recent week's
    # short-term reversal noise is excluded from the ranking signal.
    out["momentum_30d_skip7"] = out["close"].shift(7) / out["close"].shift(37) - 1.0

    out["true_range"] = _true_range(out)
    out[f"atr_{VOLATILITY_WINDOW}d"] = out["true_range"].rolling(VOLATILITY_WINDOW).mean()
    out[f"realized_vol_{VOLATILITY_WINDOW}d"] = out["log_return_1d"].rolling(VOLATILITY_WINDOW).std() * np.sqrt(365)

    rolling_mean = out["close"].rolling(ZSCORE_WINDOW).mean()
    rolling_std = out["close"].rolling(ZSCORE_WINDOW).std()
    out[f"zscore_{ZSCORE_WINDOW}d"] = (out["close"] - rolling_mean) / rolling_std

    rolling_high = out["high"].rolling(BREAKOUT_WINDOW).max()
    rolling_low = out["low"].rolling(BREAKOUT_WINDOW).min()
    out[f"donchian_high_{BREAKOUT_WINDOW}d"] = rolling_high
    out[f"donchian_low_{BREAKOUT_WINDOW}d"] = rolling_low
    out[f"breakout_dist_{BREAKOUT_WINDOW}d"] = (out["close"] - rolling_high) / rolling_high

    # Breakout levels relative to the PRIOR window (shifted by one bar so a
    # candle is never compared against its own high/low): a close above
    # prior_high_20d is a fresh Donchian breakout as of t.
    out["prior_high_20d"] = out["high"].rolling(BREAKOUT_WINDOW).max().shift(1)
    out["prior_low_10d"] = out["low"].rolling(10).min().shift(1)

    rolling_peak = out["close"].cummax()
    out["drawdown_from_peak"] = out["close"] / rolling_peak - 1.0

    out["volume_change_1d"] = out["volume"] / out["volume"].shift(1) - 1.0
    vol_mean = out["quote_asset_volume"].rolling(VOLUME_ZSCORE_WINDOW).mean()
    vol_std = out["quote_asset_volume"].rolling(VOLUME_ZSCORE_WINDOW).std()
    out[f"quote_volume_zscore_{VOLUME_ZSCORE_WINDOW}d"] = (out["quote_asset_volume"] - vol_mean) / vol_std

    out["taker_buy_ratio"] = out["taker_buy_quote_volume"] / out["quote_asset_volume"]

    out["trailing_quote_volume_30d"] = out["quote_asset_volume"].rolling(30).sum()

    return out


def compute_btc_regime_features(btc_df: pd.DataFrame) -> pd.DataFrame:
    """BTC trend/vol regime, joined onto other symbols by open_time.
    Long-run trend filter: close above/below its 200d moving average.
    """
    out = btc_df[["open_time", "close"]].copy()
    out["btc_close"] = out["close"]
    out["btc_sma_200d"] = out["close"].rolling(200).mean()
    out["btc_trend_up"] = out["btc_close"] > out["btc_sma_200d"]
    out["btc_log_return_1d"] = np.log(out["btc_close"] / out["btc_close"].shift(1))
    out["btc_realized_vol_14d"] = out["btc_log_return_1d"].rolling(14).std() * np.sqrt(365)
    return out[["open_time", "btc_close", "btc_sma_200d", "btc_trend_up", "btc_realized_vol_14d"]]


def build_feature_panel(symbols: list[str]) -> pd.DataFrame:
    """Build the full features panel across symbols, with BTC regime
    columns joined onto every row by date. Returns a long DataFrame with
    one row per (symbol, open_time).
    """
    con = connect()

    btc_raw = load_symbol_frame(con, "BTCUSDT")
    btc_regime = compute_btc_regime_features(btc_raw)

    frames = []
    for symbol in symbols:
        raw = load_symbol_frame(con, symbol)
        if raw.empty:
            continue
        feat = compute_symbol_features(raw)
        feat = feat.merge(btc_regime, on="open_time", how="left")
        frames.append(feat)

    return pd.concat(frames, ignore_index=True)
