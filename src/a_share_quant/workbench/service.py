"""Safe service facade for the local real-time monitoring dashboard."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from a_share_quant.analysis.breadth import calculate_market_breadth
from a_share_quant.contracts.realtime import DataQualityStatus, RealTimeQuote
from a_share_quant.data.realtime.base import RealTimeDataProvider
from a_share_quant.data.realtime.registry import (
    FailoverRealTimeProvider,
    ProviderRegistry,
    build_default_registry,
)
from a_share_quant.runtime.scheduler import RealTimeScheduler, SchedulerTick, SessionResolver
from a_share_quant.signals.realtime import RealtimeMonitorSignal, TriggerEngine
from a_share_quant.storage.realtime_store import RealTimeStore


@dataclass
class WorkbenchState:
    updated_at: str | None = None
    session: str = "UNKNOWN"
    active_provider: str | None = None
    provider_capabilities: list[dict[str, Any]] = field(default_factory=list)
    data_quality: str = DataQualityStatus.FAILED.value
    stale: bool = True
    last_error: str | None = None
    quotes: list[dict[str, Any]] = field(default_factory=list)
    breadth: dict[str, Any] = field(default_factory=dict)
    intraday_monitor: list[dict[str, Any]] = field(default_factory=list)
    official_daily_candidates: list[dict[str, Any]] = field(default_factory=list)
    paper_only: bool = True
    live_trading_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkbenchService:
    """Own provider state and expose sanitized data for a local dashboard.

    ``allow_network`` is explicit.  Offline mode still exposes health and
    cached state but never invokes an external provider endpoint.
    """

    def __init__(
        self,
        *,
        provider: RealTimeDataProvider | FailoverRealTimeProvider | None = None,
        registry: ProviderRegistry | None = None,
        store: RealTimeStore | None = None,
        symbols: Sequence[str] = (),
        allow_network: bool = False,
        resolver: SessionResolver | None = None,
        clock: Callable[[], datetime] | None = None,
        poll_interval_seconds: float = 15.0,
    ) -> None:
        self.store = store or RealTimeStore()
        self.registry = registry
        self.allow_network = allow_network
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.state = WorkbenchState()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._poll_interval_seconds = max(1.0, float(poll_interval_seconds))

        if provider is None:
            self.registry = registry or build_default_registry()
            capabilities = self.registry.discover()
            self._provider = (
                self.registry.build_failover() if self.registry.providers else None
            )
            self.state.active_provider = self.registry.active_provider_name
            self.state.provider_capabilities = [
                {
                    "provider": item.provider,
                    "authenticated": item.authenticated,
                    "market": item.market,
                    "frequency": item.frequency,
                    "permissions": list(item.permissions),
                    "status": item.status,
                    "message": item.message,
                }
                for item in capabilities
            ]
        else:
            self._provider = provider
            self.state.provider_capabilities = [
                {
                    "provider": getattr(provider, "name", "injected"),
                    "authenticated": False,
                    "market": "A",
                    "frequency": "snapshot,1m",
                    "permissions": [],
                    "status": "INJECTED",
                    "message": "offline-testable provider boundary",
                }
            ]
        self.scheduler = (
            RealTimeScheduler(
                provider=self._provider,
                store=self.store,
                resolver=resolver,
                symbols=tuple(symbols),
                clock=self.clock,
            )
            if self._provider is not None
            else None
        )

    @property
    def provider(self) -> RealTimeDataProvider | FailoverRealTimeProvider | None:
        return self._provider

    def refresh(self) -> WorkbenchState:
        now = self.clock()
        if not self.allow_network:
            self.state.updated_at = now.isoformat()
            self.state.last_error = "OFFLINE_MODE"
            self.state.data_quality = DataQualityStatus.FAILED.value
            self.state.stale = True
            return self.state
        if self.scheduler is None:
            self.state.updated_at = now.isoformat()
            self.state.last_error = "NO_PROVIDER"
            self.state.data_quality = DataQualityStatus.FAILED.value
            self.state.stale = True
            return self.state

        tick = self.scheduler.run_once()
        self._apply_tick(tick)
        return self.state

    def start_background(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="quant-workbench-refresh",
            daemon=True,
        )
        self._thread.start()

    def stop_background(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def health(self) -> dict[str, Any]:
        return {
            "status": (
                "OFFLINE"
                if not self.allow_network
                else "OK" if self._provider is not None else "DATA_UNAVAILABLE"
            ),
            "active_provider": self.state.active_provider,
            "provider_capabilities": self.state.provider_capabilities,
            "allow_network": self.allow_network,
            "paper_only": True,
            "live_trading_enabled": False,
        }

    def snapshot(self) -> dict[str, Any]:
        return self.state.to_dict()

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            self.refresh()
            self._stop_event.wait(self._poll_interval_seconds)

    def _apply_tick(self, tick: SchedulerTick) -> None:
        self.state.updated_at = tick.timestamp.isoformat()
        self.state.session = tick.session.value
        self.state.active_provider = getattr(
            self._provider,
            "active_provider_name",
            getattr(self._provider, "name", None),
        )
        self.state.last_error = tick.error or (tick.skip_reason or None)
        if tick.quality_status is not None:
            self.state.data_quality = tick.quality_status.value
            self.state.stale = tick.quality_status in {
                DataQualityStatus.STALE,
                DataQualityStatus.FAILED,
            }
        if tick.updated:
            quotes = self.store.quotes()
            self.state.quotes = [
                _quote_payload(quote, now=tick.timestamp) for quote in quotes[:100]
            ]
            breadth = calculate_market_breadth(quotes)
            self.state.breadth = _breadth_payload(breadth)
            self.state.intraday_monitor = [
                _monitor_payload(
                    _monitor_signal(quote, now=tick.timestamp),
                    quote=quote,
                    now=tick.timestamp,
                )
                for quote in quotes[:100]
            ]
            self.state.official_daily_candidates = []


def _quote_payload(quote: RealTimeQuote, *, now: datetime) -> dict[str, Any]:
    payload = quote.to_dict()
    payload["data_age_seconds"] = round(quote.data_age_seconds(now=now), 3)
    return payload


def _breadth_payload(breadth: Any) -> dict[str, Any]:
    payload = asdict(breadth)
    payload["temperature"] = breadth.temperature.value
    return payload


def _monitor_signal(quote: RealTimeQuote, *, now: datetime) -> RealtimeMonitorSignal:
    return TriggerEngine().evaluate(
        {
            "symbol": quote.symbol,
            "timestamp_exchange": quote.timestamp_exchange,
            "current_price": quote.last,
            "change_pct": quote.change_pct,
            "data_quality": quote.quality_flag.value,
            "is_stale": quote.is_stale,
        },
        now=now,
    )


def _monitor_payload(
    signal: RealtimeMonitorSignal,
    *,
    quote: RealTimeQuote,
    now: datetime,
) -> dict[str, Any]:
    payload = asdict(signal)
    payload["state"] = signal.state.value
    payload["evaluated_at"] = signal.evaluated_at.isoformat()
    payload["change_pct"] = quote.change_pct
    payload["data_age_seconds"] = round(quote.data_age_seconds(now=now), 3)
    payload["source"] = quote.source
    return payload
