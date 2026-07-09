from __future__ import annotations

import argparse
import asyncio
import json
import os
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import httpx
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.text import Text

ARCHIVE_BASE_URL = "https://data.binance.vision/data/spot"
EXCHANGE_INFO_URL = "https://data-api.binance.vision/api/v3/exchangeInfo"

DEFAULT_OUTPUT_DIRECTORY = Path("data/raw/binance/spot")

DEFAULT_EXCLUDED_BASE_ASSETS = {
    "USDC",
    "FDUSD",
    "TUSD",
    "USDP",
    "BUSD",
    "DAI",
    "EUR",
    "TRY",
    "BRL",
    "GBP",
    "AUD",
    "JPY",
    "RUB",
    "UAH",
    "NGN",
    "ZAR",
    "IDRT",
    "BIDR",
}

VALID_INTERVALS = {
    "1s",
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "8h",
    "12h",
    "1d",
    "3d",
    "1w",
    "1mo",
}


@dataclass(frozen=True)
class DownloadItem:
    url: str
    destination: Path


@dataclass
class DownloadResult:
    status: str
    url: str
    destination: Path
    message: str | None = None


class DownloadStatsColumn(ProgressColumn):
    """Render download counters in a compact Rich progress column."""

    def render(self, task: Task) -> Text:
        downloaded = int(task.fields.get("downloaded", 0))
        existing = int(task.fields.get("existing", 0))
        missing = int(task.fields.get("missing", 0))
        failed = int(task.fields.get("failed", 0))

        text = Text()
        text.append(f"new {downloaded:,}", style="bold green")
        text.append(" · ")
        text.append(f"old {existing:,}", style="cyan")
        text.append(" · ")
        text.append(f"404 {missing:,}", style="yellow")
        text.append(" · ")
        text.append(f"err {failed:,}", style="bold red")

        return text


def parse_month(value: str) -> date:
    """Parse YYYY-MM into the first day of that month."""
    try:
        year_text, month_text = value.split("-", maxsplit=1)
        return date(int(year_text), int(month_text), 1)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError(
            "Month must use YYYY-MM format, for example 2019-01."
        ) from error


def iterate_months(start: date, end: date) -> Iterable[date]:
    """Yield the first day of every month from start through end."""
    current = start.replace(day=1)
    final = end.replace(day=1)

    while current <= final:
        yield current

        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)


def iterate_days(start: date, end: date) -> Iterable[date]:
    """Yield every calendar day from start through end."""
    current = start

    while current <= end:
        yield current
        current += timedelta(days=1)


def validate_intervals(intervals: list[str]) -> None:
    invalid = sorted(set(intervals) - VALID_INTERVALS)

    if invalid:
        raise ValueError(
            f"Unsupported interval(s): {', '.join(invalid)}. "
            f"Supported intervals: {', '.join(sorted(VALID_INTERVALS))}"
        )


def is_valid_zip(path: Path) -> bool:
    """Return True when a file is a readable, non-empty ZIP archive."""
    if not path.exists() or path.stat().st_size == 0:
        return False

    try:
        with zipfile.ZipFile(path) as archive:
            return archive.testzip() is None
    except (OSError, zipfile.BadZipFile):
        return False


async def fetch_symbols(
    client: httpx.AsyncClient,
    quote_asset: str,
    include_stablecoins: bool,
    requested_symbols: list[str] | None,
) -> list[dict[str, Any]]:
    """Fetch currently active Binance Spot symbols."""
    response = await client.get(EXCHANGE_INFO_URL)
    response.raise_for_status()

    payload = response.json()

    requested_set = (
        {symbol.upper() for symbol in requested_symbols} if requested_symbols else None
    )

    selected: list[dict[str, Any]] = []

    for item in payload.get("symbols", []):
        symbol = str(item.get("symbol", "")).upper()
        base_asset = str(item.get("baseAsset", "")).upper()
        item_quote_asset = str(item.get("quoteAsset", "")).upper()

        if not symbol:
            continue

        if item.get("status") != "TRADING":
            continue

        if not item.get("isSpotTradingAllowed", False):
            continue

        if quote_asset != "ALL" and item_quote_asset != quote_asset:
            continue

        if requested_set is not None and symbol not in requested_set:
            continue

        if not include_stablecoins and base_asset in DEFAULT_EXCLUDED_BASE_ASSETS:
            continue

        selected.append(
            {
                "symbol": symbol,
                "base_asset": base_asset,
                "quote_asset": item_quote_asset,
                "status": item.get("status"),
            }
        )

    selected.sort(key=lambda row: row["symbol"])

    if requested_set is not None:
        found = {row["symbol"] for row in selected}
        unavailable = sorted(requested_set - found)

        if unavailable:
            print(
                "Warning: these symbols were not active eligible Spot pairs: "
                + ", ".join(unavailable)
            )

    return selected


