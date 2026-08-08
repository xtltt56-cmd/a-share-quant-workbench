"""Mapping between project symbols and Qlib instrument identifiers."""

from __future__ import annotations

from collections.abc import Iterable

from a_share_quant.data.normalization import normalize_symbol


class QlibInstrumentAdapter:
    @staticmethod
    def normalize_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
        return tuple(sorted({normalize_symbol(symbol) for symbol in symbols}))
