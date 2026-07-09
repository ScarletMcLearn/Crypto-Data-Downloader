from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

ARCHIVE_BASE_URL = "https://data.binance.vision/data/spot"
EXCHANGE_INFO_URL = "https://data-api.binance.vision/api/v3/exchangeInfo"

# These are generally not useful as independent swing-trading assets
# when your quote currency is already USDT.
DEFAULT_EXCLUDED_BASE_ASSETS = {
    "USDC",
    "FDUSD",
    "TUSD",
    "USDP",
    "DAI",
    "EUR",
    "TRY",
    "BRL",
    "GBP",
    "AUD",
}


def parse_month(value: str) -> date:
    try:
        year, month = map(int, value.split("-"))
        return date(year, month, 1)
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError(
            "Month must use YYYY-MM format, for example 2019-01"
        ) from error


def iterate_months(start: date, end: date):
    current = start

    while current <= end:
        yield current

        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)


def iterate_days(start: date, end: date):
    current = start

    while current <= end:
        yield current
        current += timedelta(days=1)


async def fetch_symbols(
    client: httpx.AsyncClient,
    quote_asset: str,
    include_stablecoins: bool,
) -> list[dict[str, Any]]:
    response = await client.get(
        EXCHANGE_INFO_URL,
        params={
            "permissions": "SPOT",
            "symbolStatus": "TRADING",
            "showPermissionSets": "false",
        },
    )
    response.raise_for_status()

    exchange_info = response.json()
    selected: list[dict[str, Any]] = []

    for symbol_info in exchange_info["symbols"]:
        if symbol_info.get("status") != "TRADING":
            continue

        if not symbol_info.get("isSpotTradingAllowed", False):
            continue

        if quote_asset != "ALL" and symbol_info.get("quoteAsset") != quote_asset:
            continue

        if (
            not include_stablecoins
            and symbol_info.get("baseAsset") in DEFAULT_EXCLUDED_BASE_ASSETS
        ):
            continue

        selected.append(
            {
                "symbol": symbol_info["symbol"],
                "base_asset": symbol_info["baseAsset"],
                "quote_asset": symbol_info["quoteAsset"],
                "status": symbol_info["status"],
            }
        )

    return sorted(selected, key=lambda item: item["symbol"])


def monthly_download(
    root: Path,
    symbol: str,
    interval: str,
    month: date,
) -> tuple[str, Path]:
    month_string = month.strftime("%Y-%m")
    filename = f"{symbol}-{interval}-{month_string}.zip"

    url = f"{ARCHIVE_BASE_URL}/monthly/klines/{symbol}/{interval}/{filename}"

    destination = root / "monthly" / "klines" / symbol / interval / filename

    return url, destination


def daily_download(
    root: Path,
    symbol: str,
    interval: str,
    day: date,
) -> tuple[str, Path]:
    day_string = day.isoformat()
    filename = f"{symbol}-{interval}-{day_string}.zip"

    url = f"{ARCHIVE_BASE_URL}/daily/klines/{symbol}/{interval}/{filename}"

    destination = root / "daily" / "klines" / symbol / interval / filename

    return url, destination


async def download_file(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    url: str,
    destination: Path,
    retries: int = 4,
) -> str:
    if destination.exists() and destination.stat().st_size > 0:
        return "existing"

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")

    for attempt in range(retries):
        try:
            async with semaphore:
                async with client.stream("GET", url) as response:
                    if response.status_code == 404:
                        return "missing"

                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", "5"))
                        await asyncio.sleep(retry_after)
                        continue

                    response.raise_for_status()

                    with temporary.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            output.write(chunk)

            os.replace(temporary, destination)
            return "downloaded"

        except (
            httpx.HTTPError,
            OSError,
        ) as error:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

            if attempt == retries - 1:
                print(f"Failed: {url}: {error}")
                return "failed"

            await asyncio.sleep(2**attempt)

    return "failed"


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Binance Spot kline archives."
    )
    parser.add_argument(
        "--quote",
        default="USDT",
        help="Quote asset, normally USDT. Use ALL for every quote asset.",
    )
    parser.add_argument(
        "--start",
        type=parse_month,
        default=parse_month("2019-01"),
        help="Starting month in YYYY-MM format.",
    )
    parser.add_argument(
        "--intervals",
        nargs="+",
        default=["1h"],
        help="Intervals such as 1h, 4h, and 1d.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=12,
        help="Maximum concurrent downloads.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/raw/binance/spot"),
    )
    parser.add_argument(
        "--include-stablecoins",
        action="store_true",
    )
    parser.add_argument(
        "--monthly-only",
        action="store_true",
        help="Do not download current-month daily files.",
    )

    args = parser.parse_args()

    today = date.today()
    current_month_start = today.replace(day=1)
    last_complete_month_day = current_month_start - timedelta(days=1)
    last_complete_month = last_complete_month_day.replace(day=1)

    timeout = httpx.Timeout(
        connect=30,
        read=180,
        write=30,
        pool=30,
    )

    limits = httpx.Limits(
        max_connections=args.workers,
        max_keepalive_connections=args.workers,
    )

    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        headers={"User-Agent": "binance-research-downloader/1.0"},
    ) as client:
        symbols = await fetch_symbols(
            client=client,
            quote_asset=args.quote.upper(),
            include_stablecoins=args.include_stablecoins,
        )

        args.output.mkdir(parents=True, exist_ok=True)

        universe_path = args.output / "current_universe.json"
        universe_path.write_text(
            json.dumps(symbols, indent=2),
            encoding="utf-8",
        )

        print(f"Selected symbols: {len(symbols)}")
        print(f"Universe saved to: {universe_path}")
        print(f"Intervals: {', '.join(args.intervals)}")
        print(f"Starting month: {args.start:%Y-%m}")

        requests_to_make: list[tuple[str, Path]] = []

        for symbol_info in symbols:
            symbol = symbol_info["symbol"]

            for interval in args.intervals:
                for month in iterate_months(
                    args.start,
                    last_complete_month,
                ):
                    requests_to_make.append(
                        monthly_download(
                            root=args.output,
                            symbol=symbol,
                            interval=interval,
                            month=month,
                        )
                    )

                if not args.monthly_only:
                    # Daily archives fill the gap after the latest
                    # completed monthly archive.
                    latest_expected_daily_file = today - timedelta(days=1)

                    for day in iterate_days(
                        current_month_start,
                        latest_expected_daily_file,
                    ):
                        requests_to_make.append(
                            daily_download(
                                root=args.output,
                                symbol=symbol,
                                interval=interval,
                                day=day,
                            )
                        )

        print(f"Files to check/download: {len(requests_to_make):,}")

        semaphore = asyncio.Semaphore(args.workers)

        results = await asyncio.gather(
            *[
                download_file(
                    client=client,
                    semaphore=semaphore,
                    url=url,
                    destination=destination,
                )
                for url, destination in requests_to_make
            ]
        )

        summary = Counter(results)

        print("\nDownload summary")
        print("----------------")
        for status in [
            "downloaded",
            "existing",
            "missing",
            "failed",
        ]:
            print(f"{status:>10}: {summary[status]:,}")


if __name__ == "__main__":
    asyncio.run(main())
