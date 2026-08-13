from scripts.quant_cli import build_parser, main


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
