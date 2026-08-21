from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from a_share_quant.research.prospective_competition import (
    OutcomeObservation,
    ProspectiveCompetition,
    ProspectiveContest,
    ProspectivePrediction,
)
from a_share_quant.runtime import research_worker
from a_share_quant.signals.realtime import OfficialModelSignal
from a_share_quant.storage.official_signal_store import OfficialSignalStore
from a_share_quant.storage.project_storage import ProjectStoragePolicy
from a_share_quant.storage.prospective_ledger_store import ProspectiveLedgerStore
from a_share_quant.storage.research_data_store import ResearchDataset, ResearchDataStore


@pytest.fixture
def d_worker_root() -> Path:
    workspace = Path(__file__).resolve().parents[1]
    assert workspace.drive.casefold() == "d:"
    parent = workspace / ".runtime" / "temp"
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / f"task8-research-worker-{uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        lexical = Path(os.path.normpath(os.path.abspath(root)))
        assert lexical.parent == parent.resolve()
        if lexical.exists():
            shutil.rmtree(lexical)


def test_worker_parser_allows_only_fixed_internal_jobs() -> None:
    assert research_worker.parse_args(["history"]).job == "history"
    assert research_worker.parse_args(["screen"]).job == "screen"
    assert research_worker.parse_args(["predict"]).job == "predict"
    assert research_worker.parse_args(["settle"]).job == "settle"

    with pytest.raises(SystemExit):
        research_worker.parse_args(["forecast"])
    with pytest.raises(SystemExit):
        research_worker.parse_args(["history", "--output", "C:\\outside.json"])


def test_derived_screen_features_retain_unadjusted_close_for_cost_screening() -> None:
    from datetime import date, timedelta

    from a_share_quant.research.historical_screening import (
        HistoricalCandidate,
        HistoricalEngineeringScreen,
    )

    periods = 1_800
    frame = pd.DataFrame(
        {
            "date": [date(2019, 1, 1) + timedelta(days=index) for index in range(periods)],
            "close": [10.0 + index * 0.001 for index in range(periods)],
            "research_usable": [True] * periods,
        }
    )
    features, labels = research_worker._derived_screen_frames(frame, "000001")

    assert "close" in features.columns
    snapshot = type("Snapshot", (), {})()
    snapshot.features = features
    snapshot.labels = labels
    snapshot.signal_cutoff = features["date"].max()
    snapshot.canonical_sha256 = "a" * 64
    result = HistoricalEngineeringScreen().run(
        snapshot,
        [HistoricalCandidate("derived", score_column="feature_momentum_5")],
    )

    assert "cost_data_unavailable" not in result.trials[0].leakage_flags


def test_worker_status_is_project_local_and_screen_is_non_promotional(tmp_path) -> None:
    exit_code = research_worker.run_job("screen", tmp_path)

    assert exit_code != 0  # no verified research dataset is a blocked screen
    path = tmp_path / ".runtime" / "research" / "screen-status.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert payload["promotion"] == "NEVER"
    assert Path(payload["status_path"]).resolve() == path.resolve()
    assert Path(payload["status_path"]).drive == path.drive


@pytest.mark.parametrize(
    ("job", "expected_status"),
    [
        ("history", "BLOCKED"),
        ("predict", "BLOCKED"),
        ("settle", "BLOCKED"),
    ],
)
def test_worker_commands_fail_closed_without_verified_inputs(
    tmp_path, job: str, expected_status: str
) -> None:
    exit_code = research_worker.run_job(job, tmp_path)

    assert exit_code != 0
    path = tmp_path / ".runtime" / "research" / f"{job}-status.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == expected_status
    assert payload["promotion"] == "NEVER"


def test_worker_main_does_not_accept_paths_or_flags(tmp_path) -> None:
    with pytest.raises(SystemExit):
        research_worker.main(["history", "--repo-root", str(tmp_path)])


