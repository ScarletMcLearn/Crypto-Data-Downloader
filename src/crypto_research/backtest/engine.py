"""Portfolio-level, long-only, multi-symbol vectorized backtest engine.

Execution model (see crypto_research.features.panel module docstring for
the full rationale):
  - A strategy produces target portfolio weights "as of" candle t, using
    only information available at t's close.
  - Those weights are executed at candle (t+1)'s OPEN price. This is a
    single, uniform one-bar delay applied here in the engine -- strategies
    never see or use next-bar prices when computing their target weights.
  - If a symbol has no candle at t+1 (e.g. very new listing, data gap, or
    delisting) the fill for that symbol is skipped for that period and its
    prior-period position is carried forward unchanged (we do not force an
    exit at an unavailable price, and we do not silently forward-fill a
    tradable price across a real gap).

Costs: every trade (a change in target weight for a symbol) pays a
round-trip-style one-way cost of (taker_fee_bps + spread_bps + slippage_bps)
combined, applied to the notional traded. Cost tiers come from
configs/costs.yaml.

Liquidity: for each candidate trade, notional is capped at
`max_trade_pct_of_trailing_daily_quote_volume` * that symbol's trailing
30-day average daily quote volume (using only volume observed at or before
t, since the caller is expected to have already computed weights using
point-in-time-safe trailing volume). Trades that would need to exceed this
cap are shrunk rather than rejected outright, and the shrinkage is logged.

This is NOT a limit-order-book simulator. It is a deliberately simple,
transparent, vectorized approximation appropriate for daily/4h rebalancing
frequencies on liquid-to-mid liquidity spot pairs. It is not appropriate
for high-frequency or large-capital simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

COSTS_CONFIG_PATH = Path("configs/costs.yaml")


@dataclass
class CostTier:
    name: str
    taker_fee_bps: float
    spread_bps: float
    slippage_bps: float

    @property
    def total_one_way_bps(self) -> float:
        return self.taker_fee_bps + self.spread_bps + self.slippage_bps


@dataclass
class LiquidityConfig:
    max_trade_pct_of_trailing_daily_quote_volume: float
    min_trailing_quote_volume_usd_to_trade: float


def load_cost_tier(name: str, path: Path = COSTS_CONFIG_PATH) -> CostTier:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    tier = raw["tiers"][name]
    return CostTier(
        name=name,
        taker_fee_bps=tier["taker_fee_bps"],
        spread_bps=tier["spread_bps"],
        slippage_bps=tier["slippage_bps"],
    )


def load_liquidity_config(path: Path = COSTS_CONFIG_PATH) -> LiquidityConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    liq = raw["liquidity"]
    return LiquidityConfig(
        max_trade_pct_of_trailing_daily_quote_volume=liq["max_trade_pct_of_trailing_daily_quote_volume"],
        min_trailing_quote_volume_usd_to_trade=liq["min_trailing_quote_volume_usd_to_trade"],
    )


@dataclass
class BacktestResult:
    equity_curve: pd.Series  # indexed by date, portfolio NAV
    weights_history: pd.DataFrame  # date x symbol, realized weights after fills
    trade_log: pd.DataFrame
    turnover: pd.Series  # date -> gross turnover (sum abs weight change)
    gross_return: pd.Series
    net_return: pd.Series
    fee_cost: pd.Series
    slippage_cost: pd.Series
    capacity_breaches: pd.DataFrame = field(default_factory=pd.DataFrame)


def run_backtest(
    target_weights: pd.DataFrame,
    open_prices: pd.DataFrame,
    close_prices: pd.DataFrame,
    trailing_daily_quote_volume: pd.DataFrame,
    cost_tier: CostTier,
    liquidity: LiquidityConfig,
    initial_capital: float = 1.0,
) -> BacktestResult:
    """Run the backtest.

    Parameters
    ----------
    target_weights : DataFrame indexed by signal date t (rows), symbols
        (columns), values in [0, 1] (long-only), rows need not sum to 1
        (remaining is held in cash). This is the weight the strategy WANTS
        as of information available at close of t; it is executed at the
        open of the next available trading date (t+1) per symbol.
    open_prices, close_prices : DataFrame indexed by date (rows), symbols
        (columns). Must be aligned on the same date index as target_weights
        shifted by one row (i.e. open_prices.index should include the
        execution dates).
    trailing_daily_quote_volume : DataFrame, same shape as prices, giving
        each symbol's trailing 30d average daily quote volume as of that
        date (must already be point-in-time safe / lagged by the caller).
    """
    dates = target_weights.index
    symbols = target_weights.columns

    # Execution happens one row later than the signal date.
    exec_dates = dates[1:]
    signal_dates = dates[:-1]

    nav = initial_capital
    gross_nav = initial_capital  # tracks the same positions with zero costs, for gross-vs-net comparison
    realized_weights = pd.DataFrame(0.0, index=dates, columns=symbols)
    turnover_series = {}
    fee_cost_series = {}
    slippage_cost_series = {}
    trade_rows = []
    capacity_breach_rows = []
    nav_path = {dates[0]: nav}
    gross_nav_path = {dates[0]: gross_nav}

    prev_weights = pd.Series(0.0, index=symbols)
    prev_exec_date = dates[0]

    for signal_date, exec_date in zip(signal_dates, exec_dates):
        # Mark NAV to market using the price move since the previous exec
        # date, applied to the weights that were actually held over that
        # interval (prev_weights), before any new trading happens today.
        if prev_exec_date in close_prices.index and exec_date in close_prices.index:
            prev_close = close_prices.loc[prev_exec_date].reindex(symbols)
            cur_close = close_prices.loc[exec_date].reindex(symbols)
            asset_return = (cur_close / prev_close - 1.0).fillna(0.0)
        else:
            asset_return = pd.Series(0.0, index=symbols)
        port_return = (prev_weights * asset_return).sum()
        nav = nav * (1 + port_return)
        gross_nav = gross_nav * (1 + port_return)

        wanted = target_weights.loc[signal_date].reindex(symbols).fillna(0.0)

        if exec_date not in open_prices.index:
            realized_weights.loc[exec_date] = prev_weights
            nav_path[exec_date] = nav
            gross_nav_path[exec_date] = gross_nav
            prev_exec_date = exec_date
            continue

        exec_open = open_prices.loc[exec_date]
        tradable = exec_open.notna()

        # For symbols with no tradable price at exec_date, carry forward
        # the previous weight (do not force a fill at an unavailable price).
        effective_target = wanted.where(tradable, prev_weights)

        # Liquidity cap: shrink the desired weight change to respect
        # max_trade_pct_of_trailing_daily_quote_volume, using volume as of
        # the signal date (already lagged/point-in-time safe upstream).
        trailing_vol = trailing_daily_quote_volume.loc[signal_date].reindex(symbols) if signal_date in trailing_daily_quote_volume.index else pd.Series(0.0, index=symbols)
        max_trade_notional = trailing_vol * liquidity.max_trade_pct_of_trailing_daily_quote_volume
        max_trade_weight = (max_trade_notional / nav).clip(lower=0.0) if nav > 0 else pd.Series(0.0, index=symbols)

        desired_delta = effective_target - prev_weights
        capped_delta = desired_delta.clip(lower=-max_trade_weight, upper=max_trade_weight)
        breached = (desired_delta.abs() > max_trade_weight) & tradable
        if breached.any():
            for sym in symbols[breached]:
                capacity_breach_rows.append(
                    {
                        "date": exec_date,
                        "symbol": sym,
                        "desired_delta_weight": desired_delta[sym],
                        "capped_delta_weight": capped_delta[sym],
                    }
                )

        new_weights = prev_weights + capped_delta
        new_weights = new_weights.where(tradable, prev_weights)
        new_weights = new_weights.clip(lower=0.0)

        # Enforce liquidity floor: symbols below the minimum trailing
        # volume are not allowed to hold a nonzero NEW position (existing
        # positions are allowed to be held/reduced, just not increased).
        illiquid = trailing_vol < liquidity.min_trailing_quote_volume_usd_to_trade
        increasing = capped_delta > 0
        blocked = illiquid & increasing
        new_weights = new_weights.where(~blocked, prev_weights)

        traded_notional_weight = (new_weights - prev_weights).abs()
        turnover = traded_notional_weight.sum()
        cost_frac = cost_tier.total_one_way_bps / 10_000.0
        cost_amount = (traded_notional_weight * nav * cost_frac).sum()
        fee_amount = (traded_notional_weight * nav * (cost_tier.taker_fee_bps / 10_000.0)).sum()
        slippage_amount = cost_amount - fee_amount

        for sym in symbols[traded_notional_weight > 1e-12]:
            trade_rows.append(
                {
                    "date": exec_date,
                    "symbol": sym,
                    "prev_weight": prev_weights[sym],
                    "new_weight": new_weights[sym],
                    "delta_weight": new_weights[sym] - prev_weights[sym],
                }
            )

        # Deduct trading costs from NAV at the moment of trading. The price
        # return for the interval [prev_exec_date, exec_date] was already
        # applied to `nav` at the top of this loop iteration, using the
        # weights held over that interval (prev_weights) -- so this cost
        # deduction and the next iteration's mark-to-market are the only
        # two ways NAV changes, with no double counting.
        nav = nav - cost_amount

        realized_weights.loc[exec_date] = new_weights
        turnover_series[exec_date] = turnover
        fee_cost_series[exec_date] = fee_amount
        slippage_cost_series[exec_date] = slippage_amount
        nav_path[exec_date] = nav
        gross_nav_path[exec_date] = gross_nav

        prev_weights = new_weights
        prev_exec_date = exec_date

    final_equity = pd.Series(nav_path).sort_index()
    gross_equity = pd.Series(gross_nav_path).sort_index()
    gross_return = gross_equity.pct_change().fillna(0.0)
    net_return = final_equity.pct_change().fillna(0.0)

    return BacktestResult(
        equity_curve=final_equity,
        weights_history=realized_weights,
        trade_log=pd.DataFrame(trade_rows),
        turnover=pd.Series(turnover_series).sort_index(),
        gross_return=gross_return,
        net_return=net_return,
        fee_cost=pd.Series(fee_cost_series).sort_index(),
        slippage_cost=pd.Series(slippage_cost_series).sort_index(),
        capacity_breaches=pd.DataFrame(capacity_breach_rows),
    )
