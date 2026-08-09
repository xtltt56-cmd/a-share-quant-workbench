"""Deterministic replay provider for offline real-time pipeline testing."""

from __future__ import annotations

from collections.abc import Iterable

from a_share_quant.contracts.realtime import (
    MarketSnapshot,
    MinuteBar,
    ProviderHealth,
    ProviderMetadata,
    RealTimeQuote,
)
from a_share_quant.data.normalization import normalize_symbol


class ReplayRealTimeProvider:
    name = "replay"

    def __init__(self, *, bars: Iterable[MinuteBar]) -> None:
        self._bars = tuple(sorted(bars, key=lambda bar: (bar.timestamp, bar.symbol)))
        self._cursor = 0

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            provider=self.name,
            market="A",
            frequencies=("1m", "5m", "15m", "30m", "60m"),
            authenticated=False,
            permissions=("replay",),
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            connected=bool(self._bars),
            authenticated=False,
            permissions=("replay",),
            status="READY" if self._bars else "EMPTY",
        )

    def replay_next(self) -> MinuteBar | None:
        if self._cursor >= len(self._bars):
            return None
        bar = self._bars[self._cursor]
        self._cursor += 1
        return bar

    def reset(self) -> None:
        self._cursor = 0

    def get_minute_bars(self, symbols, frequency: str) -> tuple[MinuteBar, ...]:
        requested = {normalize_symbol(symbol) for symbol in symbols}
        current = self._bars[self._cursor] if self._cursor < len(self._bars) else None
        if current is None:
            return ()
        return tuple(
            bar
            for bar in self._bars
            if bar.timestamp == current.timestamp
            and bar.frequency == frequency
            and bar.symbol in requested
        )

    def get_market_snapshot(self) -> MarketSnapshot:
        if not self._bars:
            raise ValueError("replay provider has no bars")
        current_timestamp = self._bars[min(self._cursor, len(self._bars) - 1)].timestamp
        current = [bar for bar in self._bars if bar.timestamp == current_timestamp]
        quotes = tuple(
            RealTimeQuote(
                symbol=bar.symbol,
                market="A",
                timestamp_exchange=bar.timestamp,
                timestamp_received=bar.timestamp,
                last=bar.close,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                volume=bar.volume,
                amount=bar.amount,
                source=self.name,
                quality_flag=bar.quality_flag,
            )
            for bar in current
        )
        return MarketSnapshot(
            timestamp_exchange=current_timestamp,
            timestamp_received=current_timestamp,
            quotes=quotes,
            source=self.name,
        )

    def get_quotes(self, symbols) -> tuple[RealTimeQuote, ...]:
        requested = {normalize_symbol(symbol) for symbol in symbols}
        return tuple(
            quote for quote in self.get_market_snapshot().quotes if quote.symbol in requested
        )

    def get_index_snapshot(self, symbols) -> tuple[RealTimeQuote, ...]:
        return self.get_quotes(symbols)

    def get_market_status(self) -> str:
        return "OPEN" if self._bars else "CLOSED"
