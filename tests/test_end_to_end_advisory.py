from pathlib import Path

from scripts import run_advisory_acceptance
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


def test_acceptance_fixture_is_repeatable_without_accumulating_manual_fills(tmp_path: Path) -> None:
    run_acceptance_fixture(tmp_path)
    first_ledger = (tmp_path / "account-ledger.jsonl").read_text(encoding="utf-8")

    second = run_acceptance_fixture(tmp_path)
    second_ledger = (tmp_path / "account-ledger.jsonl").read_text(encoding="utf-8")

    assert second["manual_buy_recorded"] is True
    assert second["qmt_reconciliation"] == "RECONCILED"
    assert second["qmt_order_submission"] == "REJECTED"
    assert len(first_ledger.splitlines()) == 1
    assert len(second_ledger.splitlines()) == 1


def test_acceptance_command_returns_nonzero_for_a_failed_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        run_advisory_acceptance,
        "run_acceptance_fixture",
        lambda _root: {
            "data_quality_failure": {"state": "UNEXPECTEDLY_ACCEPTED"},
            "manual_execution_required": True,
            "manual_buy_recorded": True,
            "guidance_state": "BUY_CANDIDATE",
            "prediction_outcomes": 1,
            "qmt_reconciliation": "RECONCILED",
            "qmt_order_submission": "REJECTED",
        },
    )
    monkeypatch.setattr(
        "sys.argv",
        ["run_advisory_acceptance.py", "--root", str(tmp_path)],
    )

    assert run_advisory_acceptance.main() == 1
