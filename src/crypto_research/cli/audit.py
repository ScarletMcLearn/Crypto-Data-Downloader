"""Phase 1 data audit: scan all raw Binance spot kline archives and report
coverage, integrity, and quality issues without modifying any raw data.

Usage:
    uv run python -m crypto_research.cli.audit [--intervals 1d,4h,1h] [--limit N]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from crypto_research.data.kline_reader import read_kline_archive

RAW_ROOT = Path("data/raw/binance/spot")
REPORT_DIR = Path("reports/tables")

INTERVAL_TIMEDELTA = {
    "1d": pd.Timedelta(days=1),
    "4h": pd.Timedelta(hours=4),
    "1h": pd.Timedelta(hours=1),
}


@dataclass
class SymbolIntervalReport:
    symbol: str
    interval: str
    n_files_monthly: int = 0
    n_files_daily: int = 0
    n_bad_zip: int = 0
    n_empty_file: int = 0
    n_unexpected_schema: int = 0
    n_files_with_header: int = 0
    timestamp_units_seen: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    n_rows_raw: int = 0
    n_rows_after_dedup: int = 0
    n_duplicate_rows: int = 0
    n_negative_or_zero_price: int = 0
    n_bad_ohlc_relationship: int = 0
    n_gap_periods: int = 0
    max_gap_periods: int = 0
    earliest: str | None = None
    latest: str | None = None


def discover_symbols() -> list[str]:
    monthly_root = RAW_ROOT / "monthly" / "klines"
    return sorted(p.name for p in monthly_root.iterdir() if p.is_dir())


def audit_symbol_interval(symbol: str, interval: str) -> SymbolIntervalReport:
    report = SymbolIntervalReport(symbol=symbol, interval=interval)
    frames: list[pd.DataFrame] = []

    for source in ("monthly", "daily"):
        d = RAW_ROOT / source / "klines" / symbol / interval
        if not d.exists():
            continue
        files = sorted(d.glob("*.zip"))
        if source == "monthly":
            report.n_files_monthly = len(files)
        else:
            report.n_files_daily = len(files)

        for f in files:
            result = read_kline_archive(f)
            if result.error:
                report.errors.append(f"{f.name}: {result.error}")
                if "bad_zip" in result.error:
                    report.n_bad_zip += 1
                elif result.error in ("empty_file", "zero_rows", "zero_valid_rows_after_ts_parse"):
                    report.n_empty_file += 1
                elif "unexpected_schema" in result.error:
                    report.n_unexpected_schema += 1
                continue

            if result.had_header:
                report.n_files_with_header += 1
            if result.timestamp_unit:
                report.timestamp_units_seen.add(result.timestamp_unit)
            report.warnings.extend(f"{f.name}: {w}" for w in result.warnings)
            report.n_rows_raw += result.n_rows
            frames.append(result.frame)

    if not frames:
        return report

    combined = pd.concat(frames, ignore_index=True)
    n_before = len(combined)
    combined = combined.sort_values("open_time")
    n_dupe_ts = combined.duplicated(subset=["open_time"]).sum()
    report.n_duplicate_rows = int(n_dupe_ts)

    combined = combined.drop_duplicates(subset=["open_time"], keep="last").reset_index(drop=True)
    report.n_rows_after_dedup = len(combined)

    price_cols = ["open", "high", "low", "close"]
    bad_price_mask = (combined[price_cols] <= 0).any(axis=1) | combined[price_cols].isna().any(axis=1)
    report.n_negative_or_zero_price = int(bad_price_mask.sum())

    ohlc_bad = (
        (combined["high"] < combined[["open", "close", "low"]].max(axis=1))
        | (combined["low"] > combined[["open", "close", "high"]].min(axis=1))
    )
    report.n_bad_ohlc_relationship = int(ohlc_bad.sum())

    if len(combined) > 1:
        expected_step = INTERVAL_TIMEDELTA[interval]
        deltas = combined["open_time"].diff().dropna()
        gap_counts = (deltas / expected_step).round().astype("int64") - 1
        gap_counts = gap_counts.clip(lower=0)
        report.n_gap_periods = int(gap_counts.sum())
        report.max_gap_periods = int(gap_counts.max()) if len(gap_counts) else 0

    report.earliest = combined["open_time"].min().isoformat()
    report.latest = combined["open_time"].max().isoformat()

    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--intervals", default="1d,4h,1h")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of symbols (for a quick test run)")
    parser.add_argument("--out", default=str(REPORT_DIR / "data_quality_report.json"))
    args = parser.parse_args()

    intervals = args.intervals.split(",")
    symbols = discover_symbols()
    if args.limit:
        symbols = symbols[: args.limit]

    all_reports: list[SymbolIntervalReport] = []
    for symbol in tqdm(symbols, desc="Auditing symbols"):
        for interval in intervals:
            all_reports.append(audit_symbol_interval(symbol, interval))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    serializable = []
    for r in all_reports:
        d = asdict(r)
        d["timestamp_units_seen"] = sorted(r.timestamp_units_seen)
        serializable.append(d)

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2)

    # Console summary
    total_files = sum(r.n_files_monthly + r.n_files_daily for r in all_reports)
    total_bad_zip = sum(r.n_bad_zip for r in all_reports)
    total_empty = sum(r.n_empty_file for r in all_reports)
    total_schema = sum(r.n_unexpected_schema for r in all_reports)
    total_dupe = sum(r.n_duplicate_rows for r in all_reports)
    total_bad_price = sum(r.n_negative_or_zero_price for r in all_reports)
    total_bad_ohlc = sum(r.n_bad_ohlc_relationship for r in all_reports)
    total_gaps = sum(r.n_gap_periods for r in all_reports)
    units = sorted({u for r in all_reports for u in r.timestamp_units_seen})
    header_files = sum(r.n_files_with_header for r in all_reports)

    print("\n=== Data Audit Summary ===")
    print(f"Symbols audited: {len(symbols)}")
    print(f"Intervals: {intervals}")
    print(f"Total ZIP files scanned: {total_files}")
    print(f"Bad/corrupt ZIPs: {total_bad_zip}")
    print(f"Empty/zero-row files: {total_empty}")
    print(f"Unexpected schema files: {total_schema}")
    print(f"Files with CSV header row: {header_files}")
    print(f"Timestamp units observed: {units}")
    print(f"Duplicate candles (monthly/daily overlap): {total_dupe}")
    print(f"Zero/negative/NaN price rows: {total_bad_price}")
    print(f"Bad OHLC relationship rows: {total_bad_ohlc}")
    print(f"Total missing candle-periods (gaps): {total_gaps}")
    print(f"\nFull report written to: {out_path}")


if __name__ == "__main__":
    main()
