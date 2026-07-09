from __future__ import annotations

import argparse
import io
import zipfile
from pathlib import Path

import pandas as pd
import requests

COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]


def month_range(start: str, end: str) -> list[str]:
    """Return YYYY-MM values between start and end, inclusive."""
    return [
        period.strftime("%Y-%m")
        for period in pd.period_range(start=start, end=end, freq="M")
    ]


def download_month(
    symbol: str,
    interval: str,
    month: str,
    output_dir: Path,
) -> Path | None:
    filename = f"{symbol}-{interval}-{month}.zip"
    url = (
        "https://data.binance.vision/data/spot/monthly/klines/"
        f"{symbol}/{interval}/{filename}"
    )

    destination = output_dir / filename

    if destination.exists():
        print(f"Already downloaded: {destination}")
        return destination

    print(f"Downloading: {url}")

    response = requests.get(url, timeout=120)

    if response.status_code == 404:
        print(f"Not available: {filename}")
        return None

    response.raise_for_status()
    destination.write_bytes(response.content)

    return destination


def read_archive(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        csv_names = [
            name for name in archive.namelist() if name.lower().endswith(".csv")
        ]

        if not csv_names:
            raise ValueError(f"No CSV found inside {path}")

        with archive.open(csv_names[0]) as csv_file:
            raw = csv_file.read()

    dataframe = pd.read_csv(
        io.BytesIO(raw),
        header=None,
        names=COLUMNS,
    )

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_asset_volume",
        "number_of_trades",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
    ]

    dataframe[numeric_columns] = dataframe[numeric_columns].apply(
        pd.to_numeric,
        errors="coerce",
    )

    # Binance Spot archives use microseconds from 2025-01-01 onward,
    # while older archives generally use milliseconds.
    timestamp_unit = "us" if dataframe["open_time"].max() > 10_000_000_000_000 else "ms"

    dataframe["open_time"] = pd.to_datetime(
        dataframe["open_time"],
        unit=timestamp_unit,
        utc=True,
    )

    dataframe["close_time"] = pd.to_datetime(
        dataframe["close_time"],
        unit=timestamp_unit,
        utc=True,
    )

    return dataframe


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--start", required=True, help="Example: 2020-01")
    parser.add_argument("--end", required=True, help="Example: 2026-06")
    parser.add_argument("--output", default="data")

    args = parser.parse_args()

    symbol = args.symbol.upper()
    output_dir = Path(args.output) / symbol / args.interval
    output_dir.mkdir(parents=True, exist_ok=True)

    archives: list[Path] = []

    for month in month_range(args.start, args.end):
        archive = download_month(
            symbol=symbol,
            interval=args.interval,
            month=month,
            output_dir=output_dir,
        )

        if archive:
            archives.append(archive)

    if not archives:
        raise RuntimeError("No available archives were downloaded.")

    frames = [read_archive(path) for path in archives]

    combined = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset=["open_time"])
        .sort_values("open_time")
        .reset_index(drop=True)
    )

    parquet_path = output_dir / (
        f"{symbol}-{args.interval}-{args.start}-to-{args.end}.parquet"
    )

    combined.to_parquet(parquet_path, index=False)

    print(f"Rows: {len(combined):,}")
    print(f"From: {combined['open_time'].min()}")
    print(f"To:   {combined['open_time'].max()}")
    print(f"Saved: {parquet_path}")


if __name__ == "__main__":
    main()
