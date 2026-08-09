"""Real-time provider boundary and local monitoring helpers."""

from a_share_quant.data.realtime.base import RealTimeDataProvider
from a_share_quant.data.realtime.diagnostics import (
    NetworkDiagnostics,
    collect_network_diagnostics,
)
from a_share_quant.data.realtime.registry import FailoverRealTimeProvider, ProviderRegistry

__all__ = [
    "FailoverRealTimeProvider",
    "NetworkDiagnostics",
    "ProviderRegistry",
    "RealTimeDataProvider",
    "collect_network_diagnostics",
]
