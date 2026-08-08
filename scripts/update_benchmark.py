"""Explicitly ingest an AKShare index benchmark into the local data lake."""

from __future__ import annotations

import argparse
import logging
from datetime import date

from a_share_quant.config import Settings
from a_share_quant.data.providers.akshare import AKShareDataProvider
from a_share_quant.storage.market_store import MarketDataStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Update a local index benchmark")
    parser.add_argument("--symbol", default="000300")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument(
        "--network-smoke",
        action="store_true",
        help="acknowledge that this command calls an external AKShare endpoint",
    )
    args = parser.parse_args()
    if not args.network_smoke:
        parser.error("add --network-smoke to explicitly permit an external data request")

    settings = Settings.load()
    if settings.provider != "akshare":
        parser.error("benchmark ingestion currently requires provider=akshare")
    provider = AKShareDataProvider(
        timeout_seconds=settings.request_timeout_seconds,
        retry_count=settings.request_retry_count,
        delay_seconds=settings.request_delay_seconds,
    )
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    store = MarketDataStore(root=settings.data_dir, database_path=settings.database_path)
    bars = provider.get_index_daily_bars(args.symbol, args.start_date, args.end_date)
    store.write_daily_bars(bars)
    logging.getLogger(__name__).info(
        "benchmark update completed symbol=%s rows=%s",
        args.symbol,
        len(bars),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
