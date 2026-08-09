"""Independent in-memory store for latest official daily model candidates."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from a_share_quant.signals.realtime import OfficialModelSignal


class OfficialSignalStore:
    """Store daily model outputs without importing intraday overlay concerns."""

    def __init__(self) -> None:
        self._signals: dict[tuple[date, str, str], OfficialModelSignal] = {}

    def put_signals(self, signals: Iterable[OfficialModelSignal]) -> None:
        for signal in signals:
            key = (signal.signal_date, signal.symbol, signal.strategy_version)
            self._signals[key] = signal

    def signals(self, *, signal_date: date | None = None) -> tuple[OfficialModelSignal, ...]:
        selected = tuple(
            signal
            for signal in self._signals.values()
            if signal_date is None or signal.signal_date == signal_date
        )
        return tuple(
            sorted(
                sorted(
                    selected,
                    key=lambda item: (
                        -float(item.normalized_score),
                        item.symbol,
                        item.strategy_version,
                    ),
                ),
                key=lambda item: item.signal_date,
                reverse=True,
            )
        )

    def latest(self) -> tuple[OfficialModelSignal, ...]:
        if not self._signals:
            return ()
        latest_date = max(signal.signal_date for signal in self._signals.values())
        return tuple(
            sorted(
                (
                    signal
                    for signal in self._signals.values()
                    if signal.signal_date == latest_date
                ),
                key=lambda item: (
                    -float(item.normalized_score),
                    item.symbol,
                    item.strategy_version,
                ),
            )
        )


__all__ = ["OfficialSignalStore"]
