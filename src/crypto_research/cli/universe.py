"""CLI: build and inspect point-in-time universes.

Usage:
    uv run python -m crypto_research.cli.universe --universe top10 \
        --start 2020-01-01 --end 2026-07-01 --freq MS
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from crypto_research.data.db import connect
from crypto_research.universe.builder import (
    build_universe,
    discover_all_symbols,
    load_config,
    load_daily_panel,
)

OUT_DIR = Path("reports/tables")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", required=True)
    parser.add_argument("--start", default="2019-06-01")
    parser.add_argument("--end", default="2026-07-01")
    parser.add_argument("--freq", default="MS", help="pandas date_range freq, e.g. MS (month start), W")
    args = parser.parse_args()

    rebalance_dates = list(pd.date_range(args.start, args.end, freq=args.freq, tz="UTC"))

    con = connect()
    load_daily_panel(con)
    config = load_config()
    all_symbols = discover_all_symbols()

    result = build_universe(args.universe, rebalance_dates, con, config, all_symbols)

    out_path = OUT_DIR / f"universe_{args.universe}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    sizes = [len(v) for v in result.values()]
    print(f"Universe: {args.universe}")
    print(f"Rebalance dates: {len(result)}")
    print(f"Min/median/max size: {min(sizes)}/{sorted(sizes)[len(sizes)//2]}/{max(sizes)}")
    print(f"Written: {out_path}")


if __name__ == "__main__":
    main()
