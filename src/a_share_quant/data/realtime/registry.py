"""Capability discovery and explicit provider failover."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from a_share_quant.contracts.realtime import (
    ProviderCapability,
    ProviderHealth,
    ProviderMetadata,
    ProviderSwitchEvent,
)
from a_share_quant.data.realtime.base import ProviderRequestError, RealTimeDataProvider

ProviderFactory = Callable[[], RealTimeDataProvider]


class ProviderRegistry:
    def __init__(self, providers: Sequence[tuple[str, ProviderFactory]]) -> None:
        self._factories = tuple(providers)
        self._providers: dict[str, RealTimeDataProvider] = {}
        self._health: dict[str, ProviderHealth] = {}
        self._capabilities: list[ProviderCapability] = []
        self.active_provider_name: str | None = None

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self._factories)

    def discover(self) -> list[ProviderCapability]:
        self._providers.clear()
        self._health.clear()
        self._capabilities = []
        self.active_provider_name = None
        for name, factory in self._factories:
            try:
                provider = factory()
                health = provider.health_check()
                metadata = provider.metadata()
                self._health[name] = health
                if health.connected:
                    self._providers[name] = provider
                capability = ProviderCapability(
                    provider=name,
                    authenticated=metadata.authenticated,
                    market=metadata.market,
                    frequency=",".join(metadata.frequencies),
                    latency_ms=health.latency_ms,
                    last_update=health.last_update,
                    permissions=metadata.permissions or health.permissions,
                    status=health.status,
                    message=health.message,
                )
                if health.connected and self.active_provider_name is None:
                    self.active_provider_name = name
            except Exception:
                capability = ProviderCapability(
                    provider=name,
                    authenticated=False,
                    market="A",
                    frequency="",
                    latency_ms=None,
                    last_update=None,
                    permissions=(),
                    status="UNAVAILABLE",
                    message="provider discovery failed",
                )
            self._capabilities.append(capability)
        return list(self._capabilities)

    def health_report(self) -> list[ProviderHealth]:
        return list(self._health.values())

    @property
    def providers(self) -> tuple[RealTimeDataProvider, ...]:
        return tuple(self._providers.values())

    def build_failover(self) -> FailoverRealTimeProvider:
        return FailoverRealTimeProvider(self.providers)


def build_default_registry(
    *,
    provider_priority: Sequence[str] = ("rqdata", "tushare", "akshare"),
) -> ProviderRegistry:
    """Build the configured priority chain without importing optional SDKs."""

    from a_share_quant.data.realtime.akshare import AKShareRealTimeProvider
    from a_share_quant.data.realtime.rqdata import RQDataRealTimeProvider
    from a_share_quant.data.realtime.tushare import TushareRealTimeProvider

    factories: dict[str, ProviderFactory] = {
        "rqdata": lambda: RQDataRealTimeProvider(
            username=os.getenv("RQDATA_USERNAME"),
            password=os.getenv("RQDATA_PASSWORD"),
            config_path=os.getenv("RQDATA_CONFIG_PATH") or None,
        ),
        "tushare": lambda: TushareRealTimeProvider(token=os.getenv("TUSHARE_TOKEN")),
        "akshare": lambda: AKShareRealTimeProvider(
            use_system_proxy=_environment_flag("A_SHARE_QUANT_USE_SYSTEM_PROXY", default=True),
            isolated_transport_authorized=_environment_flag(
                "A_SHARE_QUANT_ISOLATED_TRANSPORT_APPROVED",
                default=False,
            ),
        ),
    }
    ordered_names = tuple(dict.fromkeys(provider_priority))
    unknown = sorted(set(ordered_names).difference(factories))
    if unknown:
        raise ValueError(f"unknown real-time providers: {', '.join(unknown)}")
    return ProviderRegistry([(name, factories[name]) for name in ordered_names])


def _environment_flag(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


class FailoverRealTimeProvider:
    def __init__(self, providers: Sequence[RealTimeDataProvider]) -> None:
        if not providers:
            raise ValueError("at least one real-time provider is required")
        self._providers = tuple(providers)
        self._active_index = 0
        self._switch_events: list[ProviderSwitchEvent] = []

    @property
    def active_provider_name(self) -> str:
        return self._providers[self._active_index].name

    @property
    def switch_events(self) -> tuple[ProviderSwitchEvent, ...]:
        return tuple(self._switch_events)

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for index in range(self._active_index, len(self._providers)):
            provider = self._providers[index]
            try:
                result = getattr(provider, method)(*args, **kwargs)
                self._active_index = index
                return result
            except Exception as exc:
                last_error = exc
                if index + 1 < len(self._providers):
                    next_provider = self._providers[index + 1]
                    self._switch_events.append(
                        ProviderSwitchEvent(
                            source_from=provider.name,
                            source_to=next_provider.name,
                            reason=type(exc).__name__,
                            timestamp=datetime.now(timezone.utc),
                        )
                    )
        raise ProviderRequestError("all real-time providers failed") from last_error

    def get_market_snapshot(self):
        return self._call("get_market_snapshot")

    def get_quotes(self, symbols):
        return self._call("get_quotes", symbols)

    def get_minute_bars(self, symbols, frequency):
        return self._call("get_minute_bars", symbols, frequency)

    def get_index_snapshot(self, symbols):
        return self._call("get_index_snapshot", symbols)

    def get_market_status(self):
        return self._call("get_market_status")

    def health_check(self):
        return self._call("health_check")

    def metadata(self) -> ProviderMetadata:
        return self._call("metadata")
