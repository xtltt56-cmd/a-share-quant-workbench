from pathlib import Path

from a_share_quant.account.import_inbox import AccountImportInbox
from a_share_quant.account.snapshot_store import AccountSnapshotStore
from scripts.quant_cli import main as quant_cli_main


def test_workbench_cli_wires_fixed_account_import_paths(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_service(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("scripts.quant_cli.AdvisoryWorkbenchService", fake_service)
    monkeypatch.setattr(
        "scripts.quant_cli.run_server", lambda **kwargs: captured.update(run=kwargs)
    )

    import_dir = tmp_path / "import-inbox"
    snapshot_path = tmp_path / "imported-account-snapshot.json"
    exit_code = quant_cli_main(
        [
            "workbench",
            "--offline",
            "--advisory-ledger",
            str(tmp_path / "ledger.jsonl"),
            "--advisory-initial-cash",
            "100000",
            "--account-import-dir",
            str(import_dir),
            "--account-snapshot-path",
            str(snapshot_path),
        ]
    )

    assert exit_code == 0
    assert isinstance(captured["account_import_inbox"], AccountImportInbox)
    assert captured["account_import_inbox"].root == import_dir.resolve()
    assert isinstance(captured["account_snapshot_store"], AccountSnapshotStore)
    assert captured["account_snapshot_store"].path == snapshot_path


def test_workbench_parser_defaults_to_runtime_import_inbox() -> None:
    from scripts.quant_cli import build_parser

    args = build_parser().parse_args(
        [
            "workbench",
            "--advisory-ledger",
            "ledger.jsonl",
            "--advisory-initial-cash",
            "100000",
        ]
    )

    assert args.account_import_dir == Path(".runtime/advisory/import-inbox")
    assert args.account_snapshot_path == Path(
        ".runtime/advisory/imported-account-snapshot.json"
    )
