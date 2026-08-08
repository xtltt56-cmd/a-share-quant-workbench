"""Incremental provider-to-store ingestion orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from a_share_quant.contracts.data import MarketDataProvider
from a_share_quant.storage.market_store import MarketDataStore


@dataclass
class UpdateSummary:
    symbols_seen: int = 0
    symbols_updated: int = 0
    symbols_skipped: int = 0
    symbols_failed: int = 0
    rows_written: int = 0
    errors: list[str] = field(default_factory=list)


class IncrementalUpdater:
    def __init__(self, *, provider: MarketDataProvider, store: MarketDataStore) -> None:
        self.provider = provider
        self.store = store

    def run(
        self,
        *,
        start_date: date,
        end_date: date,
        limit: int | None = None,
    ) -> UpdateSummary:
        if start_date > end_date:
            raise ValueError("start_date cannot be later than end_date")
        instruments = self.provider.list_instruments(as_of=end_date)
        self.store.write_instruments(instruments)
        if limit is not None:
            instruments = instruments.head(limit)

        summary = UpdateSummary(symbols_seen=len(instruments))
        for row in instruments.itertuples(index=False):
            symbol = str(row.symbol)
            latest = self.store.latest_date(symbol)
            requested_start = latest + timedelta(days=1) if latest is not None else start_date
            if requested_start > end_date:
                summary.symbols_skipped += 1
                continue
            try:
                bars = self.provider.get_daily_bars(symbol, requested_start, end_date)
                self.store.write_daily_bars(bars)
                summary.symbols_updated += 1
                summary.rows_written += len(bars)
            except Exception as exc:  # one bad endpoint must not corrupt other symbols
                summary.symbols_failed += 1
                summary.errors.append(f"{symbol}: {type(exc).__name__}")
        return summary
