"""Bounded supplemental quotes for favorites outside the main monitor view."""

from __future__ import annotations

import logging
import threading
import time

from a_share_quant.contracts.data import ProviderError
from a_share_quant.contracts.realtime import DataQualityStatus, RealTimeQuote
from a_share_quant.data.realtime.akshare import AKShareRealTimeProvider
from a_share_quant.data.realtime.tencent import get_tencent_quotes
from a_share_quant.market.trading_calendar import CalendarUnavailableError
from a_share_quant.runtime.scheduler import MarketSession, SessionResolver
from a_share_quant.workbench.service import WorkbenchService

_LOG = logging.getLogger(__name__)


class WatchlistQuotes:
    def __init__(self, service: WorkbenchService) -> None:
        self._service = service
        self._lock = threading.Lock()
        self._session = SessionResolver()
        self._provider = AKShareRealTimeProvider(retry_count=0, priority_timeout_seconds=5)
        self._quotes: dict[tuple[str, str], RealTimeQuote] = {}
        self._thread: threading.Thread | None = None
        self._next_request = 0.0
        self._cursor = 0
        self._closed = False

    def enrich(self, rows: list[dict], *, allow_network: bool) -> list[dict]:
        active = {(row["exchange"], row["symbol"]) for row in rows if row["verified"]}
        primary = {
            item.symbol: item
            for item in self._service.store.quotes(symbol for _, symbol in active)
            if item.market != "INDEX"
        }
        now = self._service.clock()
        try:
            market_open = self._session.resolve(now) is MarketSession.OPEN
        except CalendarUnavailableError:
            market_open = False

        with self._lock:
            self._quotes = {key: quote for key, quote in self._quotes.items() if key in active}
            result = []
            missing = []
            for row in rows:
                exchange, symbol = row["exchange"], row["symbol"]
                key = (exchange, symbol)
                quote = primary.get(symbol) if key in active else None
                if quote is not None and quote.market in {"SSE", "SZSE", "BSE", "SH", "SZ", "BJ"}:
                    market = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(
                        quote.market, quote.market,
                    )
                    if market != exchange:
                        quote = None
                status = self._quality(quote, now, market_open)
                supplement = self._quotes.get(key)
                supplement_status = self._quality(supplement, now, market_open)
                if status == "NO_QUOTE" or (
                    status != "LIVE" and supplement_status == "LIVE"
                ) or (
                    supplement_status == "CLOSED_CACHE" and (
                        status != "CLOSED_CACHE"
                        or supplement.timestamp_exchange > quote.timestamp_exchange
                    )
                ):
                    quote, status = supplement, supplement_status
                if key in active and status in {"STALE", "NO_QUOTE", "UNVERIFIED"}:
                    missing.append(key)
                result.append({
                    **row,
                    "quote_status": status,
                    "price": quote.last if quote else None,
                    "quote_source": quote.source if quote else None,
                    "quote_time": (
                        (quote.timestamp_received if status == "UNVERIFIED"
                         else quote.timestamp_exchange).isoformat()
                        if quote else None
                    ),
                })
            if allow_network and market_open and missing:
                self._queue(tuple(dict.fromkeys(missing)))
            return result

    def _quality(self, quote: RealTimeQuote | None, now, market_open: bool) -> str:
        if quote is None or quote.last is None:
            return "NO_QUOTE"
        age = (now - quote.timestamp_exchange).total_seconds()
        if quote.quality_flag is not DataQualityStatus.GOOD or quote.is_stale or age < 0:
            return "UNVERIFIED"
        if not market_open:
            return "CLOSED_CACHE"
        return "LIVE" if age <= self._service.live_quality_gate.stale_after_seconds else "STALE"

    def _queue(self, missing: tuple[tuple[str, str], ...]) -> None:
        now = time.monotonic()
        if self._closed or now < self._next_request or (
            self._thread is not None and self._thread.is_alive()
        ):
            return
        self._next_request = now + 30
        start = self._cursor % len(missing)
        batch = (missing[start:] + missing[:start])[:3]
        self._cursor = start + len(batch)
        self._thread = threading.Thread(target=self._query, args=(batch,), daemon=True)
        self._thread.start()

    def _query(self, batch: tuple[tuple[str, str], ...]) -> None:
        try:
            found = {(quote.market, quote.symbol): quote for quote in get_tencent_quotes(batch)}
        except (OSError, ValueError) as exc:
            _LOG.info("腾讯自选补查暂不可用 (%s)", type(exc).__name__)
            found = {}
        missing = [key for key in batch if key not in found]
        if missing and not self._closed:
            try:
                fallback = self._provider.get_priority_quotes(tuple(code for _, code in missing))
                by_symbol = {quote.symbol: quote for quote in fallback}
                found.update((key, by_symbol[key[1]]) for key in missing if key[1] in by_symbol)
            except (ProviderError, OSError, ValueError) as exc:
                if not self._closed:
                    _LOG.info("补查自选报价暂不可用 (%s)", type(exc).__name__)
        with self._lock:
            if not self._closed:
                self._quotes.update(found)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            thread = self._thread
        self._provider.close()
        if thread is not None:
            thread.join(timeout=3)
