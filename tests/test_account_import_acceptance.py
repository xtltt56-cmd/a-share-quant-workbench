import json

from scripts.run_account_import_acceptance import main


def test_account_import_acceptance_is_offline_and_manual_only(tmp_path, capsys) -> None:
    assert main(["--workspace", str(tmp_path)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "PASS",
        "fills_recorded": 1,
        "duplicate_fills_recorded": 0,
        "positions_loaded": 1,
        "manual_execution_required": True,
        "order_capability_present": False,
    }
