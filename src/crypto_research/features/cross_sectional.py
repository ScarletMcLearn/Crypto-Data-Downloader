"""Cross-sectional features computed across all symbols eligible at each
date. Must be called AFTER per-symbol features (panel.build_feature_panel)
and after joining in the point-in-time universe membership, so that ranks
are computed only among symbols that were actually eligible at that date
(never ranking against a symbol whose future listing status "leaks" into
the past).
"""

from __future__ import annotations

import pandas as pd


def add_cross_sectional_features(panel: pd.DataFrame, momentum_col: str = "momentum_30d") -> pd.DataFrame:
    """Adds cross-sectional rank/percentile of `momentum_col` and market
    breadth (fraction of symbols in an uptrend) computed independently at
    each open_time, using only rows present in `panel` for that date --
    callers must pre-filter `panel` to the point-in-time eligible universe
    before calling this.
    """
    out = panel.copy()
    grouped = out.groupby("open_time")[momentum_col]
    out["cs_momentum_rank"] = grouped.rank(ascending=False, method="first")
    out["cs_momentum_pct"] = grouped.rank(pct=True)

    breadth = (
        out.assign(_above_ma=out["close"] > out.get("donchian_low_20d"))
        .groupby("open_time")["_above_ma"]
        .mean()
        .rename("market_breadth_above_donchian_low")
    )
    out = out.merge(breadth, on="open_time", how="left")

    dispersion = grouped.std().rename("cs_momentum_dispersion")
    out = out.merge(dispersion, on="open_time", how="left")

    return out
