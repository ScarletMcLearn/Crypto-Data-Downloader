"""Run a matrix of strategy x universe (x cost tier) combinations and write
a single comparison table. Failures in one combination are logged and do
not stop the rest of the matrix.
"""

from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path

import pandas as pd

from crypto_research.cli.research import run_strategy

REPORT_DIR = Path("reports/tables")

DEFAULT_STRATEGY_UNIVERSE_PAIRS = [
    ("buy_and_hold_btc", "btc_eth"),
    ("buy_and_hold_eth", "btc_eth"),
    ("buy_and_hold_50_50", "btc_eth"),
    ("btc_trend", "btc_eth"),
    ("equal_weight_universe", "top10"),
    ("equal_weight_universe", "top20"),
    ("equal_weight_universe", "top50"),
    ("equal_weight_universe", "broad_liquid"),
    ("equal_weight_universe", "current_universe_survivorship_biased"),
    ("cross_sectional_momentum", "top10"),
    ("cross_sectional_momentum", "top20"),
    ("cross_sectional_momentum", "top50"),
    ("cross_sectional_momentum", "broad_liquid"),
    ("mean_reversion", "top10"),
    ("mean_reversion", "top20"),
    ("mean_reversion", "top50"),
    ("mean_reversion", "broad_liquid"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost-tier", default="base")
    parser.add_argument("--start", default="2019-07-01")
    parser.add_argument("--end", default="2026-07-01")
    parser.add_argument("--freq", default="W")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--out", default=str(REPORT_DIR / "strategy_comparison.json"))
    args = parser.parse_args()

    rows = []
    errors = []

    for strategy, universe in DEFAULT_STRATEGY_UNIVERSE_PAIRS:
        print(f"Running {strategy} / {universe} / {args.cost_tier} ...")
        try:
            result = run_strategy(
                strategy, universe, args.cost_tier, args.start, args.end, args.freq, args.top_n
            )
            perf = result["performance"]
            rows.append(
                {
                    "strategy": strategy,
                    "universe": universe,
                    "cost_tier": args.cost_tier,
                    "final_equity": result["final_equity"],
                    "n_trades": result["n_trades"],
                    "total_cost": result["total_fee_cost"] + result["total_slippage_cost"],
                    "avg_turnover_per_rebalance": result["avg_turnover_per_rebalance"],
                    "capacity_breaches": result["capacity_breaches"],
                    **perf,
                }
            )
        except Exception as exc:
            errors.append({"strategy": strategy, "universe": universe, "error": str(exc), "traceback": traceback.format_exc()})
            print(f"  ERROR: {exc}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    table = pd.DataFrame(rows)
    table.to_json(out_path, orient="records", indent=2)
    csv_path = out_path.with_suffix(".csv")
    table.to_csv(csv_path, index=False)

    errors_path = out_path.with_name(out_path.stem + "_errors.json")
    errors_path.write_text(json.dumps(errors, indent=2), encoding="utf-8")

    print("\n=== Strategy Comparison ===")
    if not table.empty:
        print(table[["strategy", "universe", "final_equity", "cagr", "sharpe", "max_drawdown", "n_trades"]].to_string(index=False))
    print(f"\nWritten: {out_path} and {csv_path}")
    print(f"Errors: {len(errors)} -> {errors_path}")


if __name__ == "__main__":
    main()
