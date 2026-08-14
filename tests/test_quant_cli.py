import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from scripts.quant_cli import build_parser, main


@pytest.fixture
def d_cli_root() -> Path:
    workspace = Path(__file__).resolve().parents[1]
    assert workspace.drive.casefold() == "d:"
    parent = workspace / ".runtime" / "temp"
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / f"task8-quant-cli-{uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        lexical = Path(os.path.normpath(os.path.abspath(root)))
        assert lexical.parent == parent.resolve()
        if lexical.exists():
            shutil.rmtree(lexical)


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


def test_history_backfill_requires_network_and_never_builds_direct_provider(monkeypatch) -> None:
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

    with pytest.raises(SystemExit, match="工作台"):
        main(
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

    assert built == []


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


def test_research_cli_exposes_offline_status_screen_and_frozen_contest() -> None:
    assert build_parser().parse_args(["research", "status"]).research_command == "status"
    assert build_parser().parse_args(["research", "screen"]).research_command == "screen"
    contest = build_parser().parse_args(["research", "contest-start"])
    assert contest.research_command == "contest-start"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["research", "contest-start", "--version", "candidate-v2"])


def test_research_status_is_offline(capsys, monkeypatch) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("research status must not access a provider")

    monkeypatch.setattr("scripts.quant_cli.BaoStockDataProvider", fail_network)
    assert main(["research", "status"]) == 0
    assert "网络访问" in capsys.readouterr().out


