"""Independent in-memory store for the newest intraday monitor observations."""

from __future__ import annotations

from collections.abc import Iterable

from a_share_quant.contracts.realtime_overlay import RealtimeOverlay
from a_share_quant.data.normalization import normalize_symbol


class RealtimeOverlayStore:
    """Accept newer overlays only; never reference daily model scores."""

    def __init__(self) -> None:
        self._overlays: dict[str, RealtimeOverlay] = {}

    def put_overlays(self, overlays: Iterable[RealtimeOverlay]) -> None:
        for overlay in overlays:
            previous = self._overlays.get(overlay.symbol)
            if previous is None or overlay.timestamp >= previous.timestamp:
                self._overlays[overlay.symbol] = overlay

    def overlays(self, symbols: Iterable[str] | None = None) -> tuple[RealtimeOverlay, ...]:
        if symbols is None:
            selected = self._overlays.values()
        else:
            requested = {normalize_symbol(symbol) for symbol in symbols}
            selected = (
                overlay
                for symbol, overlay in self._overlays.items()
                if symbol in requested
            )
        return tuple(sorted(selected, key=lambda item: item.symbol))


__all__ = ["RealtimeOverlayStore"]
