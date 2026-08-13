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