def test_task8_owned_worker_status_binds_exact_instance_evidence(
    d_worker_root: Path, monkeypatch
) -> None:
    """A supervised child cannot report success without durable own evidence."""

    policy = ProjectStoragePolicy(d_worker_root)
    job_id = "screen-0123456789abcdefabcd"
    monkeypatch.setenv("A_SHARE_QUANT_RESEARCH_WORKBENCH", "1")
    monkeypatch.setenv("A_SHARE_QUANT_RESEARCH_JOB_ID", job_id)
    monkeypatch.setattr(
        research_worker,
        "_verified_job_context",
        lambda _job, _policy: {"verified": True},
    )
    monkeypatch.setattr(
        research_worker,
        "_run_screen",
        lambda _root, _policy, _context: {"result": "persisted"},
    )

    assert research_worker.run_job("screen", d_worker_root, storage_policy=policy) == 0
    status_path = d_worker_root / ".runtime" / "research" / "status" / f"{job_id}.json"
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    artifact = d_worker_root / ".runtime" / "research" / payload["artifact_relpath"]

    assert payload["job_id"] == job_id
    assert payload["artifact_relpath"] == f"evidence/{job_id}.json"
    assert payload["artifact_digest"] == hashlib.sha256(artifact.read_bytes()).hexdigest()


def test_task8_worker_uses_only_workbench_derived_inputs_not_request_json(
    d_worker_root: Path, monkeypatch
) -> None:
    """The four worker contracts must not depend on hand-authored requests."""

    policy = ProjectStoragePolicy(d_worker_root)
    monkeypatch.setenv("A_SHARE_QUANT_RESEARCH_WORKBENCH", "1")
    screen = {"derived": "screen"}
    prediction = {"derived": "predict"}
    settlement = {"derived": "settle"}
    contest = {"frozen": "contest"}
    monkeypatch.setattr(
        research_worker, "_derive_screen_context", lambda _policy: screen, raising=False
    )
    monkeypatch.setattr(
        research_worker,
        "_derive_predict_context",
        lambda _policy, _contest: prediction,
        raising=False,
    )
    monkeypatch.setattr(
        research_worker,
        "_derive_settle_context",
        lambda _policy, _contest: settlement,
        raising=False,
    )
    monkeypatch.setattr(
        research_worker, "_read_frozen_contest", lambda _policy: contest
    )

    assert research_worker._verified_job_context("screen", policy) is screen
    assert research_worker._verified_job_context("predict", policy) is prediction
    assert research_worker._verified_job_context("settle", policy) is settlement


