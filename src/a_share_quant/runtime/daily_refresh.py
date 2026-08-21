"""Bounded, credential-free BaoStock refresh for the local daily data lake."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from a_share_quant.data.pipeline import IncrementalUpdater, UpdateSummary
from a_share_quant.data.providers.baostock import BaoStockDataProvider
from a_share_quant.storage.market_store import MarketDataStore

DEFAULT_INDEX_SYMBOLS = ("000300",)


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
    minimum_history_rows: int = 252,
    index_symbols: tuple[str, ...] = DEFAULT_INDEX_SYMBOLS,
) -> DailyRefreshSummary:
    """Incrementally update BaoStock bars only when the lake is behind.

    The function does not require credentials.  It makes no network call when
    every local bar already reaches ``end_date``.  A provider supplied by tests
    or an operator is closed when this function created it.
    """

    if lookback_days < 60:
        raise ValueError("lookback_days must be at least 60")
    if minimum_history_rows < 1:
        raise ValueError("minimum_history_rows must be positive")
    root = Path(data_root).resolve()
    target = end_date or _latest_complete_weekday()
    daily_dir = root / "lake" / "daily_bars"
    paths = tuple(daily_dir.glob("*.parquet"))
    profiles = tuple(_daily_file_profile(path) for path in paths)
    valid_profiles = tuple(profile for profile in profiles if profile is not None)
    required_version = getattr(provider, "daily_data_version", None)
    complete = (
        bool(paths)
        and len(valid_profiles) == len(paths)
        and all(
            latest >= target
            and rows >= minimum_history_rows
            and (required_version is None or version == required_version)
            for latest, rows, version in valid_profiles
        )
    )
    if complete:
        return DailyRefreshSummary(skipped=True)

    for path, profile in zip(paths, profiles, strict=True):
        if profile is None:
            path.replace(path.with_name(f"{path.name}.corrupt-{uuid4().hex}"))

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
                    required_data_version=getattr(active_provider, "daily_data_version", None),
                    minimum_history_rows=minimum_history_rows,
                ).run(start_date=target - timedelta(days=lookback_days), end_date=target)
            finally:
                active_provider.list_instruments = original_list
            _refresh_existing_indices(
                store=store,
                provider=active_provider,
                symbols=tuple(
                    sorted(
                        (
                            set(existing_symbols)
                            - set(instruments["symbol"].astype(str))
                        )
                        & set(index_symbols)
                    )
                ),
                start_date=target - timedelta(days=lookback_days),
                end_date=target,
                minimum_history_rows=minimum_history_rows,
                summary=summary,
            )
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
            required_data_version=getattr(active_provider, "daily_data_version", None),
            minimum_history_rows=minimum_history_rows,
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


def _daily_file_profile(path: Path) -> tuple[date, int, str] | None:
    try:
        import pandas as pd

        frame = pd.read_parquet(path, columns=["date", "data_version"])
        if frame.empty:
            return None
        latest = pd.to_datetime(frame["date"], errors="coerce").dt.date.max()
        versions = frame["data_version"].dropna().astype(str).unique().tolist()
        if latest is None or len(versions) != 1:
            return None
        return latest, len(frame), versions[0]
    except (OSError, ValueError, KeyError, ImportError):
        return None


def _latest_date(path: Path) -> date | None:
    profile = _daily_file_profile(path)
    return profile[0] if profile is not None else None


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


def _refresh_existing_indices(
    *,
    store: MarketDataStore,
    provider: Any,
    symbols: tuple[str, ...],
    start_date: date,
    end_date: date,
    minimum_history_rows: int,
    summary: UpdateSummary,
) -> None:
    """Refresh index files omitted from the ordinary stock instrument list.

    BaoStock's ``query_stock_basic`` intentionally excludes indices, while the
    official daily ranking uses CSI 300 (000300) as a benchmark.  Keep this
    narrow and opt-in by only touching an index file that already exists and a
    provider that exposes ``get_index_daily_bars``; no universe expansion or
    fabricated fallback is allowed.
    """

    fetch_index = getattr(provider, "get_index_daily_bars", None)
    if not callable(fetch_index):
        return
    for symbol in symbols:
        summary.symbols_seen += 1
        latest, row_count, stored_version = store.daily_profile(symbol)
        required_version = getattr(provider, "daily_data_version", None)
        replace_history = bool(
            required_version
            and stored_version is not None
            and stored_version != required_version
        )
        needs_backfill = row_count < minimum_history_rows
        requested_start = (
            _stored_earliest_date(store, symbol) or start_date
            if replace_history
            else start_date
            if needs_backfill or latest is None
            else latest + timedelta(days=1)
        )
        if requested_start > end_date:
            summary.symbols_skipped += 1
            continue
        try:
            bars = fetch_index(symbol, requested_start, end_date)
            if replace_history:
                store.replace_daily_bars(bars)
            else:
                store.write_daily_bars(bars)
            summary.symbols_updated += 1
            summary.rows_written += len(bars)
        except Exception as exc:  # one index endpoint must not stop stock refresh
            summary.symbols_failed += 1
            summary.errors.append(f"{symbol}: {type(exc).__name__}")


def _stored_earliest_date(store: MarketDataStore, symbol: str) -> date | None:
    """Return the earliest local date before replacing a versioned history."""

    try:
        import pandas as pd

        frame = pd.read_parquet(store.daily_dir / f"{symbol}.parquet", columns=["date"])
        if frame.empty:
            return None
        values = pd.to_datetime(frame["date"], errors="coerce").dropna()
        return values.min().date() if not values.empty else None
    except (OSError, ValueError, KeyError, ImportError):
        return None