def create_monthly_item(
    output_root: Path,
    symbol: str,
    interval: str,
    month: date,
) -> DownloadItem:
    month_text = month.strftime("%Y-%m")
    filename = f"{symbol}-{interval}-{month_text}.zip"

    url = f"{ARCHIVE_BASE_URL}/monthly/klines/{symbol}/{interval}/{filename}"

    destination = output_root / "monthly" / "klines" / symbol / interval / filename

    return DownloadItem(
        url=url,
        destination=destination,
    )


def create_daily_item(
    output_root: Path,
    symbol: str,
    interval: str,
    day: date,
) -> DownloadItem:
    day_text = day.isoformat()
    filename = f"{symbol}-{interval}-{day_text}.zip"

    url = f"{ARCHIVE_BASE_URL}/daily/klines/{symbol}/{interval}/{filename}"

    destination = output_root / "daily" / "klines" / symbol / interval / filename

    return DownloadItem(
        url=url,
        destination=destination,
    )


def build_download_items(
    symbols: list[dict[str, Any]],
    intervals: list[str],
    start_month: date,
    output_root: Path,
    monthly_only: bool,
) -> tuple[list[DownloadItem], date]:
    today = date.today()
    current_month_start = today.replace(day=1)

    latest_complete_month_day = current_month_start - timedelta(days=1)
    latest_complete_month = latest_complete_month_day.replace(day=1)

    latest_expected_daily = today - timedelta(days=1)

    items: list[DownloadItem] = []

    for symbol_info in symbols:
        symbol = symbol_info["symbol"]

        for interval in intervals:
            for month in iterate_months(
                start=start_month,
                end=latest_complete_month,
            ):
                items.append(
                    create_monthly_item(
                        output_root=output_root,
                        symbol=symbol,
                        interval=interval,
                        month=month,
                    )
                )

            if monthly_only:
                continue

            if current_month_start <= latest_expected_daily:
                for day in iterate_days(
                    start=current_month_start,
                    end=latest_expected_daily,
                ):
                    items.append(
                        create_daily_item(
                            output_root=output_root,
                            symbol=symbol,
                            interval=interval,
                            day=day,
                        )
                    )

    return items, latest_complete_month


async def download_item(
    client: httpx.AsyncClient,
    item: DownloadItem,
    verify_existing: bool,
    retries: int,
) -> DownloadResult:
    destination = item.destination
    temporary = destination.with_suffix(destination.suffix + ".part")

    if destination.exists():
        if not verify_existing or is_valid_zip(destination):
            return DownloadResult(
                status="existing",
                url=item.url,
                destination=destination,
            )

        destination.unlink(missing_ok=True)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary.unlink(missing_ok=True)

    last_error: str | None = None

    for attempt in range(1, retries + 1):
        try:
            async with client.stream("GET", item.url) as response:
                if response.status_code == 404:
                    return DownloadResult(
                        status="missing",
                        url=item.url,
                        destination=destination,
                    )

                if response.status_code == 429:
                    retry_after_text = response.headers.get(
                        "Retry-After",
                        "5",
                    )

                    try:
                        retry_after = max(float(retry_after_text), 1.0)
                    except ValueError:
                        retry_after = 5.0

                    last_error = "HTTP 429 rate limit"
                    await asyncio.sleep(retry_after)
                    continue

                if response.status_code >= 500:
                    last_error = f"Server returned HTTP {response.status_code}"

                    await asyncio.sleep(min(2**attempt, 30))
                    continue

                response.raise_for_status()

                with temporary.open("wb") as output_file:
                    async for chunk in response.aiter_bytes(chunk_size=1024 * 128):
                        output_file.write(chunk)

            if not is_valid_zip(temporary):
                last_error = "Downloaded file is not a valid ZIP archive"
                temporary.unlink(missing_ok=True)

                if attempt < retries:
                    await asyncio.sleep(min(2**attempt, 30))
                    continue

                break

            os.replace(temporary, destination)

            return DownloadResult(
                status="downloaded",
                url=item.url,
                destination=destination,
            )

        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
            httpx.HTTPStatusError,
            OSError,
        ) as error:
            last_error = str(error)
            temporary.unlink(missing_ok=True)

            if attempt < retries:
                await asyncio.sleep(min(2**attempt, 30))

    temporary.unlink(missing_ok=True)

    return DownloadResult(
        status="failed",
        url=item.url,
        destination=destination,
        message=last_error or "Unknown download failure",
    )


