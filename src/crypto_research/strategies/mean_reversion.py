"""Short-term mean reversion: buy an equal-weight basket of the most
oversold symbols in the eligible universe (lowest z-score of price vs its
rolling mean), rebalanced periodically. Long-only, as required for spot.

Signal (as of date t): zscore_20d = (close_t - rolling_mean_20d) / rolling_std_20d.
Most negative z-score = most oversold. This is a pure price-based capitulation
proxy, no order-book data available.
"""

from __future__ import annotations

import pandas as pd


def build_mean_reversion_weights(
    feature_panel: pd.DataFrame,
    universe_by_date: dict[pd.Timestamp, list[str]],
    zscore_col: str = "zscore_20d",
    bottom_n: int = 10,
    max_zscore: float = -1.0,
) -> pd.DataFrame:
    """Holds an equal-weight basket of the `bottom_n` most-oversold
    (lowest zscore_col) eligible symbols, but only among those with
    zscore_col <= max_zscore (i.e. only actually oversold names, not just
    "the least overbought of a bullish set").
    """
    dates = sorted(universe_by_date.keys())
    all_symbols = sorted({s for syms in universe_by_date.values() for s in syms})

    weights = pd.DataFrame(0.0, index=pd.DatetimeIndex(dates), columns=all_symbols)
    panel_indexed = feature_panel.set_index(["open_time", "symbol"])

    for d in dates:
        eligible = universe_by_date[d]
        if not eligible:
            continue
        try:
            day_slice = panel_indexed.loc[(d,)]
        except KeyError:
            continue

        day_slice = day_slice[day_slice.index.isin(eligible)]
        day_slice = day_slice.dropna(subset=[zscore_col])
        day_slice = day_slice[day_slice[zscore_col] <= max_zscore]
        if day_slice.empty:
            continue

        bottom = day_slice[zscore_col].sort_values(ascending=True).head(bottom_n)
        weight_each = 1.0 / len(bottom)
        weights.loc[d, bottom.index] = weight_each

    return weights