def test_research_screen_cannot_promote_and_contest_freezes_version(
    d_cli_root: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr("scripts.quant_cli._project_root", lambda: d_cli_root)
    assert main(["research", "screen"]) == 0
    screen_payload = capsys.readouterr().out
    assert "工作台" in screen_payload
    assert "禁止" in screen_payload

    # No user-supplied version can turn an unverified model into a contestant.
    with pytest.raises(SystemExit, match="模型登记"):
        main(["research", "contest-start"])


def test_workbench_cli_persists_governance_and_starts_owned_research_job(
    tmp_path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class Supervisor:
        def __init__(self, root, **kwargs):
            captured["research_root"] = root
            captured["storage_policy"] = kwargs.get("storage_policy")
            captured["network_enabled"] = kwargs.get("network_enabled")

        def register_default_jobs(self, **kwargs):
            captured["schedule"] = kwargs
            return ()

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
        "scripts.quant_cli._workbench_research_context",
        lambda _root, _now: {
            "session_completed": False,
            "data_fingerprint": None,
            "data_refreshed": False,
            "outcome_cutoff": None,
        },
    )
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
    assert captured["network_enabled"] is False
    assert "schedule" in captured
    assert "started_at" in captured


def test_workbench_cli_does_not_block_http_startup_on_daily_network_refresh(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("scripts.quant_cli.ResearchJobSupervisor", lambda root, **kwargs: type(
            "Supervisor",
            (),
            {
                "register_default_jobs": lambda self, *args, **kwargs: (),
                "start_due_jobs": lambda self, **kwargs: (),
            },
    )())
    monkeypatch.setattr("scripts.quant_cli.AdvisoryWorkbenchService", lambda **kwargs: object())
    monkeypatch.setattr(
        "scripts.quant_cli._workbench_research_context",
        lambda _root, _now: {
            "session_completed": False,
            "data_fingerprint": None,
            "data_refreshed": False,
            "outcome_cutoff": None,
        },
    )
    started: list[bool] = []
    monkeypatch.setattr("scripts.quant_cli.run_server", lambda **kwargs: started.append(True))

    assert main([
        "workbench", "--network", "--advisory-ledger", str(tmp_path / "ledger.jsonl"),
        "--advisory-initial-cash", "100000",
    ]) == 0
    assert started == [True]
    assert "refresh_daily_data_if_due" not in main.__globals__


def test_task8_workbench_registers_defaults_and_offline_never_launches_network(
    d_cli_root: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class Supervisor:
        def __init__(self, _root, **kwargs):
            captured["supervisor_kwargs"] = kwargs

        def register_job(self, *_args, **_kwargs):
            raise AssertionError("workbench must not hand-register only predict")

        def register_default_jobs(self, **kwargs):
            captured["schedule"] = kwargs
            return ("screen", "predict")

        def start_due_jobs(self, **kwargs):
            captured["started"] = kwargs
            return ("screen",)

    monkeypatch.setattr("scripts.quant_cli.ResearchJobSupervisor", Supervisor)
    monkeypatch.setattr("scripts.quant_cli.EvolutionRegistry", lambda **_kwargs: object())
    monkeypatch.setattr("scripts.quant_cli.AdvisoryWorkbenchService", lambda **_kwargs: object())
    monkeypatch.setattr("scripts.quant_cli.run_server", lambda **_kwargs: None)
    monkeypatch.setattr(
        "scripts.quant_cli._workbench_research_context",
        lambda _root, _now: {
            "session_completed": True,
            "data_fingerprint": "verified-data",
            "data_refreshed": True,
            "outcome_cutoff": _now,
        },
        raising=False,
    )

    assert main(
        [
            "workbench",
            "--offline",
            "--advisory-ledger",
            str(d_cli_root / "ledger.jsonl"),
            "--advisory-initial-cash",
            "100000",
        ]
    ) == 0

    assert captured["supervisor_kwargs"]["network_enabled"] is False
    assert captured["schedule"] == {
        "session_completed": True,
        "data_fingerprint": "verified-data",
        "data_refreshed": True,
        "outcome_cutoff": captured["schedule"]["outcome_cutoff"],
        "now": captured["schedule"]["now"],
    }
    assert "started" in captured


def test_task8_research_status_is_read_only_and_screen_does_not_bypass_supervisor(
    d_cli_root: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("scripts.quant_cli._project_root", lambda: d_cli_root)
    monkeypatch.setattr(
        "a_share_quant.runtime.research_worker.run_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("manual screen must not start a worker")
        ),
    )

    assert main(["research", "status"]) == 0
    assert not (d_cli_root / ".runtime").exists()
    assert main(["research", "screen"]) == 0
    assert "工作台" in capsys.readouterr().out
    assert not (d_cli_root / ".runtime").exists()


def test_task8_contest_start_freezes_internal_provenance_and_rejects_version_flag(
    d_cli_root: Path, monkeypatch
) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["research", "contest-start", "--version", "not-trusted"]
        )

    registration = {
        "model_id": "candidate-model",
        "model_version": "2026.08.14",
        "config_hash": "c" * 64,
        "training_snapshot_hash": "d" * 64,
    }
    terms = {
        "primary_metric": "net_cost_return",
        "tie_break": ["max_drawdown", "brier", "ece", "rank_ic", "turnover"],
        "provisional_sessions": 20,
        "provisional_matured_predictions": 100,
        "approval_sessions": 60,
        "approval_matured_predictions": 200,
    }
    monkeypatch.setattr(
        "scripts.quant_cli._frozen_model_registration",
        lambda _root, _policy: registration,
        raising=False,
    )
    monkeypatch.setattr(
        "scripts.quant_cli._contest_terms",
        lambda _root: terms,
        raising=False,
    )

    from scripts import quant_cli

    frozen = quant_cli._freeze_contest(d_cli_root)
    assert frozen["model_id"] == "candidate-model"
    assert frozen["model_version"] == "2026.08.14"
    assert frozen["config_hash"] == "c" * 64
    assert frozen["training_snapshot_hash"] == "d" * 64
    assert frozen["primary_metric"] == "net_cost_return"
    assert frozen["tie_break"] == terms["tie_break"]
    assert frozen["provisional_sessions"] == 20
    assert frozen["approval_matured_predictions"] == 200
    monkeypatch.setattr(
        "scripts.quant_cli._frozen_model_registration",
        lambda _root, _policy: (_ for _ in ()).throw(
            AssertionError("an existing frozen contest must not depend on newer inputs")
        ),
    )
    assert quant_cli._freeze_contest(d_cli_root) == frozen

    tampered = {**frozen, "model_version": "tampered"}
    path = d_cli_root / ".runtime" / "research" / "prospective-contest.json"
    path.write_text(__import__("json").dumps(tampered), encoding="utf-8")
    with pytest.raises(SystemExit, match="校验"):
        quant_cli._freeze_contest(d_cli_root)