async def download_worker(
    item_queue: asyncio.Queue[DownloadItem | None],
    result_queue: asyncio.Queue[DownloadResult],
    client: httpx.AsyncClient,
    verify_existing: bool,
    retries: int,
) -> None:
    while True:
        item = await item_queue.get()

        try:
            if item is None:
                return

            result = await download_item(
                client=client,
                item=item,
                verify_existing=verify_existing,
                retries=retries,
            )

            await result_queue.put(result)

        finally:
            item_queue.task_done()


async def progress_reporter(
    result_queue: asyncio.Queue[DownloadResult],
    total: int,
    refresh_per_second: int,
) -> tuple[Counter[str], list[DownloadResult]]:
    summary: Counter[str] = Counter()
    failures: list[DownloadResult] = []

    console = Console()

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]Binance Spot[/bold cyan]"),
        BarColumn(bar_width=28),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        DownloadStatsColumn(),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        console=console,
        refresh_per_second=refresh_per_second,
        transient=False,
    )

    with progress:
        task_id = progress.add_task(
            "Binance Spot",
            total=total,
            downloaded=0,
            existing=0,
            missing=0,
            failed=0,
        )

        for _ in range(total):
            result = await result_queue.get()

            try:
                summary[result.status] += 1

                if result.status == "failed":
                    failures.append(result)

                progress.update(
                    task_id,
                    advance=1,
                    downloaded=summary["downloaded"],
                    existing=summary["existing"],
                    missing=summary["missing"],
                    failed=summary["failed"],
                )

            finally:
                result_queue.task_done()

    return summary, failures


async def run_downloads(
    items: list[DownloadItem],
    worker_count: int,
    refresh_per_second: int,
    verify_existing: bool,
    retries: int,
) -> tuple[Counter[str], list[DownloadResult]]:
    if not items:
        return Counter(), []

    timeout = httpx.Timeout(
        connect=30.0,
        read=180.0,
        write=30.0,
        pool=30.0,
    )

    limits = httpx.Limits(
        max_connections=worker_count,
        max_keepalive_connections=worker_count,
    )

    item_queue: asyncio.Queue[DownloadItem | None] = asyncio.Queue(
        maxsize=max(worker_count * 4, 1)
    )

    result_queue: asyncio.Queue[DownloadResult] = asyncio.Queue()

    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        headers={
            "User-Agent": "binance-spot-research-downloader/3.0",
        },
    ) as client:
        workers = [
            asyncio.create_task(
                download_worker(
                    item_queue=item_queue,
                    result_queue=result_queue,
                    client=client,
                    verify_existing=verify_existing,
                    retries=retries,
                )
            )
            for _ in range(worker_count)
        ]

        reporter = asyncio.create_task(
            progress_reporter(
                result_queue=result_queue,
                total=len(items),
                refresh_per_second=refresh_per_second,
            )
        )

        for item in items:
            await item_queue.put(item)

        for _ in workers:
            await item_queue.put(None)

        await item_queue.join()
        await result_queue.join()

        await asyncio.gather(*workers)

        summary, failures = await reporter

    return summary, failures


def save_failures(
    output_root: Path,
    failures: list[DownloadResult],
) -> Path | None:
    failure_path = output_root / "failed_downloads.txt"

    if not failures:
        failure_path.unlink(missing_ok=True)
        return None

    lines: list[str] = []

    for failure in failures:
        lines.append(f"URL: {failure.url}")
        lines.append(f"Destination: {failure.destination}")
        lines.append(f"Error: {failure.message or 'Unknown error'}")
        lines.append("")

    failure_path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    return failure_path


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download Binance Spot kline archives with resumable "
            "downloads and a Rich progress display."
        )
    )

    parser.add_argument(
        "--quote",
        default="USDT",
        help=("Quote asset to download. Default: USDT. Use ALL for every quote asset."),
    )

    parser.add_argument(
        "--start",
        type=parse_month,
        default=parse_month("2019-01"),
        help="Starting month in YYYY-MM format. Default: 2019-01.",
    )

    parser.add_argument(
        "--intervals",
        nargs="+",
        default=["1h"],
        help=("Intervals such as 1h, 4h and 1d. Default: 1h."),
    )

    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help=("Optional exact symbols, for example BTCUSDT ETHUSDT SOLUSDT."),
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=12,
        help="Concurrent download workers. Default: 12.",
    )

    parser.add_argument(
        "--refresh-rate",
        type=int,
        default=8,
        help=("Rich progress refreshes per second. Default: 8."),
    )

    parser.add_argument(
        "--retries",
        type=int,
        default=4,
        help="Attempts for temporary failures. Default: 4.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
        help="Output directory. Default: data/raw/binance/spot.",
    )

    parser.add_argument(
        "--include-stablecoins",
        action="store_true",
        help="Include stablecoin and fiat-like base assets.",
    )

    parser.add_argument(
        "--monthly-only",
        action="store_true",
        help="Do not download current-month daily archives.",
    )

    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help=(
            "Validate existing ZIP files before skipping them. "
            "This is safer but slower."
        ),
    )

    return parser


