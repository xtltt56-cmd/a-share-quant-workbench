"""Workbench-scoped end-of-day refresh coordinator."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from a_share_quant.market.trading_calendar import AShareTradingCalendar


class EODCoordinator:
    def __init__(
        self,
        *,
        refresh: Callable[[date], None],
        clock: Callable[[], datetime] | None = None,
        calendar: AShareTradingCalendar | None = None,
        close_time: time = time(15, 30),
    ) -> None:
        self.refresh = refresh
        self.clock = clock or (lambda: datetime.now(ZoneInfo("Asia/Shanghai")))
        self.calendar = calendar or AShareTradingCalendar()
        self.close_time = close_time
        self._completed: set[date] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_due(self) -> bool:
        if self._stop.is_set():
            return False
        current = self.clock().astimezone(ZoneInfo("Asia/Shanghai"))
        day = current.date()
        if (
            day in self._completed
            or not self.calendar.is_session(day)
            or current.timetz().replace(tzinfo=None) < self.close_time
        ):
            return False
        self.refresh(day)
        self._completed.add(day)
        return True

    def start(self, *, interval_seconds: float = 60.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def worker() -> None:
            while not self._stop.wait(interval_seconds):
                self.run_due()

        self._thread = threading.Thread(target=worker, name="quant-eod-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None


__all__ = ["EODCoordinator"]
