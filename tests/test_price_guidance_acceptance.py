from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from scripts.run_price_guidance_acceptance import run_acceptance


def _bars(rows: int = 280) -> pd.DataFrame:
    start = date(2025, 1, 2)
    return pd.DataFrame(
        {
            "date": [start + timedelta(days=index) for index in range(rows)],
            "open": [10 + index * 0.01 for index in range(rows)],
            "high": [10.1 + index * 0.01 for index in range(rows)],
            "low": [9.9 + index * 0.01 for index in range(rows)],
            "close": [10 + index * 0.01 for index in range(rows)],
            "volume": [100000] * rows,
            "amount": [1000000] * rows,
        }
    )


def test_acceptance_is_paper_only_and_fail_closed(tmp_path) -> None:
    result = run_acceptance(
        workspace=tmp_path,
        bars_by_symbol={"000001": _bars(), "000002": _bars()},
        calculation_date=date(2026, 1, 1),
        valid_for=date(2026, 1, 2),
    )

    assert result["status"] == "PASS"
    assert result["candidate_plans"] > 0
    assert result["holding_plans"] > 0
    assert result["research_quantities_are_zero"] is True
    assert result["frozen_boundaries_unchanged"] is True
    assert result["manual_execution_required"] is True
    assert result["order_capability_present"] is False
    assert result["failure_closed"] is True
