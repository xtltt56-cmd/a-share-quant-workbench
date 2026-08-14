from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from a_share_quant.research.prospective_competition import (
    FrozenContestError,
    ImmutablePredictionError,
    OutcomeObservation,
    ProspectiveCompetition,
    ProspectiveContest,
    ProspectiveMetrics,
    ProspectivePrediction,
)
from a_share_quant.storage.prospective_ledger_store import (
    ImmutableLedgerError,
    ProspectiveLedgerStore,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)


def _bands() -> dict[str, tuple[float, float]]:
    return {
        "buy": (10.0, 10.5),
        "observe": (10.5, 11.0),
        "risk": (9.0, 9.5),
    }


def _ledger(tmp_path: Path) -> ProspectiveCompetition:
    store = ProspectiveLedgerStore(tmp_path / "prospective-ledger.jsonl")
    return ProspectiveCompetition(store=store, now=NOW)


def _prediction(
    ledger: ProspectiveCompetition, *, model_version: str = "v1"
) -> ProspectivePrediction:
    return ledger.append_prediction(
        model_id="challenger",
        model_version=model_version,
        config_hash="cfg-v1",
        training_snapshot_hash="train-v1",
        symbol="600001",
        name="示例股份",
        prediction_at=datetime(2026, 8, 19, 8, 0, tzinfo=UTC),
        as_of=date(2026, 8, 19),
        horizon=5,
        score=0.73,
        probability=0.68,
        guidance_price_bands=_bands(),
        evidence_mode="PROSPECTIVE",
    )


def _valid_outcome(prediction: ProspectivePrediction) -> OutcomeObservation:
    return OutcomeObservation(
        prediction_id=prediction.id,
        symbol=prediction.symbol,
        maturity_date=prediction.maturity_date,
        outcome_at=datetime(2026, 8, 30, 8, 0, tzinfo=UTC),
        realized_price=10.8,
        realized_return=0.08,
        data_version="bars-v3",
        data_sha256="a" * 64,
        status="OK",
        fresh=True,
        complete=True,
        session_aligned=True,
        corporate_action_ok=True,
    )


def _mature(ledger: ProspectiveCompetition) -> None:
    ledger.now = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)


