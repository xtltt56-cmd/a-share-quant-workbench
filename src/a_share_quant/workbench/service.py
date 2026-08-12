"""Safe service facade for the local real-time monitoring dashboard."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from a_share_quant.advisory.price_contracts import PriceGuidancePlan
from a_share_quant.advisory.price_overlay import PriceGuidanceOverlay
from a_share_quant.analysis.breadth import calculate_market_breadth
from a_share_quant.contracts.realtime import DataQualityStatus, RealTimeQuote
from a_share_quant.contracts.realtime_overlay import RealtimeOverlay
from a_share_quant.data.realtime.base import RealTimeDataProvider
from a_share_quant.data.realtime.cache import CachedQuoteSnapshot, RealtimeQuoteCache
from a_share_quant.data.realtime.registry import (
    FailoverRealTimeProvider,
    ProviderRegistry,
    build_default_registry,
)
from a_share_quant.runtime.realtime_telemetry import LiveDataQualityGate, ProviderTelemetry
from a_share_quant.runtime.scheduler import RealTimeScheduler, SchedulerTick, SessionResolver
from a_share_quant.signals.realtime import (
    OfficialModelSignal,
    RealtimeMonitorSignal,
    RealtimeSignalState,
    TriggerEngine,
)
from a_share_quant.storage.official_signal_store import OfficialSignalStore
from a_share_quant.storage.price_guidance_store import PriceGuidanceStore
from a_share_quant.storage.realtime_overlay_store import RealtimeOverlayStore
from a_share_quant.storage.realtime_store import RealTimeStore


@dataclass
class WorkbenchState:
    updated_at: str | None = None
    session: str = "UNKNOWN"
    active_provider: str | None = None
    active_source: str = "No Active Source"
    source_class: str = "UNAVAILABLE"
    evidence_mode: str = "OFFLINE"
    provider_capabilities: list[dict[str, Any]] = field(default_factory=list)
    data_quality: str = DataQualityStatus.FAILED.value
    stale: bool = True
    schema_pass: bool = False
    continuous_updates: bool = False
    distinct_update_count: int = 0
    circuit_breaker_state: str = "UNKNOWN"
    last_update: str | None = None
    data_age_seconds: float | None = None
    latency_ms: float | None = None
    fallback_count: int = 0
    provider_telemetry: dict[str, Any] = field(default_factory=dict)
    last_error: str | None = None
    quotes: list[dict[str, Any]] = field(default_factory=list)
    breadth: dict[str, Any] = field(default_factory=dict)
    intraday_monitor: list[dict[str, Any]] = field(default_factory=list)
    official_daily_candidates: list[dict[str, Any]] = field(default_factory=list)
    price_guidance_plans: list[dict[str, Any]] = field(default_factory=list)
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
        quote_cache: RealtimeQuoteCache | None = None,
        official_signal_store: OfficialSignalStore | None = None,
        price_guidance_store: PriceGuidanceStore | None = None,
        realtime_overlay_store: RealtimeOverlayStore | None = None,
        symbols: Sequence[str] = (),
        allow_network: bool = False,
        resolver: SessionResolver | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        poll_interval_seconds: float = 15.0,
        stale_after_seconds: float = 60.0,
        minimum_distinct_updates: int = 2,
        telemetry_latency_samples: int = 256,
    ) -> None:
        self.store = store or RealTimeStore()
        self.quote_cache = quote_cache
        self.official_signal_store = official_signal_store or OfficialSignalStore()
        self.price_guidance_store = price_guidance_store
        self.price_guidance_overlay = PriceGuidanceOverlay()
        self.realtime_overlay_store = realtime_overlay_store or RealtimeOverlayStore()
        self.registry = registry
        self.allow_network = allow_network
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic_clock = monotonic_clock or time.monotonic
        self.live_quality_gate = LiveDataQualityGate(
            stale_after_seconds=stale_after_seconds,
            minimum_distinct_updates=minimum_distinct_updates,
        )
        self.telemetry = ProviderTelemetry(max_latency_samples=telemetry_latency_samples)
        self._last_switch_event_count = 0
        self.state = WorkbenchState()
        self._cached_snapshot: CachedQuoteSnapshot | None = None
        self._cache_error: str | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._poll_interval_seconds = max(1.0, float(poll_interval_seconds))

        if self.quote_cache is not None:
            try:
                self._cached_snapshot = self.quote_cache.load(now=self.clock())
            except ValueError:
                self._cache_error = "CACHE_REJECTED"
            if self._cached_snapshot is not None:
                self.store.put_quotes(self._cached_snapshot.quotes)

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
        initial_provider = getattr(
            self._provider,
            "active_provider_name",
            getattr(self._provider, "name", None),
        )
        self._set_source_metadata(
            getattr(self._provider, "active_source_name", initial_provider)
        )
        # Load the last validated daily candidates immediately.  The dashboard
        # must not appear empty merely because the live provider is offline or
        # the first refresh has not completed yet.  These rows are explicitly
        # marked stale/monitoring-only until a fresh quote is observed.
        self._apply_stored_signal_state(now=self.clock())
        if self._cached_snapshot is not None:
            self._apply_cached_state(now=self.clock(), error=self._cache_error or "CACHED_DATA")
        self.scheduler = (
            RealTimeScheduler(
                provider=self._provider,
                store=self.store,
                resolver=resolver,
                symbols=tuple(symbols),
                clock=self.clock,
                stale_after_seconds=stale_after_seconds,
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
            self._apply_unavailable_state(now, error="OFFLINE_MODE", evidence_mode="OFFLINE")
            return self.state
        if self.scheduler is None:
            self._apply_unavailable_state(now, error="NO_PROVIDER", evidence_mode="OFFLINE")
            return self.state

        started = self._monotonic_clock()
        tick = self.scheduler.run_once()
        latency_ms = max(0.0, (self._monotonic_clock() - started) * 1000)
        self._apply_tick(tick, latency_ms=latency_ms)
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
            "active_source": self.state.active_source,
            "source_class": self.state.source_class,
            "evidence_mode": self.state.evidence_mode,
            "provider_capabilities": self.state.provider_capabilities,
            "allow_network": self.allow_network,
            "provider_telemetry": self.telemetry.snapshot().to_dict(),
            "paper_only": True,
            "live_trading_enabled": False,
        }

    def snapshot(self) -> dict[str, Any]:
        return self.state.to_dict()

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            self.refresh()
            self._stop_event.wait(self._poll_interval_seconds)

    def _apply_tick(self, tick: SchedulerTick, *, latency_ms: float) -> None:
        self.state.updated_at = tick.timestamp.isoformat()
        self.state.session = tick.session.value
        active_provider = getattr(
            self._provider,
            "active_provider_name",
            getattr(self._provider, "name", None),
        )
        self.state.active_provider = active_provider
        self._set_source_metadata(
            getattr(self._provider, "active_source_name", active_provider)
        )
        self.state.last_error = tick.error or (tick.skip_reason or None)
        self._record_new_fallbacks(observed_at=tick.timestamp)

        if not tick.updated:
            if self._cached_snapshot is not None:
                self._apply_cached_state(
                    now=tick.timestamp,
                    error=tick.error or tick.skip_reason or self._cache_error or "CACHED_DATA",
                )
                self.state.circuit_breaker_state = self.scheduler.circuit_breaker.state
                self.state.provider_telemetry = self.telemetry.snapshot().to_dict()
                return
            if tick.requested:
                self.telemetry.record_failure(observed_at=tick.timestamp)
            report = self.live_quality_gate.evaluate(
                (),
                provider_connected=False,
                circuit_breaker_open=True,
                now=tick.timestamp,
            )
            self._apply_quality_report(report, replay=False)
            self.state.evidence_mode = (
                tick.skip_reason
                if not tick.requested and tick.skip_reason
                else "OFFLINE"
            )
            self.state.circuit_breaker_state = self.scheduler.circuit_breaker.state
            self.state.provider_telemetry = self.telemetry.snapshot().to_dict()
            return

        quotes = self.store.quotes()
        fresh_quotes = tuple(quote for quote in quotes if not quote.is_stale)
        if self.quote_cache is not None and fresh_quotes:
            try:
                self.quote_cache.save(fresh_quotes, saved_at=tick.timestamp)
                self._cached_snapshot = CachedQuoteSnapshot(
                    quotes=tuple(
                        replace(
                            quote,
                            quality_flag=DataQualityStatus.STALE,
                            is_stale=True,
                        )
                        for quote in fresh_quotes
                    ),
                    saved_at=tick.timestamp,
                )
                self._cache_error = None
            except ValueError:
                self._cache_error = "CACHE_WRITE_FAILED"
        self.telemetry.record_success(
            provider=active_provider or "unknown",
            quotes=quotes,
            latency_ms=latency_ms,
            observed_at=tick.timestamp,
        )
        report = self.live_quality_gate.evaluate(
            quotes,
            provider_connected=True,
            circuit_breaker_open=self.scheduler.circuit_breaker.state == "OPEN",
            expected_symbols=self.scheduler.symbols,
            now=tick.timestamp,
        )
        replay = _is_replay_provider(active_provider, quotes)
        effective_quality = _worst_quality(report.status, tick.quality_status)
        self._apply_quality_report(
            report,
            replay=replay,
            status=effective_quality,
        )
        self.state.circuit_breaker_state = self.scheduler.circuit_breaker.state
        # Replay fixtures are explicitly separated in the state evidence mode;
        # keep their deterministic monitor behavior while applying the live
        # quality gate to every real-market snapshot.
        monitor_quality = DataQualityStatus.GOOD if replay else effective_quality
        self._apply_quote_state(
            quotes,
            now=tick.timestamp,
            data_quality=monitor_quality,
        )
        self.state.last_update = _last_received_at(quotes)
        self.state.data_age_seconds = _maximum_data_age(quotes, now=tick.timestamp)
        telemetry = self.telemetry.snapshot()
        self.state.latency_ms = telemetry.last_latency_ms
        self.state.fallback_count = telemetry.fallback_count
        self.state.provider_telemetry = telemetry.to_dict()

    def _apply_unavailable_state(
        self,
        now: datetime,
        *,
        error: str,
        evidence_mode: str,
    ) -> None:
        if self._cached_snapshot is not None:
            self._apply_cached_state(now=now, error=error)
            return
        self.state.updated_at = now.isoformat()
        self.state.last_error = error
        self.state.evidence_mode = evidence_mode
        self.state.data_quality = DataQualityStatus.FAILED.value
        self.state.stale = True
        self.state.schema_pass = False
        self.state.continuous_updates = False
        self.state.distinct_update_count = 0
        self.state.last_update = None
        self.state.data_age_seconds = None
        self.state.latency_ms = None
        self.state.provider_telemetry = self.telemetry.snapshot().to_dict()

    def _apply_cached_state(self, *, now: datetime, error: str) -> None:
        """Show the last validated snapshot without upgrading its quality."""

        if self._cached_snapshot is None:
            return
        quotes = self._cached_snapshot.quotes
        self._apply_quote_state(quotes, now=now, data_quality=DataQualityStatus.STALE)
        self.state.updated_at = now.isoformat()
        self.state.last_error = error
        self.state.evidence_mode = "CACHED"
        self.state.data_quality = DataQualityStatus.STALE.value
        self.state.stale = True
        self.state.schema_pass = False
        self.state.continuous_updates = False
        self.state.distinct_update_count = 0
        self.state.last_update = _last_received_at(quotes)
        self.state.data_age_seconds = _maximum_data_age(quotes, now=now)
        self.state.latency_ms = None

    def _apply_quality_report(
        self,
        report: Any,
        *,
        replay: bool,
        status: DataQualityStatus | None = None,
    ) -> None:
        effective_status = status or report.status
        self.state.schema_pass = report.schema_pass
        self.state.continuous_updates = (
            False
            if replay or effective_status is not DataQualityStatus.GOOD
            else report.continuous_updates
        )
        self.state.distinct_update_count = 0 if replay else report.distinct_update_count
        self.state.evidence_mode = "REPLAY" if replay else "REAL_MARKET"
        self.state.data_quality = "REPLAY" if replay else effective_status.value
        self.state.stale = effective_status in {
            DataQualityStatus.STALE,
            DataQualityStatus.FAILED,
        }

    def _apply_quote_state(
        self,
        quotes: tuple[RealTimeQuote, ...],
        *,
        now: datetime,
        data_quality: DataQualityStatus,
    ) -> None:
        official_signals = {
            signal.symbol: signal for signal in self.official_signal_store.latest()
        }
        guidance_plans = self._guidance_plans_by_symbol()
        ordered_quotes = tuple(
            sorted(
                quotes,
                key=lambda quote: (
                    0 if quote.symbol in official_signals else 1,
                    quote.symbol,
                ),
            )
        )
        self.state.quotes = [_quote_payload(quote, now=now) for quote in ordered_quotes[:100]]
        breadth = calculate_market_breadth(ordered_quotes)
        self.state.breadth = _breadth_payload(breadth)
        monitor_rows: list[dict[str, Any]] = []
        overlays: list[RealtimeOverlay] = []
        for quote in ordered_quotes[:100]:
            official = official_signals.get(quote.symbol)
            signal = _monitor_signal(
                quote,
                official=official,
                data_quality=data_quality,
                now=now,
            )
            overlay = _overlay_from_quote(
                quote,
                signal=signal,
                data_quality=data_quality,
            )
            monitor_rows.append(_monitor_payload(signal, quote=quote, overlay=overlay, now=now))
            monitor_rows[-1]["price_guidance"] = self._quote_guidance_payload(
                guidance_plans.get(quote.symbol), quote=quote, data_quality=data_quality, now=now
            )
            overlays.append(overlay)
        self.realtime_overlay_store.put_overlays(overlays)
        self.state.intraday_monitor = monitor_rows
        self.state.official_daily_candidates = [
            _official_signal_payload(
                signal,
                now=now,
                price_guidance=guidance_plans.get(signal.symbol),
            )
            for signal in self.official_signal_store.latest()
        ]
        self.state.price_guidance_plans = [
            item.to_dict() for item in self._guidance_plans()
        ]

    def _apply_stored_signal_state(self, *, now: datetime) -> None:
        """Expose durable candidates while live quotes are unavailable."""

        signals = self.official_signal_store.latest()
        self.state.official_daily_candidates = [
            _official_signal_payload(
                signal, now=now, price_guidance=self._guidance_plans_by_symbol().get(signal.symbol)
            )
            for signal in signals
        ]
        self.state.intraday_monitor = [
            {
                **_stale_candidate_payload(signal, now=now),
                "price_guidance": self._quote_guidance_payload(
                    self._guidance_plans_by_symbol().get(signal.symbol),
                    quote=None,
                    data_quality=DataQualityStatus.FAILED,
                    now=now,
                ),
            }
            for signal in signals
        ]
        self.state.price_guidance_plans = [item.to_dict() for item in self._guidance_plans()]

    def _guidance_plans(self) -> tuple[PriceGuidancePlan, ...]:
        if self.price_guidance_store is None:
            return ()
        try:
            return self.price_guidance_store.plans()
        except ValueError:
            return ()

    def _guidance_plans_by_symbol(self) -> dict[str, PriceGuidancePlan]:
        return {
            item.symbol: item
            for item in self._guidance_plans()
            if item.plan_type.value == "DAILY_CANDIDATE"
        }

    def _quote_guidance_payload(
        self,
        plan: PriceGuidancePlan | None,
        *,
        quote: RealTimeQuote | None,
        data_quality: DataQualityStatus,
        now: datetime,
    ) -> dict[str, Any]:
        if plan is None:
            return {
                "state": "NO_RELIABLE_GUIDANCE",
                "manual_execution_required": True,
                "notice_zh": "暂无可靠指导价；当前标的没有冻结价格计划。",
            }
        if quote is None:
            return {
                **plan.to_dict(),
                "state": "NO_RELIABLE_GUIDANCE",
                "current_price": None,
                "data_quality": data_quality.value,
                "manual_execution_required": True,
                "notice_zh": "暂无可靠指导价；尚无经过核验的盘中行情。",
            }
        try:
            result = self.price_guidance_overlay.evaluate(
                plan,
                {
                    "current_price": quote.last,
                    "data_quality": data_quality.value,
                    "quote_timestamp": quote.timestamp_exchange,
                    "observed_at": quote.timestamp_received,
                },
                now=now,
            )
            return {
                **plan.to_dict(),
                **result.to_dict(),
                "notice_zh": (
                    "研究参考区间，尚未通过正式模型晋升门槛。"
                    if plan.guidance_level.value == "RESEARCH_REFERENCE"
                    else "仅供人工复核，系统不会提交委托。"
                ),
            }
        except ValueError:
            return {
                **plan.to_dict(),
                "state": "NO_RELIABLE_GUIDANCE",
                "current_price": None,
                "data_quality": data_quality.value,
                "manual_execution_required": True,
                "notice_zh": "暂无可靠指导价；计划已过期或行情时间无效。",
            }

    def _record_new_fallbacks(self, *, observed_at: datetime) -> None:
        switch_events = tuple(getattr(self._provider, "switch_events", ()))
        switch_count = len(switch_events)
        for _ in range(max(0, switch_count - self._last_switch_event_count)):
            self.telemetry.record_fallback(observed_at=observed_at)
        self._last_switch_event_count = switch_count

    def _set_source_metadata(self, provider_name: str | None) -> None:
        source, source_class = _source_metadata(provider_name)
        self.state.active_source = source
        self.state.source_class = source_class


def _quote_payload(quote: RealTimeQuote, *, now: datetime) -> dict[str, Any]:
    payload = quote.to_dict()
    payload["data_age_seconds"] = round(quote.data_age_seconds(now=now), 3)
    return payload


def _breadth_payload(breadth: Any) -> dict[str, Any]:
    payload = asdict(breadth)
    payload["temperature"] = breadth.temperature.value
    return payload


def _monitor_signal(
    quote: RealTimeQuote,
    *,
    official: OfficialModelSignal | None = None,
    data_quality: DataQualityStatus = DataQualityStatus.GOOD,
    now: datetime,
) -> RealtimeMonitorSignal:
    quality_blocks_ready = data_quality is not DataQualityStatus.GOOD
    return TriggerEngine().evaluate(
        {
            "symbol": quote.symbol,
            "timestamp_exchange": quote.timestamp_exchange,
            "current_price": quote.last,
            "change_pct": quote.change_pct,
            "data_quality": (
                DataQualityStatus.STALE.value
                if quality_blocks_ready
                else quote.quality_flag.value
            ),
            "is_stale": quote.is_stale or quality_blocks_ready,
            "normalized_score": official.normalized_score if official is not None else None,
            "official_model_signal": official is not None,
        },
        now=now,
    )


def _monitor_payload(
    signal: RealtimeMonitorSignal,
    *,
    quote: RealTimeQuote,
    overlay: RealtimeOverlay,
    now: datetime,
) -> dict[str, Any]:
    payload = overlay.to_dict()
    payload["state"] = signal.state.value
    payload["evaluated_at"] = signal.evaluated_at.isoformat()
    payload["score"] = signal.score
    payload["current_price"] = signal.current_price
    payload["change_pct"] = quote.change_pct
    payload["data_age_seconds"] = round(quote.data_age_seconds(now=now), 3)
    payload["source"] = quote.source
    payload["quote_timestamp"] = quote.timestamp_exchange.isoformat()
    payload["quote_received_at"] = quote.timestamp_received.isoformat()
    payload["official_model_signal"] = signal.official_model_signal
    payload["monitoring_only"] = True
    return payload


def _overlay_from_quote(
    quote: RealTimeQuote,
    *,
    signal: RealtimeMonitorSignal,
    data_quality: DataQualityStatus,
) -> RealtimeOverlay:
    previous_close = quote.previous_close
    intraday_return = (
        quote.last / previous_close - 1
        if quote.last is not None and previous_close is not None and previous_close > 0
        else None
    )
    vwap = (
        quote.amount / quote.volume
        if quote.amount is not None and quote.volume is not None and quote.volume > 0
        else None
    )
    risk_state = (
        "RISK"
        if signal.state is RealtimeSignalState.RISK
        else "STALE"
        if signal.state is RealtimeSignalState.STALE_DATA
        else "NORMAL"
    )
    return RealtimeOverlay(
        symbol=quote.symbol,
        timestamp=quote.timestamp_exchange,
        last=quote.last or 0.0,
        vwap=vwap,
        volume_ratio=None,
        intraday_return=intraday_return,
        market_relative_strength=None,
        trigger_state=signal.state.value,
        risk_state=risk_state,
        data_quality=data_quality,
    )


def _official_signal_payload(
    signal: OfficialModelSignal,
    *,
    now: datetime | None = None,
    price_guidance: PriceGuidancePlan | None = None,
) -> dict[str, Any]:
    reference = now or datetime.now(timezone.utc)
    age_days = max(0, (reference.date() - signal.signal_date).days)
    return {
        "signal_date": signal.signal_date.isoformat(),
        "symbol": signal.symbol,
        "name": signal.name,
        "normalized_score": signal.normalized_score,
        "strategy_version": signal.strategy_version,
        "model_version": signal.model_version,
        "feature_version": signal.feature_version,
        "data_mode": signal.data_mode,
        "source": signal.source,
        "data_cutoff": signal.data_cutoff.isoformat() if signal.data_cutoff else None,
        "generated_at": signal.generated_at.isoformat(),
        "rank": signal.rank,
        "reasons": list(signal.reasons),
        "reference_price": signal.reference_price,
        "average_amount": signal.average_amount,
        "invalidation_price": signal.invalidation_price,
        "signal_age_days": age_days,
        "signal_stale": age_days > 3,
        "frequency": signal.frequency.value,
        "monitoring_only": True,
        "price_guidance": (
            price_guidance.to_dict()
            if price_guidance is not None
            else {
                "state": "NO_RELIABLE_GUIDANCE",
                "manual_execution_required": True,
                "notice_zh": "暂无可靠指导价；请先生成冻结价格计划。",
            }
        ),
    }


def _stale_candidate_payload(signal: OfficialModelSignal, *, now: datetime) -> dict[str, Any]:
    """Build a visible but non-actionable monitor row without inventing a quote."""

    return {
        "symbol": signal.symbol,
        "state": RealtimeSignalState.STALE_DATA.value,
        "score": signal.normalized_score,
        "current_price": None,
        "change_pct": None,
        "data_quality": DataQualityStatus.FAILED.value,
        "data_age_seconds": None,
        "source": signal.source,
        "quote_timestamp": None,
        "quote_received_at": None,
        "official_model_signal": True,
        "monitoring_only": True,
        "evaluated_at": now.isoformat(),
        "reasons": ["等待经过核验的实时行情"],
        "risks": ["无实时行情，禁止生成可执行买卖结论"],
        "trigger_state": RealtimeSignalState.STALE_DATA.value,
        "risk_state": "STALE",
    }


def _source_metadata(provider_name: str | None) -> tuple[str, str]:
    normalized = (provider_name or "").casefold()
    if normalized == "akshare":
        return "AKShare / Eastmoney", "PUBLIC DATA SOURCE"
    if normalized.startswith("akshare"):
        return provider_name or "AKShare / Eastmoney", "PUBLIC DATA SOURCE"
    if normalized == "rqdata":
        return "RQData", "PROFESSIONAL DATA SOURCE"
    if normalized == "tushare":
        return "Tushare", "PROFESSIONAL DATA SOURCE"
    if normalized == "replay":
        return "Replay / Test Data", "REPLAY / NON-MARKET"
    if provider_name:
        return provider_name, "UNKNOWN DATA SOURCE"
    return "No Active Source", "UNAVAILABLE"


def _is_replay_provider(
    provider_name: str | None,
    quotes: tuple[RealTimeQuote, ...],
) -> bool:
    return (provider_name or "").casefold() == "replay" or (
        bool(quotes) and all(quote.source.casefold() == "replay" for quote in quotes)
    )


def _last_received_at(quotes: tuple[RealTimeQuote, ...]) -> str | None:
    if not quotes:
        return None
    return max(quote.timestamp_received for quote in quotes).isoformat()


def _maximum_data_age(
    quotes: tuple[RealTimeQuote, ...],
    *,
    now: datetime,
) -> float | None:
    if not quotes:
        return None
    return round(max(quote.data_age_seconds(now=now) for quote in quotes), 3)


def _worst_quality(
    first: DataQualityStatus,
    second: DataQualityStatus | None,
) -> DataQualityStatus:
    """Never let a service-level check upgrade a degraded scheduler tick."""

    if second is None:
        return first
    order = {
        DataQualityStatus.GOOD: 0,
        DataQualityStatus.DEGRADED: 1,
        DataQualityStatus.STALE: 2,
        DataQualityStatus.FAILED: 3,
    }
    return first if order[first] >= order[second] else second
