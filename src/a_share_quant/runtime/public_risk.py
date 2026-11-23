"""Background refresh lifecycle for low-frequency public risk evidence."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from a_share_quant.data.public_intelligence.cninfo import CNInfoAnnouncementProvider
from a_share_quant.intelligence.contracts import PublicRiskSnapshot
from a_share_quant.storage.public_risk_store import PublicRiskStore

SnapshotPublisher = Callable[[PublicRiskSnapshot | None, str, str], None]
SymbolSupplier = Callable[[], Iterable[str]]
_CHINA_TZ = ZoneInfo("Asia/Shanghai")


class PublicRiskCoordinator:
    """Refresh CNINFO in its own thread so live quote polling stays responsive."""

    def __init__(
        self,
        *,
        store: PublicRiskStore,
        symbols: Iterable[str] | SymbolSupplier,
        publish: SnapshotPublisher,
        provider: CNInfoAnnouncementProvider | None = None,
        interval_seconds: float = (6 * 60 - 5) * 60,
        window_days: int = 30,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if interval_seconds < 60:
            raise ValueError("public risk refresh interval must be at least 60 seconds")
        self.store = store
        self._symbol_supplier = symbols if callable(symbols) else None
        self._static_symbols = (
            ()
            if self._symbol_supplier is not None
            else tuple(dict.fromkeys(str(symbol) for symbol in symbols))
        )
        self.publish = publish
        self.provider = provider or CNInfoAnnouncementProvider()
        self.interval_seconds = float(interval_seconds)
        self.window_days = window_days
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._retry_needed = False
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def symbols(self) -> tuple[str, ...]:
        supplied = (
            self._symbol_supplier()
            if self._symbol_supplier is not None
            else self._static_symbols
        )
        return tuple(dict.fromkeys(str(symbol) for symbol in supplied))

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="public-risk-refresh",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._thread is not None and not self._thread.is_alive():
            self._thread = None

    def request_refresh(self) -> None:
        """Wake the worker after the daily candidate universe changes."""

        self._wake.set()

    def refresh_once(self) -> PublicRiskSnapshot | None:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("public risk clock must be timezone-aware")
        self._retry_needed = False
        try:
            symbols = self.symbols
            if not symbols:
                return self.store.latest()
            snapshot = self.provider.fetch(
                symbols,
                window_end=now.astimezone(_CHINA_TZ).date(),
                window_days=self.window_days,
                now=now,
            )
            self.store.save(snapshot)
            snapshot = self.store.latest()
            assert snapshot is not None
        except (OSError, TimeoutError, RuntimeError, TypeError, ValueError):
            self._retry_needed = True
            cached = self.store.latest()
            notice = (
                "巨潮公告检查失败，继续保留上次成功快照；未成功检查的股票不得视为无风险。"
            )
            self.publish(cached, "UPDATE_FAILED", notice)
            return cached
        self._retry_needed = snapshot.status in {"PARTIAL", "UPDATE_FAILED"}
        self.publish(snapshot, snapshot.status, snapshot.notice_zh)
        return snapshot

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            self._wake.clear()
            self.refresh_once()
            failures = failures + 1 if self._retry_needed else 0
            delay = self.interval_seconds
            if 0 < failures <= 3:
                delay = min(delay, (60, 120, 240)[failures - 1])
            if not self._wake.wait(delay) and failures > 3:
                failures = 0


__all__ = ["PublicRiskCoordinator"]