def test_prediction_is_atomically_appended_before_outcome_and_never_deleted(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    prediction = _prediction(ledger)

    assert ledger.read(prediction.id) == prediction
    assert prediction.evidence_mode == "PROSPECTIVE"
    assert prediction.guidance_price_bands["buy"] == (10.0, 10.5)
    with pytest.raises(ImmutablePredictionError):
        ledger.delete(prediction.id)
    with pytest.raises(ImmutableLedgerError):
        ledger.store.delete(prediction.id)


def test_prediction_requires_complete_prospective_metadata() -> None:
    with pytest.raises(ValueError, match="evidence_mode"):
        ProspectivePrediction(
            model_id="m",
            model_version="v1",
            config_hash="cfg",
            training_snapshot_hash="train",
            symbol="600001",
            name="示例",
            prediction_at=NOW,
            as_of=date(2026, 8, 12),
            horizon=5,
            score=0.1,
            probability=0.5,
            guidance_price_bands=_bands(),
            evidence_mode="HISTORICAL",
        )


def test_future_prediction_timestamp_and_mutating_duplicate_are_rejected(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    with pytest.raises(ValueError, match="future"):
        ledger.append_prediction(
            model_id="challenger",
            model_version="v1",
            config_hash="cfg-v1",
            training_snapshot_hash="train-v1",
            symbol="600001",
            name="示例股份",
            prediction_at=NOW + timedelta(seconds=1),
            as_of=date(2026, 8, 12),
            horizon=5,
            score=0.73,
            probability=0.68,
            guidance_price_bands=_bands(),
            evidence_mode="PROSPECTIVE",
        )
    prediction = _prediction(ledger)
    changed = ProspectivePrediction(
        **{
            **prediction.to_dict(),
            "score": 0.99,
        }
    )
    with pytest.raises(ValueError, match="different"):
        ledger.store.append_prediction(changed)


def test_missing_suspended_or_stale_outcome_delays_settlement(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    prediction = _prediction(ledger)
    _mature(ledger)
    delayed = OutcomeObservation(
        prediction_id=prediction.id,
        symbol=prediction.symbol,
        maturity_date=prediction.maturity_date,
            outcome_at=datetime(2026, 8, 30, 8, 0, tzinfo=UTC),
        realized_price=None,
        realized_return=None,
        data_version="bars-v3",
        data_sha256="b" * 64,
        status="SUSPENDED",
        fresh=True,
        complete=False,
        session_aligned=False,
        corporate_action_ok=True,
    )

    result = ledger.settle_due(delayed)

    assert result.matured is False
    assert result.delay_reason == "SUSPENDED"
    assert ledger.matured_predictions == 0
    assert ledger.settlements() == ()


def test_valid_settlement_is_hashed_idempotent_and_changed_result_rejected(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    prediction = _prediction(ledger)
    _mature(ledger)
    outcome = _valid_outcome(prediction)

    result = ledger.settle_due(outcome)
    repeated = ledger.settle_due(outcome)

    assert result.matured is True
    assert result.outcome == outcome
    assert repeated.matured is True
    assert repeated.idempotent is True
    assert ledger.matured_predictions == 1
    metrics = ledger.compute_metrics()
    assert metrics.net_cost_return is not None
    assert metrics.coverage == 1.0
    assert metrics.brier is not None
    changed = OutcomeObservation(**{**outcome.to_dict(), "realized_return": 0.09})
    with pytest.raises(ValueError, match="different"):
        ledger.settle_due(changed)


def test_invalid_quality_flags_are_pending_not_failures(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    prediction = _prediction(ledger)
    _mature(ledger)
    for field, reason in (
        ("fresh", "STALE"),
        ("complete", "INCOMPLETE"),
        ("session_aligned", "SESSION_MISMATCH"),
        ("corporate_action_ok", "CORPORATE_ACTION_CONFLICT"),
    ):
        values = _valid_outcome(prediction).to_dict()
        values[field] = False
        result = ledger.settle_due(OutcomeObservation(**values))
        assert result.matured is False
        assert result.delay_reason == reason
    assert ledger.matured_predictions == 0


def test_twenty_sessions_is_observation_only_and_sixty_requires_manual_approval() -> None:
    contest = ProspectiveContest(champion_id="champion-v1", now=NOW)
    contest.start(
        model_id="challenger",
        model_version="v1",
        config_hash="cfg-v1",
        training_snapshot_hash="train-v1",
        primary_metric="net_cost_return",
        tie_break=("max_drawdown", "brier"),
    )

    metrics = ProspectiveMetrics(net_cost_return=0.1, max_drawdown=0.1, coverage=1.0)
    contest.record_observations(sessions=20, matured=100, gates_pass=True, metrics=metrics)
    assert contest.status == "PROVISIONAL_UNMATURED_OBSERVATION"
    assert contest.can_replace_champion is False
    assert contest.can_issue_approval is False
    contest.record_observations(sessions=60, matured=200, gates_pass=True, metrics=metrics)
    assert contest.status == "AWAITING_MANUAL_APPROVAL"
    assert contest.can_replace_champion is False
    assert contest.can_issue_approval is True
    assert contest.champion_id == "champion-v1"


def test_approval_threshold_waits_for_all_frozen_gates() -> None:
    contest = ProspectiveContest(champion_id="champion-v1", now=NOW)
    contest.start(
        model_id="challenger",
        model_version="v1",
        config_hash="cfg-v1",
        training_snapshot_hash="train-v1",
        primary_metric="net_cost_return",
        tie_break=("max_drawdown", "brier"),
    )
    contest.record_observations(sessions=60, matured=200, gates_pass=False)
    assert contest.status == "PROSPECTIVE_COLLECTING"
    assert contest.can_issue_approval is False


def test_new_version_resets_future_counts_and_metrics_are_immutable_after_start() -> None:
    contest = ProspectiveContest(champion_id="champion-v1", now=NOW)
    contest.start(
        model_id="challenger",
        model_version="v1",
        config_hash="cfg-v1",
        training_snapshot_hash="train-v1",
        primary_metric="net_cost_return",
        tie_break=("max_drawdown", "brier"),
    )
    contest.record_observations(
        sessions=20, matured=100, gates_pass=True,
        metrics=ProspectiveMetrics(net_cost_return=0.1, max_drawdown=0.1, coverage=1.0),
    )
    contest.register_version(
        model_version="v2",
        config_hash="changed",
        training_snapshot_hash="train-v2",
    )
    assert contest.future_sessions == 0
    assert contest.matured_predictions == 0
    with pytest.raises(FrozenContestError):
        contest.change_primary_metric("directional_hit_rate")
    with pytest.raises(FrozenContestError):
        contest.change_tie_break(("brier",))


def test_historical_results_never_qualify_prospective_contest() -> None:
    contest = ProspectiveContest(champion_id="champion-v1", now=NOW)
    contest.start(
        model_id="challenger",
        model_version="v1",
        config_hash="cfg-v1",
        training_snapshot_hash="train-v1",
        primary_metric="net_cost_return",
        tie_break=("max_drawdown", "brier"),
    )
    contest.record_observations(
        sessions=0,
        matured=0,
        gates_pass=False,
        historical_matured=10_000,
    )
    assert contest.status == "PROSPECTIVE_COLLECTING"
    assert contest.future_sessions == 0
    assert contest.matured_predictions == 0


def test_ledger_reloads_append_only_records(tmp_path: Path) -> None:
    path = tmp_path / "prospective-ledger.jsonl"
    first = _ledger(tmp_path)
    prediction = _prediction(first)
    _mature(first)
    first.settle_due(_valid_outcome(prediction))

    restored = ProspectiveCompetition(
        store=ProspectiveLedgerStore(path),
        now=NOW,
    )
    assert restored.read(prediction.id) == prediction
    assert restored.matured_predictions == 1


def test_store_rejects_delete_and_non_project_path(tmp_path: Path) -> None:
    store = ProspectiveLedgerStore(tmp_path / "ledger.jsonl")
    with pytest.raises(ImmutableLedgerError):
        store.delete("anything")


def test_prediction_cannot_be_registered_after_its_cutoff(tmp_path: Path) -> None:
    store = ProspectiveLedgerStore(tmp_path / "ledger.jsonl")
    with pytest.raises(ValueError, match="maturity"):
        ProspectiveCompetition(store=store, now=NOW).append_prediction(
            model_id="m", model_version="v1", config_hash="c", training_snapshot_hash="t",
            symbol="600001", name="示例", prediction_at=NOW,
            as_of=NOW.date(), horizon=5, score=0.1, probability=0.5,
            guidance_price_bands=_bands(), maturity_date=NOW.date(),
        )


def test_store_rejects_orphan_or_mismatched_outcome(tmp_path: Path) -> None:
    store = ProspectiveLedgerStore(tmp_path / "ledger.jsonl")
    outcome = OutcomeObservation(
        prediction_id="missing", symbol="600001", maturity_date=date(2026, 8, 20),
        outcome_at=NOW, realized_price=10, realized_return=0, data_version="v1",
        data_sha256="a" * 64,
    )
    with pytest.raises(KeyError):
        store.append_settlement(outcome)


def test_contest_thresholds_are_frozen_and_metrics_drive_gates() -> None:
    contest = ProspectiveContest(now=NOW)
    with pytest.raises(AttributeError):
        contest.provisional_sessions = 1
    contest.start(model_id="m", model_version="v1", config_hash="c", training_snapshot_hash="t")
    with pytest.raises(ValueError, match="metrics"):
        contest.record_observations(sessions=60, matured=200, gates_pass=True)
    metrics = ProspectiveMetrics(net_cost_return=0.1, max_drawdown=0.1, brier=0.1, coverage=1.0)
    contest.record_observations(sessions=60, matured=200, metrics=metrics)
    assert contest.status == "AWAITING_MANUAL_APPROVAL"


def test_competition_binds_predictions_to_started_contest(tmp_path: Path) -> None:
    contest = ProspectiveContest(now=NOW)
    contest.start(model_id="m", model_version="v1", config_hash="c", training_snapshot_hash="t")
    ledger = ProspectiveLedgerStore(tmp_path / "ledger.jsonl")
    competition = ProspectiveCompetition(store=ledger, contest=contest, now=NOW)
    with pytest.raises(ValueError, match="contest"):
        competition.append_prediction(
            model_id="other", model_version="v1", config_hash="c", training_snapshot_hash="t",
            symbol="600001", name="示例", prediction_at=NOW - timedelta(minutes=1),
            as_of=NOW.date(), horizon=5, score=0.1, probability=0.5,
            guidance_price_bands=_bands(),
        )


def test_calendar_requires_exact_horizon_and_rejects_cutoff_or_late_binding(tmp_path: Path) -> None:
    calendar = [date(2026, 8, 13), date(2026, 8, 14), date(2026, 8, 17), date(2026, 8, 18)]
    store = ProspectiveLedgerStore(tmp_path / "ledger.jsonl")
    competition = ProspectiveCompetition(
        store=store, now=datetime(2026, 8, 12, 8, 0, tzinfo=UTC), session_calendar=calendar
    )
    with pytest.raises(ValueError, match="horizon|insufficient"):
        competition.append_prediction(
            model_id="m", model_version="v1", config_hash="c", training_snapshot_hash="t",
            symbol="600001", name="示例", prediction_at=datetime(2026, 8, 12, 8, 0, tzinfo=UTC),
            as_of=date(2026, 8, 12), horizon=5, score=0.1, probability=0.5,
            guidance_price_bands=_bands(), maturity_date=date(2026, 8, 18),
        )
    with pytest.raises(ValueError, match="cutoff"):
        ProspectiveCompetition(store=store, now=NOW, session_calendar=calendar).append_prediction(
            model_id="m", model_version="v1", config_hash="c", training_snapshot_hash="t",
            symbol="600001", name="示例", prediction_at=datetime(2026, 8, 12, 8, 0, tzinfo=UTC),
            as_of=date(2026, 8, 12), horizon=5, score=0.1, probability=0.5,
            guidance_price_bands=_bands(), maturity_date=date(2026, 8, 20),
        )


def test_pending_quality_record_never_enters_settlements_or_metrics(tmp_path: Path) -> None:
    competition = _ledger(tmp_path)
    prediction = _prediction(competition)
    _mature(competition)
    pending = OutcomeObservation(**{**_valid_outcome(prediction).to_dict(), "status": "SUSPENDED"})
    competition.settle_due(pending)
    assert competition.store.settlements() == ()
    assert competition.compute_metrics().coverage == 0
    competition.store.append_pending(pending, "SUSPENDED")
    competition.store.append_settlement(_valid_outcome(prediction))
    assert competition.store.pending() == ()


def test_valid_unterminated_tail_is_recovered_but_middle_corruption_fails(tmp_path: Path) -> None:
    competition = _ledger(tmp_path)
    prediction = _prediction(competition)
    path = competition.store.path
    with path.open("ab") as handle:
        handle.write(b'{"kind":"crash"')
    restored = ProspectiveLedgerStore(path)
    assert restored.read(prediction.id) == prediction
    with path.open("ab") as handle:
        handle.write(b'\nnot-json\n')
    with pytest.raises(Exception, match="完整性"):
        ProspectiveLedgerStore(path)


def test_explicit_maturity_requires_calendar_and_storage_rejects_early_settlement(
    tmp_path: Path,
) -> None:
    store = ProspectiveLedgerStore(tmp_path / "ledger.jsonl")
    competition = ProspectiveCompetition(store=store, now=NOW)
    with pytest.raises(ValueError, match="calendar"):
        competition.append_prediction(
            model_id="m", model_version="v1", config_hash="c", training_snapshot_hash="t",
            symbol="600001", name="示例", prediction_at=NOW - timedelta(days=1),
            as_of=date(2026, 8, 19), horizon=5, score=0.1, probability=0.5,
            guidance_price_bands=_bands(), maturity_date=date(2026, 8, 26),
        )
    ledger = _ledger(tmp_path)
    prediction = _prediction(ledger)
    early = _valid_outcome(prediction)
    early = OutcomeObservation(**{**early.to_dict(), "outcome_at": NOW.isoformat()})
    with pytest.raises(ValueError, match="maturity"):
        ledger.store.append_settlement(early)
    _mature(ledger)
    ledger.store.append_settlement(_valid_outcome(prediction))
    with pytest.raises(ValueError, match="settlement"):
        ledger.store.append_pending(_valid_outcome(prediction), "STALE")


def test_preconstructed_explicit_maturity_requires_calendar(tmp_path: Path) -> None:
    prediction = ProspectivePrediction(
        model_id="m", model_version="v1", config_hash="c", training_snapshot_hash="t",
        symbol="600001", name="示例", prediction_at=datetime(2026, 8, 19, 8, tzinfo=UTC),
        as_of=date(2026, 8, 19), horizon=5, score=0.1, probability=0.5,
        guidance_price_bands=_bands(), maturity_date=date(2026, 8, 26),
    )
    competition = ProspectiveCompetition(
        store=ProspectiveLedgerStore(tmp_path / "ledger.jsonl"), now=NOW
    )
    with pytest.raises(ValueError, match="calendar"):
        competition.append_prediction(prediction)
