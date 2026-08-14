from pathlib import Path

import pytest

from scripts.quant_cli import build_parser, main


def test_history_cli_exposes_offline_status_and_explicit_network_backfill() -> None:
    status = build_parser().parse_args(["history", "status"])
    backfill = build_parser().parse_args(
        [
            "history",
            "backfill",
            "--start",
            "2019-01-01",
            "--end",
            "2026-08-13",
            "--network",
        ]
    )

    assert status.history_command == "status"
    assert backfill.network is True


def test_history_backfill_requires_network_before_building_provider(monkeypatch) -> None:
    built: list[bool] = []
    monkeypatch.setattr(
        "scripts.quant_cli._create_history_coordinator",
        lambda _root, *, allow_network: built.append(allow_network),
    )

    with pytest.raises(SystemExit, match="--network"):
        main(
            [
                "history",
                "backfill",
                "--start",
                "2019-01-01",
                "--end",
                "2026-08-13",
            ]
        )

    assert built == []


def test_history_backfill_prints_fixed_d_drive_targets_before_requests(
    monkeypatch, capsys
) -> None:
    events: list[str] = []

    class Coordinator:
        checkpoint_path = Path("D:/量化交易/.runtime/research/history.json")
        data_root = Path("D:/量化交易/data/research")

        def run(self, *, start, end):
            printed = capsys.readouterr().out
            assert "D:\\" in printed or "D:/" in printed
            assert "history.json" in printed
            events.append(f"run:{start}:{end}")
            return type(
                "Result",
                (),
                {
                    "to_dict": lambda self: {
                        "symbols_updated": 1,
                        "rows_written": 2,
                        "failures": {},
                    }
                },
            )()

    monkeypatch.setattr(
        "scripts.quant_cli._create_history_coordinator",
        lambda _root, *, allow_network: Coordinator(),
    )

    assert main(
        [
            "history",
            "backfill",
            "--start",
            "2019-01-01",
            "--end",
            "2026-08-13",
            "--network",
        ]
    ) == 0
    assert events == ["run:2019-01-01:2026-08-13"]


def test_history_status_is_offline_and_rejects_custom_output_path(
    monkeypatch, capsys
) -> None:
    created: list[bool] = []

    class Coordinator:
        checkpoint_path = Path("D:/量化交易/.runtime/research/history.json")
        data_root = Path("D:/量化交易/data/research")

        def coverage(self):
            return type("Coverage", (), {"to_dict": lambda self: {"row_count": 0}})()

    def create(_root, *, allow_network):
        created.append(allow_network)
        return Coordinator()

    monkeypatch.setattr("scripts.quant_cli._create_history_coordinator", create)
    assert main(["history", "status"]) == 0
    assert created == [False]
    assert '"网络访问": false' in capsys.readouterr().out

    with pytest.raises(SystemExit):
        build_parser().parse_args(["history", "status", "--output", "C:/history"])


def test_daily_candidates_cli_exposes_explicit_stale_data_escape_hatch() -> None:
    args = build_parser().parse_args(["daily-candidates", "--allow-stale"])

    assert args.allow_stale is True


def test_workbench_cli_persists_governance_and_starts_owned_research_job(
    tmp_path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class Supervisor:
        def __init__(self, root):
            captured["research_root"] = root

        def register_job(self, job_id, command, *, due_at):
            captured["job"] = (job_id, command, due_at)

        def start_due_jobs(self, *, now):
            captured["started_at"] = now
            return ("forecast-on-launch",)

    class Governance:
        def __init__(self, **kwargs):
            captured["governance"] = kwargs

    monkeypatch.setattr("scripts.quant_cli.ResearchJobSupervisor", Supervisor)
    monkeypatch.setattr("scripts.quant_cli.EvolutionRegistry", Governance)
    monkeypatch.setattr("scripts.quant_cli.AdvisoryWorkbenchService", lambda **kwargs: object())
    monkeypatch.setattr(
        "scripts.quant_cli.run_server", lambda **kwargs: captured.update(run=kwargs)
    )

    assert main(
        [
            "workbench",
            "--offline",
            "--advisory-ledger",
            str(tmp_path / "ledger.jsonl"),
            "--advisory-initial-cash",
            "100000",
        ]
    ) == 0

    governance = captured["governance"]
    assert isinstance(governance, dict)
    assert str(governance["state_path"]).endswith(
        ".runtime\\research\\evolution-registry.json"
    )
    assert captured["job"][:2] == ("forecast-on-launch", ("research", "forecast"))
    assert "started_at" in captured


def test_workbench_cli_does_not_block_http_startup_on_daily_network_refresh(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("scripts.quant_cli.ResearchJobSupervisor", lambda root: type(
        "Supervisor",
        (),
        {
            "register_job": lambda self, *args, **kwargs: None,
            "start_due_jobs": lambda self, **kwargs: (),
        },
    )())
    monkeypatch.setattr("scripts.quant_cli.AdvisoryWorkbenchService", lambda **kwargs: object())
    started: list[bool] = []
    monkeypatch.setattr("scripts.quant_cli.run_server", lambda **kwargs: started.append(True))

    assert main([
        "workbench", "--network", "--advisory-ledger", str(tmp_path / "ledger.jsonl"),
        "--advisory-initial-cash", "100000",
    ]) == 0
    assert started == [True]
    assert "refresh_daily_data_if_due" not in main.__globals__
