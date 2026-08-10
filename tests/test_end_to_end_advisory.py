from pathlib import Path

from scripts.run_advisory_acceptance import run_acceptance_fixture


def test_end_to_end_local_manual_advisory_flow(tmp_path: Path) -> None:
    result = run_acceptance_fixture(tmp_path)

    assert result["data_quality_failure"]["state"] == "INSUFFICIENT_DATA"
    assert result["manual_execution_required"] is True
    assert result["manual_buy_recorded"] is True
    assert result["guidance_state"] == "BUY_CANDIDATE"
    assert result["prediction_outcomes"] == 1
    assert result["qmt_reconciliation"] == "RECONCILED"
    assert result["qmt_order_submission"] == "REJECTED"