def test_task8_history_partial_batch_is_not_reported_as_success(
    d_worker_root: Path, monkeypatch
) -> None:
    """Persisted partial history may be useful, but its lifecycle cycle fails."""

    class Provider:
        def close(self):
            return None

    class Result:
        symbols_updated = 1
        rows_written = 4
        failures = {"600001": "TRANSIENT_PROVIDER_FAILURE"}

        @staticmethod
        def to_dict():
            return {
                "symbols_updated": 1,
                "rows_written": 4,
                "failures": {"600001": "TRANSIENT_PROVIDER_FAILURE"},
            }

    class Coordinator:
        def __init__(self, *_args, **_kwargs):
            return None

        def run(self, **_kwargs):
            return Result()

    job_id = "history-0123456789abcdefabcd"
    policy = ProjectStoragePolicy(d_worker_root)
    config_path = d_worker_root / "config" / "research_maturity.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("history:\n  start_date: '2019-01-01'\n", encoding="utf-8")
    monkeypatch.setenv("A_SHARE_QUANT_RESEARCH_WORKBENCH", "1")
    monkeypatch.setenv("A_SHARE_QUANT_RESEARCH_JOB_ID", job_id)
    monkeypatch.setattr(research_worker, "_verified_job_context", lambda *_args: {})
    monkeypatch.setattr(
        "a_share_quant.data.providers.baostock.BaoStockDataProvider", Provider
    )
    monkeypatch.setattr(
        "a_share_quant.runtime.historical_backfill.HistoricalBackfillCoordinator",
        Coordinator,
    )

    assert research_worker.run_job("history", d_worker_root, storage_policy=policy) == 1
    payload = json.loads(
        (
            d_worker_root / ".runtime" / "research" / "status" / f"{job_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert payload["status"] == "PARTIAL"
    assert payload["reason_code"] == "HISTORY_PARTIAL"


def test_task8_history_all_failed_batch_is_reported_as_failed(
    d_worker_root: Path, monkeypatch
) -> None:
    """A zero-write failed history batch is not merely an unavailable input."""

    class Provider:
        def close(self):
            return None

    class Result:
        symbols_updated = 0
        rows_written = 0
        failures = {"600001": "PERMANENT_PROVIDER_FAILURE"}

        @staticmethod
        def to_dict():
            return {
                "symbols_updated": 0,
                "rows_written": 0,
                "failures": {"600001": "PERMANENT_PROVIDER_FAILURE"},
            }

    class Coordinator:
        def __init__(self, *_args, **_kwargs):
            return None

        def run(self, **_kwargs):
            return Result()

    job_id = "history-abcdef0123456789abcd"
    policy = ProjectStoragePolicy(d_worker_root)
    config_path = d_worker_root / "config" / "research_maturity.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("history:\n  start_date: '2019-01-01'\n", encoding="utf-8")
    monkeypatch.setenv("A_SHARE_QUANT_RESEARCH_WORKBENCH", "1")
    monkeypatch.setenv("A_SHARE_QUANT_RESEARCH_JOB_ID", job_id)
    monkeypatch.setattr(research_worker, "_verified_job_context", lambda *_args: {})
    monkeypatch.setattr(
        "a_share_quant.data.providers.baostock.BaoStockDataProvider", Provider
    )
    monkeypatch.setattr(
        "a_share_quant.runtime.historical_backfill.HistoricalBackfillCoordinator",
        Coordinator,
    )

    assert research_worker.run_job("history", d_worker_root, storage_policy=policy) == 2
    payload = json.loads(
        (
            d_worker_root / ".runtime" / "research" / "status" / f"{job_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert payload["status"] == "FAILED"
    assert payload["reason_code"] == "HISTORY_ALL_FAILED"


def test_task8_history_worker_uses_configured_start_and_bounded_batch(
    d_worker_root: Path, monkeypatch
) -> None:
    """History lifecycle must advance from the configured maturity start date."""

    config_path = d_worker_root / "config" / "research_maturity.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "history:\n  start_date: '2019-01-01'\n", encoding="utf-8"
    )
    calls: list[dict[str, object]] = []

    class Provider:
        def close(self):
            return None

    class Result:
        failures: dict[str, str] = {}
        rows_written = 0

        @staticmethod
        def to_dict():
            return {"failures": {}, "rows_written": 0}

    class Coordinator:
        def __init__(self, *_args, **_kwargs):
            return None

        def run(self, **kwargs):
            calls.append(kwargs)
            return Result()

    policy = ProjectStoragePolicy(d_worker_root)
    monkeypatch.setattr(research_worker, "_current_time", lambda: datetime(
        2026, 8, 14, 8, tzinfo=timezone.utc
    ))
    monkeypatch.setenv("A_SHARE_QUANT_RESEARCH_WORKBENCH", "1")
    monkeypatch.setattr(
        "a_share_quant.data.providers.baostock.BaoStockDataProvider", Provider
    )
    monkeypatch.setattr(
        "a_share_quant.runtime.historical_backfill.HistoricalBackfillCoordinator",
        Coordinator,
    )

    result = research_worker._run_history(d_worker_root, policy, {})

    assert result["kind"] == "history"
    assert calls == [
        {
            "start": date(2019, 1, 1),
            "end": date(2026, 8, 14),
            "limit": 100,
        }
    ]


def test_task8_history_success_binds_manifest_and_checkpoint_digests(
    d_worker_root: Path, monkeypatch
) -> None:
    """History success must carry durable manifest/checkpoint evidence."""

    config_path = d_worker_root / "config" / "research_maturity.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("history:\n  start_date: '2019-01-01'\n", encoding="utf-8")

    class Provider:
        def close(self):
            return None

    class Result:
        failures: dict[str, str] = {}
        rows_written = 1

        @staticmethod
        def to_dict():
            return {"failures": {}, "rows_written": 1}

    class Coordinator:
        def __init__(self, store, *_args, **_kwargs):
            self.store = store
            self.checkpoint_path = store.policy.authorize(
                ".runtime/research/historical-backfill-checkpoint.json"
            )

        def run(self, **_kwargs):
            self.store.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            self.store.manifest_path.write_bytes(b"manifest-evidence")
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            self.checkpoint_path.write_bytes(b"checkpoint-evidence")
            return Result()

    policy = ProjectStoragePolicy(d_worker_root)
    monkeypatch.setenv("A_SHARE_QUANT_RESEARCH_WORKBENCH", "1")
    monkeypatch.setattr(
        "a_share_quant.data.providers.baostock.BaoStockDataProvider", Provider
    )
    monkeypatch.setattr(
        "a_share_quant.runtime.historical_backfill.HistoricalBackfillCoordinator",
        Coordinator,
    )

    result = research_worker._run_history(d_worker_root, policy, {})

    assert result["result"]["history_manifest_digest"] == hashlib.sha256(
        b"manifest-evidence"
    ).hexdigest()
    assert result["result"]["history_checkpoint_digest"] == hashlib.sha256(
        b"checkpoint-evidence"
    ).hexdigest()


def test_task8_predict_derives_future_record_from_frozen_contest_and_daily_signal(
    d_worker_root: Path, monkeypatch
) -> None:
    """No pre-written prediction request can substitute for a verified signal."""

    # UTC afternoon is already the next local calendar boundary for some
    # callers; selection must use Asia/Shanghai rather than ``now.date()``.
    now = datetime(2026, 8, 14, 15, tzinfo=timezone.utc)
    policy = ProjectStoragePolicy(d_worker_root)
    signal_path = d_worker_root / ".runtime" / "signals" / "official-daily.json"
    OfficialSignalStore(signal_path).put_signals(
        (
            OfficialModelSignal(
                signal_date=date(2026, 8, 14),
                symbol="600001",
                name="测试股票",
                normalized_score=82.0,
                strategy_version="official-rule-v1",
                model_version="daily-rule-v1",
                feature_version="daily-features-v1",
                data_mode="historical",
                source="verified-free-source",
                data_cutoff=date(2026, 8, 14),
                generated_at=now - timedelta(minutes=1),
                rank=1,
                reference_price=10.0,
                invalidation_price=9.0,
            ),
        )
    )
    signal_digest = hashlib.sha256(signal_path.read_bytes()).hexdigest()
    contest = {
        "format_version": 3,
        "contest_started_at": (now - timedelta(minutes=2)).isoformat(),
        "model_id": "official-rule-v1",
        "model_version": "daily-rule-v1",
        "config_hash": "c" * 64,
        "training_snapshot_hash": "d" * 64,
        "official_signal_digest": signal_digest,
        "primary_metric": "net_cost_return",
        "tie_break": ["max_drawdown", "brier", "ece", "rank_ic", "turnover"],
        "provisional_sessions": 20,
        "provisional_matured_predictions": 100,
        "approval_sessions": 60,
        "approval_matured_predictions": 200,
        "evidence_mode": "PROSPECTIVE_ONLY",
        "status": "PROSPECTIVE_COLLECTING",
        "promotion": "NEVER",
    }
    contest["sha256"] = hashlib.sha256(
        research_worker._canonical_json(contest)
    ).hexdigest()
    contest_path = d_worker_root / ".runtime" / "research" / "prospective-contest.json"
    contest_path.parent.mkdir(parents=True, exist_ok=True)
    contest_path.write_text(json.dumps(contest), encoding="utf-8")
    job_id = "predict-0123456789abcdefabcd"
    monkeypatch.setenv("A_SHARE_QUANT_RESEARCH_WORKBENCH", "1")
    monkeypatch.setenv("A_SHARE_QUANT_RESEARCH_JOB_ID", job_id)
    monkeypatch.setattr(research_worker, "_current_time", lambda: now)

    assert research_worker.run_job("predict", d_worker_root, storage_policy=policy) == 0
    assert not (d_worker_root / ".runtime" / "research" / "predict-request.json").exists()
    predictions = ProspectiveLedgerStore(policy=policy).predictions()
    assert len(predictions) == 1
    assert predictions[0].model_id == "official-rule-v1"
    assert predictions[0].as_of == date(2026, 8, 14)
    archive = d_worker_root / predictions[0].source_artifact_path
    assert archive.exists()
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == signal_digest
    contest["official_signal_digest"] = "a" * 64
    contest.pop("sha256", None)
    with pytest.raises(research_worker._Blocked, match="OFFICIAL_SIGNAL_DIGEST_MISMATCH"):
        research_worker._derive_predict_context(policy, contest)


def test_task8_predict_uses_verified_exchange_calendar_for_maturity(
    d_worker_root: Path, monkeypatch
) -> None:
    """A-share maturity must count verified sessions, not weekdays."""

    now = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    calendar = (
        date(2026, 8, 17),
        date(2026, 8, 18),
        date(2026, 8, 20),
        date(2026, 8, 21),
        date(2026, 8, 24),
    )
    signal = OfficialModelSignal(
        signal_date=date(2026, 8, 14),
        symbol="600001",
        name="测试股票",
        normalized_score=82.0,
        strategy_version="official-rule-v1",
        model_version="daily-rule-v1",
        feature_version="daily-features-v1",
        data_mode="historical",
        source="verified-free-source",
        data_cutoff=date(2026, 8, 14),
        generated_at=now - timedelta(minutes=1),
        rank=1,
        reference_price=10.0,
        invalidation_price=9.0,
    )
    contest = {
        "format_version": 3,
        "contest_started_at": (now - timedelta(minutes=2)).isoformat(),
        "model_id": "official-rule-v1",
        "model_version": "daily-rule-v1",
        "config_hash": "c" * 64,
        "training_snapshot_hash": "d" * 64,
        "official_signal_digest": "e" * 64,
        "primary_metric": "net_cost_return",
        "tie_break": ["max_drawdown", "brier", "ece", "rank_ic", "turnover"],
        "provisional_sessions": 20,
        "provisional_matured_predictions": 100,
        "approval_sessions": 60,
        "approval_matured_predictions": 200,
        "evidence_mode": "PROSPECTIVE_ONLY",
        "status": "PROSPECTIVE_COLLECTING",
        "promotion": "NEVER",
    }
    captured: dict[str, object] = {}

    class FakeCompetition:
        def __init__(self, *, session_calendar=None, **_kwargs):
            captured["calendar"] = session_calendar

        def append_prediction(self, prediction):
            captured["prediction"] = prediction
            return prediction

    monkeypatch.setattr(
        "a_share_quant.research.prospective_competition.ProspectiveCompetition",
        FakeCompetition,
    )
    policy = ProjectStoragePolicy(d_worker_root)

    result = research_worker._run_predict(
        d_worker_root,
        policy,
        {
            "contest": contest,
            "signals": (signal,),
            "daily_signal_digest": "f" * 64,
            "session_calendar": calendar,
            "session_calendar_digest": "a" * 64,
        },
    )

    assert result["kind"] == "predict"
    assert captured["calendar"] == calendar
    assert captured["prediction"].maturity_date == date(2026, 8, 24)


def test_task8_predict_replay_is_idempotent_for_one_cycle(
    d_worker_root: Path, monkeypatch
) -> None:
    """A crash/replay cannot append a second prediction for one cycle."""

    now = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    signal = OfficialModelSignal(
        signal_date=date(2026, 8, 14),
        symbol="600001",
        name="测试股票",
        normalized_score=82.0,
        strategy_version="official-rule-v1",
        model_version="daily-rule-v1",
        feature_version="daily-features-v1",
        data_mode="historical",
        source="verified-free-source",
        data_cutoff=date(2026, 8, 14),
        generated_at=now - timedelta(minutes=1),
        rank=1,
        reference_price=10.0,
        invalidation_price=9.0,
    )
    contest = {
        "format_version": 3,
        "contest_started_at": (now - timedelta(minutes=2)).isoformat(),
        "model_id": "official-rule-v1",
        "model_version": "daily-rule-v1",
        "config_hash": "c" * 64,
        "training_snapshot_hash": "d" * 64,
        "official_signal_digest": "e" * 64,
        "primary_metric": "net_cost_return",
        "tie_break": ["max_drawdown", "brier", "ece", "rank_ic", "turnover"],
        "provisional_sessions": 20,
        "provisional_matured_predictions": 100,
        "approval_sessions": 60,
        "approval_matured_predictions": 200,
        "evidence_mode": "PROSPECTIVE_ONLY",
        "status": "PROSPECTIVE_COLLECTING",
        "promotion": "NEVER",
    }
    calendar = tuple(
        date(2026, 8, day) for day in (17, 18, 20, 21, 24, 25, 26, 27, 28, 31)
    )
    context = {
        "contest": contest,
        "signals": (signal,),
        "daily_signal_digest": "f" * 64,
        "source_artifact_path": ".runtime/research/signal-archive/" + "f" * 64 + ".json",
        "session_calendar": calendar,
        "session_calendar_digest": "a" * 64,
    }
    policy = ProjectStoragePolicy(d_worker_root)
    monkeypatch.setenv("A_SHARE_QUANT_RESEARCH_JOB_ID", "predict-cycle-0123456789abcdefabcd")
    monkeypatch.setattr(research_worker, "_current_time", lambda: now)
    first = research_worker._run_predict(d_worker_root, policy, context)
    monkeypatch.setattr(research_worker, "_current_time", lambda: now + timedelta(minutes=1))
    second = research_worker._run_predict(d_worker_root, policy, context)

    predictions = ProspectiveLedgerStore(policy=policy).predictions()
    assert len(predictions) == 1
    assert first["predictions"][0]["prediction_id"] == second["predictions"][0]["prediction_id"]
    assert predictions[0].source_input_digest == "f" * 64
    assert predictions[0].source_artifact_path.endswith("/" + "f" * 64 + ".json")


def test_task8_uncalibrated_rank_score_is_excluded_from_calibration_metrics(
    d_worker_root: Path,
) -> None:
    """A ranking score must not be reported as calibrated probability quality."""

    policy = ProjectStoragePolicy(d_worker_root)
    ledger = ProspectiveLedgerStore(policy=policy)
    competition = ProspectiveCompetition(
        store=ledger, now=datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    )
    prediction = competition.append_prediction(
        ProspectivePrediction(
            model_id="rule-ranking",
            model_version="v1",
            config_hash="c" * 64,
            training_snapshot_hash="d" * 64,
            symbol="600001",
            name="测试股票",
            prediction_at=datetime(2026, 8, 14, 8, tzinfo=timezone.utc),
            as_of=date(2026, 8, 14),
            horizon=5,
            score=82.0,
            probability=None,
            probability_calibrated=False,
            guidance_price_bands={"reference": (10.0, 10.0)},
        )
    )
    competition.now = datetime(2026, 8, 30, 8, tzinfo=timezone.utc)
    competition.settle_due(
        OutcomeObservation(
            prediction_id=prediction.id,
            symbol=prediction.symbol,
            maturity_date=prediction.maturity_date,
            outcome_at=datetime(2026, 8, 30, 8, tzinfo=timezone.utc),
            realized_price=11.0,
            realized_return=0.1,
            data_version="returns-v1",
            data_sha256="e" * 64,
            status="OK",
            fresh=True,
            complete=True,
            session_aligned=True,
            corporate_action_ok=True,
        )
    )

    metrics = competition.compute_metrics()

    assert metrics.brier is None
    assert metrics.ece is None
    assert metrics.net_cost_return is not None


def test_task8_frozen_model_bundle_digest_rejects_changed_candidate(
    d_worker_root: Path,
) -> None:
    policy = ProjectStoragePolicy(d_worker_root)
    contest = ProspectiveContest(now=datetime(2026, 8, 14, 8, tzinfo=timezone.utc))
    contest.start(
        model_id="rule-ranking",
        model_version="v1",
        config_hash="c" * 64,
        training_snapshot_hash="d" * 64,
        model_bundle_digest="a" * 64,
        started_at=datetime(2026, 8, 14, 7, tzinfo=timezone.utc),
    )
    competition = ProspectiveCompetition(
        store=ProspectiveLedgerStore(policy=policy),
        contest=contest,
        now=datetime(2026, 8, 14, 8, tzinfo=timezone.utc),
        session_calendar=tuple(
            date(2026, 8, day) for day in (17, 18, 19, 20, 21, 24)
        ),
    )
    with pytest.raises(ValueError, match="frozen contest"):
        competition.append_prediction(
            ProspectivePrediction(
                model_id="rule-ranking",
                model_version="v1",
                config_hash="c" * 64,
                training_snapshot_hash="d" * 64,
                symbol="600001",
                name="测试股票",
                prediction_at=datetime(2026, 8, 14, 8, tzinfo=timezone.utc),
                as_of=date(2026, 8, 14),
                horizon=5,
                score=82.0,
                probability=None,
                probability_calibrated=False,
                model_bundle_digest="b" * 64,
                maturity_date=date(2026, 8, 21),
                guidance_price_bands={"reference": (10.0, 10.0)},
            )
        )


def test_task8_worker_rejects_contest_fields_outside_frozen_contract() -> None:
    """A digest alone is only an integrity check, not permission for extra input."""

    payload = {
        "format_version": 3,
        "contest_started_at": datetime(2026, 8, 14, 7, tzinfo=timezone.utc).isoformat(),
        "model_id": "official-rule-v1",
        "model_version": "daily-rule-v1",
        "config_hash": "c" * 64,
        "training_snapshot_hash": "d" * 64,
        "official_signal_digest": "e" * 64,
        "primary_metric": "net_cost_return",
        "tie_break": ["max_drawdown", "brier", "ece", "rank_ic", "turnover"],
        "provisional_sessions": 20,
        "provisional_matured_predictions": 100,
        "approval_sessions": 60,
        "approval_matured_predictions": 200,
        "evidence_mode": "PROSPECTIVE_ONLY",
        "status": "PROSPECTIVE_COLLECTING",
        "promotion": "NEVER",
        "unbound_model_override": "not-allowed",
    }

    with pytest.raises(ValueError, match="frozen"):
        research_worker._contest_from_payload(payload)


def test_task8_settle_derives_actual_outcome_from_refreshed_return_artifact(
    d_worker_root: Path, monkeypatch
) -> None:
    """Settlement must use a post-prediction immutable return artifact, not JSON."""

    now = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    policy = ProjectStoragePolicy(d_worker_root)
    contest = {
        "format_version": 3,
        "contest_started_at": datetime(2026, 8, 6, 8, tzinfo=timezone.utc).isoformat(),
        "model_id": "official-rule-v1",
        "model_version": "daily-rule-v1",
        "config_hash": "c" * 64,
        "training_snapshot_hash": "d" * 64,
        "official_signal_digest": "e" * 64,
        "primary_metric": "net_cost_return",
        "tie_break": ["max_drawdown", "brier", "ece", "rank_ic", "turnover"],
        "provisional_sessions": 20,
        "provisional_matured_predictions": 100,
        "approval_sessions": 60,
        "approval_matured_predictions": 200,
        "evidence_mode": "PROSPECTIVE_ONLY",
        "status": "PROSPECTIVE_COLLECTING",
        "promotion": "NEVER",
    }
    contest["sha256"] = hashlib.sha256(
        research_worker._canonical_json(contest)
    ).hexdigest()
    contest_path = d_worker_root / ".runtime" / "research" / "prospective-contest.json"
    contest_path.parent.mkdir(parents=True, exist_ok=True)
    contest_path.write_text(json.dumps(contest), encoding="utf-8")
    frozen = research_worker._contest_from_payload(
        {key: value for key, value in contest.items() if key != "sha256"}
    )
    prediction = ProspectivePrediction(
        model_id="official-rule-v1",
        model_version="daily-rule-v1",
        config_hash="c" * 64,
        training_snapshot_hash="d" * 64,
        symbol="600001",
        name="测试股票",
        prediction_at=datetime(2026, 8, 7, 8, tzinfo=timezone.utc),
        as_of=date(2026, 8, 6),
        horizon=5,
        score=80.0,
        probability=0.8,
        guidance_price_bands={"reference": (10.0, 10.0), "invalidation": (9.0, 9.0)},
    )
    ledger = ProspectiveLedgerStore(policy=policy)
    ProspectiveCompetition(
        store=ledger,
        contest=frozen,
        now=datetime(2026, 8, 7, 8, tzinfo=timezone.utc),
    ).append_prediction(prediction)
    data = pd.DataFrame(
        [
            {
                "symbol": "600001",
                "date": prediction.maturity_date,
                "close": 11.0,
                "research_usable": True,
                "trade_status": "1",
            }
        ]
    )
    store = ResearchDataStore(
        policy,
        minimum_free_bytes=0,
        maximum_research_data_bytes=1_000_000_000,
    )
    store.replace_dataset(
        ResearchDataset.RESEARCH_RETURNS, "600001", data, "returns-v1"
    )
    job_id = "settle-0123456789abcdefabcd"
    monkeypatch.setenv("A_SHARE_QUANT_RESEARCH_WORKBENCH", "1")
    monkeypatch.setenv("A_SHARE_QUANT_RESEARCH_JOB_ID", job_id)
    monkeypatch.setattr(research_worker, "_current_time", lambda: now)

    assert research_worker.run_job("settle", d_worker_root, storage_policy=policy) == 0
    assert not (d_worker_root / ".runtime" / "research" / "settle-request.json").exists()
    outcomes = ProspectiveLedgerStore(policy=policy).settlements()
    assert len(outcomes) == 1
    assert outcomes[0].realized_price == 11.0
    assert outcomes[0].realized_return == pytest.approx(0.1)


@pytest.mark.parametrize("job", ["history", "screen", "predict", "settle"])
def test_task8_worker_dispatches_to_the_fixed_coordinator_when_verified(
    d_worker_root: Path, monkeypatch, job: str
) -> None:
    calls: list[tuple[str, Path]] = []
    policy = ProjectStoragePolicy(d_worker_root)

    monkeypatch.setattr(
        research_worker,
        "_verified_job_context",
        lambda _job, _policy: {"verified": True},
        raising=False,
    )
    monkeypatch.setattr(
        research_worker,
        f"_run_{job}",
        lambda _root, _policy, _context: calls.append((job, _root))
        or {"artifact_digest": "b" * 64},
        raising=False,
    )

    assert research_worker.run_job(job, d_worker_root, storage_policy=policy) == 0
    assert calls == [(job, d_worker_root.resolve())]
    payload = json.loads(
        (d_worker_root / ".runtime" / "research" / f"{job}-status.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["status"] == "SUCCESS"
    assert payload["promotion"] == "NEVER"
    assert payload["artifact_digest"] == "b" * 64
