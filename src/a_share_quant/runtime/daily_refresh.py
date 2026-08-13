"""Bounded, credential-free BaoStock refresh for the local daily data lake."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from a_share_quant.data.pipeline import IncrementalUpdater, UpdateSummary
from a_share_quant.data.providers.baostock import BaoStockDataProvider
from a_share_quant.storage.market_store import MarketDataStore


@dataclass(frozen=True)
class DailyRefreshSummary:
    skipped: bool
    rows_written: int = 0
    symbols_seen: int = 0
    symbols_updated: int = 0
    symbols_skipped: int = 0
    symbols_failed: int = 0
    errors: tuple[str, ...] = ()


def refresh_daily_data_if_due(
    data_root: str | Path,
    *,
    end_date: date | None = None,
    provider: Any | None = None,
    lookback_days: int = 370,
) -> DailyRefreshSummary:
    """Incrementally update BaoStock bars only when the lake is behind.

    The function does not require credentials.  It makes no network call when
    every local bar already reaches ``end_date``.  A provider supplied by tests
    or an operator is closed when this function created it.
    """

    if lookback_days < 60:
        raise ValueError("lookback_days must be at least 60")
    root = Path(data_root).resolve()
    target = end_date or _latest_complete_weekday()
    daily_dir = root / "lake" / "daily_bars"
    paths = tuple(daily_dir.glob("*.parquet"))
    latest_dates = tuple(_latest_date(path) for path in paths)
    latest_dates = tuple(value for value in latest_dates if value is not None)
    if paths and latest_dates and min(latest_dates) >= target:
        return DailyRefreshSummary(skipped=True)

    owned_provider = provider is None
    active_provider = provider or BaoStockDataProvider()
    try:
        store = MarketDataStore(root=root, database_path="quant.duckdb")
        if paths:
            existing_symbols = {path.stem for path in paths}
            instruments = active_provider.list_instruments(as_of=target)
            if "symbol" in instruments.columns:
                instruments = instruments.loc[
                    instruments["symbol"].astype(str).str.replace(r"\D", "", regex=True).isin(
                        existing_symbols
                    )
                ].copy()
            if instruments.empty:
                return DailyRefreshSummary(skipped=True)
            # IncrementalUpdater will persist this restricted instrument
            # snapshot and never expand the automatic refresh into the whole
            # exchange universe.
            original_list = active_provider.list_instruments
            active_provider.list_instruments = lambda as_of=None: instruments
            try:
                summary = IncrementalUpdater(
                    provider=active_provider,
                    store=store,
                ).run(start_date=target - timedelta(days=lookback_days), end_date=target)
            finally:
                active_provider.list_instruments = original_list
            return DailyRefreshSummary(
                skipped=False,
                rows_written=summary.rows_written,
                symbols_seen=summary.symbols_seen,
                symbols_updated=summary.symbols_updated,
                symbols_skipped=summary.symbols_skipped,
                symbols_failed=summary.symbols_failed,
                errors=tuple(summary.errors),
            )
        start = target - timedelta(days=lookback_days)
        summary: UpdateSummary = IncrementalUpdater(
            provider=active_provider,
            store=store,
        ).run(start_date=start, end_date=target)
        return DailyRefreshSummary(
            skipped=False,
            rows_written=summary.rows_written,
            symbols_seen=summary.symbols_seen,
            symbols_updated=summary.symbols_updated,
            symbols_skipped=summary.symbols_skipped,
            symbols_failed=summary.symbols_failed,
            errors=tuple(summary.errors),
        )
    finally:
        if owned_provider:
            close = getattr(active_provider, "close", None)
            if close is not None:
                close()


def _latest_date(path: Path) -> date | None:
    try:
        import pandas as pd

        frame = pd.read_parquet(path, columns=["date"])
        if frame.empty:
            return None
        return pd.to_datetime(frame["date"], errors="coerce").dt.date.max()
    except (OSError, ValueError, KeyError, ImportError):
        return None


def _latest_complete_weekday(now: datetime | None = None) -> date:
    local = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(
        ZoneInfo("Asia/Shanghai")
    )
    candidate = local.date()
    if local.hour < 15:
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


__all__ = ["DailyRefreshSummary", "refresh_daily_data_if_due"]