async def async_main() -> None:
    parser = build_argument_parser()
    args = parser.parse_args()

    args.quote = args.quote.upper()
    args.intervals = list(dict.fromkeys(args.intervals))

    validate_intervals(args.intervals)

    if args.workers < 1:
        parser.error("--workers must be at least 1.")

    if args.workers > 50:
        parser.error(
            "--workers must not exceed 50. "
            "Use a moderate value to avoid excessive server load."
        )

    if args.refresh_rate < 1:
        parser.error("--refresh-rate must be at least 1.")

    if args.refresh_rate > 30:
        parser.error("--refresh-rate must not exceed 30.")

    if args.retries < 1:
        parser.error("--retries must be at least 1.")

    args.output.mkdir(parents=True, exist_ok=True)

    metadata_timeout = httpx.Timeout(
        connect=30.0,
        read=60.0,
        write=30.0,
        pool=30.0,
    )

    async with httpx.AsyncClient(
        timeout=metadata_timeout,
        follow_redirects=True,
        headers={
            "User-Agent": "binance-spot-research-downloader/3.0",
        },
    ) as metadata_client:
        symbols = await fetch_symbols(
            client=metadata_client,
            quote_asset=args.quote,
            include_stablecoins=args.include_stablecoins,
            requested_symbols=args.symbols,
        )

    if not symbols:
        raise RuntimeError("No eligible active Binance Spot symbols were found.")

    universe_path = args.output / "current_universe.json"

    universe_path.write_text(
        json.dumps(symbols, indent=2),
        encoding="utf-8",
    )

    items, latest_complete_month = build_download_items(
        symbols=symbols,
        intervals=args.intervals,
        start_month=args.start,
        output_root=args.output,
        monthly_only=args.monthly_only,
    )

    console = Console()

    console.print(f"Selected symbols: [bold]{len(symbols):,}[/bold]")
    console.print(f"Universe saved to: [cyan]{universe_path}[/cyan]")
    console.print(f"Intervals: [bold]{', '.join(args.intervals)}[/bold]")
    console.print(f"Starting month: [bold]{args.start:%Y-%m}[/bold]")
    console.print(
        f"Latest expected monthly archive: [bold]{latest_complete_month:%Y-%m}[/bold]"
    )
    console.print(f"Files to check/download: [bold]{len(items):,}[/bold]\n")

    summary, failures = await run_downloads(
        items=items,
        worker_count=args.workers,
        refresh_per_second=args.refresh_rate,
        verify_existing=args.verify_existing,
        retries=args.retries,
    )

    failure_path = save_failures(
        output_root=args.output,
        failures=failures,
    )

    console.print("\n[bold]Download summary[/bold]")
    console.print("────────────────────────")
    console.print(f"Downloaded: [bold green]{summary['downloaded']:,}[/bold green]")
    console.print(f"Existing:   [cyan]{summary['existing']:,}[/cyan]")
    console.print(f"Missing:    [yellow]{summary['missing']:,}[/yellow]")
    console.print(f"Failed:     [bold red]{summary['failed']:,}[/bold red]")

    if failure_path:
        console.print(f"Failure details: [red]{failure_path}[/red]")
    else:
        console.print("[green]No permanent download failures.[/green]")

    console.print(f"Data directory: [cyan]{args.output}[/cyan]")


def main() -> None:
    try:
        asyncio.run(async_main())

    except KeyboardInterrupt:
        print(
            "\nStopped by user. Completed ZIP files were preserved. "
            "Run the same command again to resume."
        )

    except httpx.HTTPError as error:
        raise SystemExit(f"Binance request failed: {error}") from error

    except ValueError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
