"""Volume-confirmed Donchian breakout (turtle-style, long-only).

Entry (as of date t, executed at t+1 open by the engine): close_t strictly
above the PRIOR 20-day high (prior_high_20d, which excludes candle t
itself), optionally confirmed by order-flow/volume:
  - confirm="taker": taker_buy_ratio_t >= taker_buy_min (aggressive buying
    dominates the breakout bar);
  - confirm="volume_z": quote_volume_zscore_20d_t >= volume_z_min (breakout
    on abnormally high volume);
  - confirm="none": raw Donchian breakout, used as the unconfirmed control.

Exit: close_t below the prior 10-day low (classic Donchian/turtle exit),
or the symbol leaving the point-in-time eligible universe.

Positions are stateful between rebalance dates: a symbol entered earlier is
held until its exit triggers, so this is NOT a "fresh breakouts only"
portfolio. Equal weight across all currently-held names, capped at
max_positions; when nothing is held, the portfolio is in cash.
"""

from __future__ import annotations

import pandas as pd


def build_volume_breakout_weights(
    feature_panel: pd.DataFrame,
    universe_by_date: dict[pd.Timestamp, list[str]],
    confirm: str = "taker",
    taker_buy_min: float = 0.55,
    volume_z_min: float = 1.0,
    max_positions: int = 10,
    require_btc_uptrend: bool = False,
    fixed_slice: bool = False,
    entry_col: str = "prior_high_20d",
    exit_col: str = "prior_low_10d",
) -> pd.DataFrame:
    """fixed_slice=True gives every held position a constant 1/max_positions
    weight (unfilled slots stay in cash), eliminating the concentration
    artifact where a lone holding receives 100% of NAV."""
    dates = sorted(universe_by_date.keys())
    all_symbols = sorted({s for syms in universe_by_date.values() for s in syms})

    weights = pd.DataFrame(0.0, index=pd.DatetimeIndex(dates), columns=all_symbols)
    panel_indexed = feature_panel.set_index(["open_time", "symbol"])

    held: set[str] = set()

    for d in dates:
        eligible = set(universe_by_date[d])

        slice_w = (1.0 / max_positions) if fixed_slice else None

        try:
            day_slice = panel_indexed.loc[(d,)]
        except KeyError:
            # No panel rows for this date: carry the held set forward as-is.
            if held:
                weights.loc[d, sorted(held)] = slice_w if fixed_slice else 1.0 / len(held)
            continue

        # Exits first: prior 10d low breach, or symbol left the universe,
        # or no data for it today.
        still_held = set()
        for sym in held:
            if sym not in eligible or sym not in day_slice.index:
                continue
            row = day_slice.loc[sym]
            if pd.notna(row[exit_col]) and row["close"] < row[exit_col]:
                continue
            still_held.add(sym)
        held = still_held

        # Entries: fresh confirmed breakouts among eligible symbols.
        btc_ok = True
        if require_btc_uptrend and "btc_trend_up" in day_slice.columns and len(day_slice):
            btc_ok = bool(day_slice["btc_trend_up"].iloc[0])

        if btc_ok:
            cand = day_slice[day_slice.index.isin(eligible)]
            cand = cand.dropna(subset=[entry_col])
            breakout = cand[cand["close"] > cand[entry_col]]

            if confirm == "taker":
                breakout = breakout[breakout["taker_buy_ratio"] >= taker_buy_min]
            elif confirm == "volume_z":
                breakout = breakout.dropna(subset=["quote_volume_zscore_20d"])
                breakout = breakout[breakout["quote_volume_zscore_20d"] >= volume_z_min]
            elif confirm != "none":
                raise ValueError(f"Unknown confirm mode: {confirm}")

            # Strongest breakouts first (largest % above prior high) if we
            # must ration slots.
            room = max_positions - len(held)
            if room > 0 and not breakout.empty:
                strength = (breakout["close"] / breakout[entry_col] - 1.0).sort_values(ascending=False)
                for sym in strength.index:
                    if len(held) >= max_positions:
                        break
                    held.add(sym)

        if held:
            weights.loc[d, sorted(held)] = slice_w if fixed_slice else 1.0 / len(held)

    return weights
