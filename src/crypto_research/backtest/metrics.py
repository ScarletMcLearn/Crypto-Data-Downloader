"""Performance metrics computed from a backtest's net daily return series."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 365  # crypto trades every calendar day, unlike traditional equities


@dataclass
class PerformanceSummary:
    total_return: float
    cagr: float
    ann_volatility: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown: float
    max_drawdown_days: int
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    payoff_ratio: float
    n_periods: int
    best_period: float
    worst_period: float


def _drawdown_series(equity: pd.Series) -> pd.Series:
    running_max = equity.cummax()
    return equity / running_max - 1.0


def max_drawdown_and_duration(equity: pd.Series) -> tuple[float, int]:
    dd = _drawdown_series(equity)
    max_dd = dd.min()
    # Duration: longest streak below the previous peak, in periods.
    is_underwater = dd < 0
    longest = 0
    current = 0
    for flag in is_underwater:
        if flag:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return float(max_dd), longest


def _periods_per_year(index: pd.DatetimeIndex) -> float:
    """Infer the annualization factor from the actual spacing of the
    return series' index, rather than assuming daily periods. This makes
    summarize() correct regardless of whether returns are the engine's
    internal daily marks or a coarser rebalance-date series.
    """
    if len(index) < 2:
        return TRADING_DAYS_PER_YEAR
    median_step_days = pd.Series(index).diff().dropna().dt.total_seconds().median() / 86400.0
    if not median_step_days or median_step_days <= 0:
        return TRADING_DAYS_PER_YEAR
    return TRADING_DAYS_PER_YEAR / median_step_days


def summarize(returns: pd.Series, equity: pd.Series | None = None) -> PerformanceSummary:
    """`returns` is a period-over-period simple return series (e.g. daily
    net_return from BacktestResult). `equity` defaults to a NAV path
    reconstructed from returns starting at 1.0 if not given. The
    annualization factor is inferred from the median spacing of the
    series' own DatetimeIndex, so this works whether `returns` is a daily
    series or a coarser (e.g. weekly) rebalance-date series.
    """
    returns = returns.dropna()
    if equity is None:
        equity = (1 + returns).cumprod()

    periods_per_year = _periods_per_year(pd.DatetimeIndex(returns.index))

    n_periods = len(returns)
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0) if len(equity) else 0.0
    if len(equity) >= 2:
        elapsed_days = (equity.index[-1] - equity.index[0]).total_seconds() / 86400.0
        years = elapsed_days / TRADING_DAYS_PER_YEAR
    else:
        years = np.nan
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1.0) if years and years > 0 else float("nan")

    ann_vol = float(returns.std() * np.sqrt(periods_per_year)) if n_periods > 1 else float("nan")
    mean_ann_return = float(returns.mean() * periods_per_year)
    sharpe = mean_ann_return / ann_vol if ann_vol and ann_vol > 0 else float("nan")

    downside = returns[returns < 0]
    downside_vol = float(downside.std() * np.sqrt(periods_per_year)) if len(downside) > 1 else float("nan")
    sortino = mean_ann_return / downside_vol if downside_vol and downside_vol > 0 else float("nan")

    max_dd, dd_days = max_drawdown_and_duration(equity)
    calmar = cagr / abs(max_dd) if max_dd and max_dd != 0 else float("nan")

    wins = returns[returns > 0]
    losses = returns[returns < 0]
    win_rate = float(len(wins) / n_periods) if n_periods else float("nan")
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    profit_factor = float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
    payoff_ratio = float(avg_win / abs(avg_loss)) if avg_loss != 0 else float("inf")

    return PerformanceSummary(
        total_return=total_return,
        cagr=cagr,
        ann_volatility=ann_vol,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        max_drawdown=max_dd,
        max_drawdown_days=dd_days,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_win=avg_win,
        avg_loss=avg_loss,
        payoff_ratio=payoff_ratio,
        n_periods=n_periods,
        best_period=float(returns.max()) if n_periods else float("nan"),
        worst_period=float(returns.min()) if n_periods else float("nan"),
    )


def monthly_returns_table(returns: pd.Series) -> pd.DataFrame:
    equity = (1 + returns).cumprod()
    monthly_equity = equity.resample("ME").last()
    monthly_return = monthly_equity.pct_change()
    monthly_return.iloc[0] = monthly_equity.iloc[0] / 1.0 - 1.0
    df = monthly_return.to_frame("return")
    df["year"] = df.index.year
    df["month"] = df.index.month
    return df.pivot(index="year", columns="month", values="return")
