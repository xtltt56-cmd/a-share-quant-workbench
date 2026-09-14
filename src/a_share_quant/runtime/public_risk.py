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
        interval_seconds: float = 6 * 60 * 60,
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
        self._thread = None

    def request_refresh(self) -> None:
        """Wake the worker after the daily candidate universe changes."""

        self._wake.set()

    def refresh_once(self) -> PublicRiskSnapshot | None:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("public risk clock must be timezone-aware")
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
        except (OSError, TimeoutError, RuntimeError, TypeError, ValueError):
            cached = self.store.latest()
            notice = (
                "巨潮公告检查失败，继续保留上次成功快照；未成功检查的股票不得视为无风险。"
            )
            self.publish(cached, "UPDATE_FAILED", notice)
            return cached
        self.publish(snapshot, snapshot.status, snapshot.notice_zh)
        return snapshot

    def _run(self) -> None:
        while not self._stop.is_set():
            self.refresh_once()
            self._wake.wait(self.interval_seconds)
            self._wake.clear()


__all__ = ["PublicRiskCoordinator"]
