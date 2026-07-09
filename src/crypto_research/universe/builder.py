"""Phase 3: point-in-time universe construction.

Every universe function here answers the question "which symbols were
eligible as of rebalance date t, using only data available at or before t?"
No function ever consults data/raw/binance/spot/current_universe.json for
anything except the clearly-labelled survivorship-biased benchmark
universe, since that file only lists symbols trading *today* and using it
historically would leak future listing-status information into the past.

Eligibility inputs, all lagged to be point-in-time safe:
  - first_open_time: the symbol's first available candle (from the
    processed layer, i.e. Binance's own archive start for that symbol).
    A symbol is only eligible once min_history_days have elapsed since then.
  - trailing quote volume over trailing_volume_window_days, computed using
    candles with close_time <= t (i.e. fully completed candles only).

Static exclusion lists (stablecoins, tokenized stocks, leveraged tokens)
come from configs/universes.yaml and are applied uniformly regardless of t.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd
import yaml

PROCESSED_ROOT = Path("data/processed/binance/spot/klines")
CONFIG_PATH = Path("configs/universes.yaml")
CURRENT_UNIVERSE_PATH = Path("data/raw/binance/spot/current_universe.json")


@dataclass
class UniverseConfig:
    excluded_bases: set[str]
    min_history_days: int
    min_trailing_quote_volume_usd: float
    trailing_volume_window_days: int


def load_config(path: Path = CONFIG_PATH) -> UniverseConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    excl = raw["exclusions"]
    excluded_bases = set(excl["stablecoins"]) | set(excl["tokenized_stocks"]) | set(excl["leveraged_tokens"])
    rules = raw["rules"]
    return UniverseConfig(
        excluded_bases=excluded_bases,
        min_history_days=rules["min_history_days"],
        min_trailing_quote_volume_usd=float(rules["min_trailing_quote_volume_usd"]),
        trailing_volume_window_days=rules["trailing_volume_window_days"],
    )


def _base_asset_for_symbol(symbol: str) -> str:
    # All symbols in this dataset are BASEUSDT; strip the fixed USDT quote suffix.
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def excluded_symbols(config: UniverseConfig, all_symbols: list[str]) -> set[str]:
    return {s for s in all_symbols if _base_asset_for_symbol(s) in config.excluded_bases}


def _daily_glob() -> str:
    return str(PROCESSED_ROOT / "interval=1d" / "symbol=*" / "data.parquet")


def load_daily_panel(con: duckdb.DuckDBPyConnection) -> None:
    """Register a `daily` view over the full processed 1d panel."""
    con.execute(
        f"""
        CREATE OR REPLACE VIEW daily AS
        SELECT symbol, open_time, close_time, close, quote_asset_volume
        FROM read_parquet('{_daily_glob()}', hive_partitioning = 1)
        """
    )


def first_open_time_by_symbol(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute(
        "SELECT symbol, min(open_time) AS first_open_time FROM daily GROUP BY symbol"
    ).df()


def trailing_quote_volume(
    con: duckdb.DuckDBPyConnection, as_of: pd.Timestamp, window_days: int
) -> pd.DataFrame:
    """Sum of quote_asset_volume over fully-completed candles with
    close_time <= as_of, over the trailing window_days window. Point-in-time
    safe: never touches a candle whose data wasn't fully observed by as_of.
    """
    window_start = as_of - pd.Timedelta(days=window_days)
    return con.execute(
        """
        SELECT symbol, sum(quote_asset_volume) AS trailing_quote_volume
        FROM daily
        WHERE close_time <= ? AND open_time > ?
        GROUP BY symbol
        """,
        [as_of, window_start],
    ).df()


def eligible_symbols_at(
    con: duckdb.DuckDBPyConnection,
    as_of: pd.Timestamp,
    config: UniverseConfig,
    first_open: pd.DataFrame,
    exclude: set[str],
) -> pd.DataFrame:
    """Symbols eligible at `as_of`: history requirement met, not in the
    static exclusion list. Returns columns [symbol, trailing_quote_volume].
    """
    min_first_open = as_of - pd.Timedelta(days=config.min_history_days)
    history_ok = first_open[first_open["first_open_time"] <= min_first_open]["symbol"]
    volumes = trailing_quote_volume(con, as_of, config.trailing_volume_window_days)

    merged = volumes.merge(history_ok, on="symbol", how="inner")
    merged = merged[~merged["symbol"].isin(exclude)]
    return merged.sort_values("trailing_quote_volume", ascending=False).reset_index(drop=True)


def build_universe(
    universe_id: str,
    rebalance_dates: list[pd.Timestamp],
    con: duckdb.DuckDBPyConnection,
    config: UniverseConfig,
    all_symbols: list[str],
) -> dict[str, list[str]]:
    """Returns {rebalance_date_iso: [symbols]} for the requested universe id."""
    exclude = excluded_symbols(config, all_symbols)
    first_open = first_open_time_by_symbol(con)

    result: dict[str, list[str]] = {}

    if universe_id == "btc_eth":
        for d in rebalance_dates:
            result[d.isoformat()] = ["BTCUSDT", "ETHUSDT"]
        return result

    if universe_id == "current_universe_survivorship_biased":
        current = json.loads(CURRENT_UNIVERSE_PATH.read_text(encoding="utf-8"))
        symbols = sorted(x["symbol"] for x in current if x["quote_asset"] == "USDT")
        for d in rebalance_dates:
            result[d.isoformat()] = symbols
        return result

    top_n_map = {"top10": 10, "top20": 20, "top50": 50}

    for d in rebalance_dates:
        eligible = eligible_symbols_at(con, d, config, first_open, exclude)
        if universe_id in top_n_map:
            symbols = eligible.head(top_n_map[universe_id])["symbol"].tolist()
        elif universe_id == "broad_liquid":
            symbols = eligible[
                eligible["trailing_quote_volume"] >= config.min_trailing_quote_volume_usd
            ]["symbol"].tolist()
        else:
            raise ValueError(f"Unknown universe_id: {universe_id}")
        result[d.isoformat()] = symbols

    return result


def discover_all_symbols() -> list[str]:
    daily_dir = PROCESSED_ROOT / "interval=1d"
    return sorted(p.name.replace("symbol=", "") for p in daily_dir.iterdir() if p.is_dir())
