from datetime import date, datetime, timezone

import pytest

from a_share_quant.contracts.timing import (
    TimeSemantics,
    next_trading_date,
    validate_execution_date,
)


def test_signal_timestamp_semantics_requires_ordered_timezone_aware_times() -> None:
    semantics = TimeSemantics(
        data_available_at=datetime(2026, 8, 7, 7, tzinfo=timezone.utc),
        signal_at=datetime(2026, 8, 7, 8, tzinfo=timezone.utc),
        decision_at=datetime(2026, 8, 7, 8, 1, tzinfo=timezone.utc),
        order_at=datetime(2026, 8, 7, 8, 2, tzinfo=timezone.utc),
        execution_at=datetime(2026, 8, 10, 1, 30, tzinfo=timezone.utc),
    )

    semantics.validate()


def test_no_same_bar_execution() -> None:
    with pytest.raises(ValueError, match="after signal"):
        validate_execution_date(date(2026, 8, 7), date(2026, 8, 7), t_plus_one=True)


def test_execution_date_alignment_uses_next_available_trading_session() -> None:
    sessions = [date(2026, 8, 7), date(2026, 8, 10), date(2026, 8, 11)]

    assert next_trading_date(date(2026, 8, 7), sessions) == date(2026, 8, 10)
    assert validate_execution_date(date(2026, 8, 7), date(2026, 8, 10), t_plus_one=True)
