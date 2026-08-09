"""Provider-neutral intraday facts kept separate from daily model signals."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from math import isfinite
from typing import Any

from a_share_quant.contracts.realtime import DataQualityStatus
from a_share_quant.data.normalization import normalize_symbol


@dataclass(frozen=True)
class RealtimeOverlay:
    """A read-only intraday observation for an official daily candidate.

    The object intentionally has no model score, order, broker, or execution
    fields.  Its only purpose is to let the local monitor display current
    market facts alongside an independently stored daily signal.
    """

    symbol: str
    timestamp: datetime
    last: float
    vwap: float | None
    volume_ratio: float | None
    intraday_return: float | None
    market_relative_strength: float | None
    trigger_state: str
    risk_state: str
    data_quality: DataQualityStatus | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "data_quality", DataQualityStatus.parse(self.data_quality))
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        _number(self.last, field="last", positive=True)
        _number(self.vwap, field="vwap", positive=True)
        _number(self.volume_ratio, field="volume_ratio")
        _number(self.intraday_return, field="intraday_return")
        _number(self.market_relative_strength, field="market_relative_strength")
        if not self.trigger_state:
            raise ValueError("trigger_state is required")
        if not self.risk_state:
            raise ValueError("risk_state is required")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp.isoformat()
        payload["data_quality"] = self.data_quality.value
        return payload


def _number(value: float | None, *, field: str, positive: bool = False) -> None:
    if value is None:
        return
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError(f"{field} must be finite")
    if positive and numeric <= 0:
        raise ValueError(f"{field} must be positive")


__all__ = ["RealtimeOverlay"]
