"""Explicit operator entry point for the first data ingestion path."""

from __future__ import annotations

import argparse
import logging
from datetime import date

from a_share_quant.config import Settings
from a_share_quant.data.pipeline import IncrementalUpdater
from a_share_quant.data.providers.akshare import AKShareDataProvider
from a_share_quant.data.providers.baostock import BaoStockDataProvider
from a_share_quant.data.providers.registry import ProviderRegistry
from a_share_quant.data.providers.tushare import TushareDataProvider
from a_share_quant.storage.market_store import MarketDataStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Update local A-share daily data")
    parser.add_argument("--provider", choices=["akshare", "baostock", "tushare"])
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--minimum-history-rows",
        type=int,
        default=0,
        help="对已有短历史也执行回填；日选生产建议至少 252 行",
    )
    parser.add_argument(
        "--provider-evidence",
        type=str,
        default=None,
        help="persisted provider provenance path; switching a formal source requires evidence",
    )
    parser.add_argument(
        "--network-smoke",
        action="store_true",
        help="acknowledge that this command will call an external data provider",
    )
    args = parser.parse_args()
    if not args.network_smoke:
        parser.error("add --network-smoke to explicitly permit an external data request")

    settings = Settings.load()
    provider_name = args.provider or settings.provider
    evidence_path = (
        args.provider_evidence
        if args.provider_evidence is not None
        else str(settings.data_dir / "provider-evidence.json")
    )
    provider_registry = ProviderRegistry(
        formal_provider=provider_name,
        evidence_path=evidence_path,
    )
    if provider_name == "akshare":
        provider = AKShareDataProvider(
            timeout_seconds=settings.request_timeout_seconds,
            retry_count=settings.request_retry_count,
            delay_seconds=settings.request_delay_seconds,
        )
    elif provider_name == "baostock":
        provider = BaoStockDataProvider()
    else:
        provider = TushareDataProvider(token=settings.tushare_token)

    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    store = MarketDataStore(root=settings.data_dir, database_path=settings.database_path)
    try:
        summary = IncrementalUpdater(
            provider=provider,
            store=store,
            minimum_history_rows=args.minimum_history_rows,
        ).run(
            start_date=args.start_date,
            end_date=args.end_date,
            limit=args.limit,
        )
    finally:
        close = getattr(provider, "close", None)
        if close is not None:
            close()
    logging.getLogger(__name__).info(
        "data update completed provider=%s evidence=%s seen=%s updated=%s "
        "skipped=%s failed=%s rows=%s",
        provider_name,
        provider_registry.evidence_path,
        summary.symbols_seen,
        summary.symbols_updated,
        summary.symbols_skipped,
        summary.symbols_failed,
        summary.rows_written,
    )
    return 1 if summary.symbols_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
