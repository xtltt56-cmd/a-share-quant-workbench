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
        prediction_at=NOW - timedelta(minutes=1),
        as_of=date(2026, 8, 13),
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
        outcome_at=NOW,
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
            as_of=date(2026, 8, 13),
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
            as_of=date(2026, 8, 13),
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
    delayed = OutcomeObservation(
        prediction_id=prediction.id,
        symbol=prediction.symbol,
        maturity_date=prediction.maturity_date,
        outcome_at=NOW,
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
    outcome = _valid_outcome(prediction)

    result = ledger.settle_due(outcome)
    repeated = ledger.settle_due(outcome)

    assert result.matured is True
    assert result.outcome == outcome
    assert repeated.matured is True
    assert repeated.idempotent is True
    assert ledger.matured_predictions == 1
    changed = OutcomeObservation(**{**outcome.to_dict(), "realized_return": 0.09})
    with pytest.raises(ValueError, match="different"):
        ledger.settle_due(changed)


def test_invalid_quality_flags_are_pending_not_failures(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    prediction = _prediction(ledger)
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

    contest.record_observations(sessions=20, matured=100, gates_pass=True)
    assert contest.status == "PROVISIONAL_UNMATURED_OBSERVATION"
    assert contest.can_replace_champion is False
    assert contest.can_issue_approval is False
    contest.record_observations(sessions=60, matured=200, gates_pass=True)
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
    contest.record_observations(sessions=20, matured=100, gates_pass=True)
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
        gates_pass=True,
        historical_matured=10_000,
    )
    assert contest.status == "PROSPECTIVE_COLLECTING"
    assert contest.future_sessions == 0
    assert contest.matured_predictions == 0


def test_ledger_reloads_append_only_records(tmp_path: Path) -> None:
    path = tmp_path / "prospective-ledger.jsonl"
    first = _ledger(tmp_path)
    prediction = _prediction(first)
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
