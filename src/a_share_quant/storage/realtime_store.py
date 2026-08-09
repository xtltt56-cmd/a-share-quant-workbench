"""Separate in-memory real-time cache and explicit EOD finalization boundary."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime

from a_share_quant.contracts.realtime import MinuteBar, RealTimeQuote
from a_share_quant.data.normalization import normalize_symbol


@dataclass(frozen=True)
class EODFinalizationReceipt:
    trade_date: date
    finalized_count: int
    discarded_count: int
    status: str


class RealTimeStore:
    """Keep intraday provisional state out of the PIT historical store."""

    def __init__(self) -> None:
        self._quotes: dict[str, RealTimeQuote] = {}
        self._provisional: dict[tuple[str, str, datetime], MinuteBar] = {}
        self._historical: dict[tuple[str, str, datetime], MinuteBar] = {}
        self._finalized_dates: set[date] = set()

    def put_quotes(self, quotes: Iterable[RealTimeQuote]) -> None:
        for quote in quotes:
            previous = self._quotes.get(quote.symbol)
            if previous is None or quote.timestamp_received >= previous.timestamp_received:
                self._quotes[quote.symbol] = quote

    def quotes(self, symbols: Iterable[str] | None = None) -> tuple[RealTimeQuote, ...]:
        if symbols is None:
            values = self._quotes.values()
        else:
            requested = {normalize_symbol(symbol) for symbol in symbols}
            values = (quote for symbol, quote in self._quotes.items() if symbol in requested)
        return tuple(sorted(values, key=lambda item: item.symbol))

    def put_minute_bars(self, bars: Iterable[MinuteBar]) -> None:
        for bar in bars:
            key = (bar.symbol, bar.frequency, bar.timestamp)
            self._provisional[key] = bar

    def provisional_bars(self) -> tuple[MinuteBar, ...]:
        return tuple(
            self._provisional[key]
            for key in sorted(self._provisional, key=lambda item: (item[2], item[0], item[1]))
        )

    def historical_bars(self) -> tuple[MinuteBar, ...]:
        return tuple(
            self._historical[key]
            for key in sorted(self._historical, key=lambda item: (item[2], item[0], item[1]))
        )

    def finalize_eod(
        self,
        *,
        trade_date: date | str,
        reconciler: Callable[[tuple[MinuteBar, ...]], Iterable[MinuteBar]],
    ) -> EODFinalizationReceipt:
        parsed_date = _parse_date(trade_date)
        if parsed_date in self._finalized_dates:
            return EODFinalizationReceipt(parsed_date, 0, 0, "ALREADY_FINALIZED")
        candidates = tuple(
            bar
            for bar in self._provisional.values()
            if bar.timestamp.date() == parsed_date and bar.is_final
        )
        reconciled = tuple(reconciler(candidates))
        valid: list[MinuteBar] = []
        for bar in reconciled:
            if bar.timestamp.date() != parsed_date or not bar.is_final:
                continue
            key = (bar.symbol, bar.frequency, bar.timestamp)
            self._historical[key] = bar
            valid.append(bar)
        discarded = len(candidates) - len(valid)
        for key in tuple(self._provisional):
            if self._provisional[key].timestamp.date() == parsed_date:
                del self._provisional[key]
        self._finalized_dates.add(parsed_date)
        return EODFinalizationReceipt(parsed_date, len(valid), discarded, "FINALIZED")


def _parse_date(value: date | str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value)).date()
