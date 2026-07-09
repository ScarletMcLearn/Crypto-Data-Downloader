"""Cross-sectional momentum: rank eligible symbols by trailing return,
hold an equal- or rank-weighted basket of the top-N, rebalanced
periodically.

Signal (as of date t): momentum_{lookback}d = close_t / close_{t-lookback} - 1,
computed only from symbols in the point-in-time eligible universe at t
(see crypto_research.universe.builder). Ranking and weighting both use
only information known at t's close; execution happens at (t+1)'s open.

Optional trend filter: only take cross-sectional bets when BTC is in an
uptrend (close > 200d SMA), reflecting the "risk-on/risk-off" market regime
idea from Phase 4's spec -- this is tested as a variant, not baked into
the core signal, since the spec is explicit that regime-conditioning
must be tested rather than assumed beneficial.
"""

from __future__ import annotations

import pandas as pd


def build_cross_sectional_momentum_weights(
    feature_panel: pd.DataFrame,
    universe_by_date: dict[pd.Timestamp, list[str]],
    momentum_col: str = "momentum_30d",
    top_n: int = 10,
    require_btc_uptrend: bool = False,
) -> pd.DataFrame:
    """feature_panel: long DataFrame with columns [open_time, symbol,
    momentum_col, btc_trend_up (if require_btc_uptrend)].
    universe_by_date: {date -> [eligible symbols]}, point-in-time safe.

    Returns a wide DataFrame (date x symbol) of equal target weights among
    the top_n symbols by momentum_col within that date's eligible universe.
    Dates/symbols not present get weight 0 (cash).
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
        day_slice = day_slice.dropna(subset=[momentum_col])
        if day_slice.empty:
            continue

        if require_btc_uptrend:
            btc_up = day_slice["btc_trend_up"].iloc[0] if "btc_trend_up" in day_slice.columns else True
            if not bool(btc_up):
                continue

        top = day_slice[momentum_col].sort_values(ascending=False).head(top_n)
        if top.empty:
            continue

        weight_each = 1.0 / len(top)
        weights.loc[d, top.index] = weight_each

    return weights
