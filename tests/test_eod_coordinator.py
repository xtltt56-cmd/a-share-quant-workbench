from datetime import date, datetime
from zoneinfo import ZoneInfo

from a_share_quant.runtime.eod_coordinator import EODCoordinator

TZ = ZoneInfo("Asia/Shanghai")


def test_eod_coordinator_runs_once_after_close_and_stops() -> None:
    calls: list[date] = []
    current = [datetime(2026, 8, 13, 15, 29, tzinfo=TZ)]
    coordinator = EODCoordinator(
        refresh=lambda day: calls.append(day),
        clock=lambda: current[0],
    )

    assert coordinator.run_due() is False
    current[0] = datetime(2026, 8, 13, 15, 31, tzinfo=TZ)
    assert coordinator.run_due() is True
    assert coordinator.run_due() is False
    coordinator.stop()

    assert calls == [date(2026, 8, 13)]


def test_failed_eod_refresh_is_retryable_without_killing_coordinator() -> None:
    calls = 0

    def refresh(day: date) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary provider failure")

    coordinator = EODCoordinator(
        refresh=refresh,
        clock=lambda: datetime(2026, 8, 13, 15, 31, tzinfo=TZ),
    )

    assert coordinator.run_due() is False
    assert coordinator.last_error == "temporary provider failure"
    assert coordinator.run_due() is True
    assert calls == 2
