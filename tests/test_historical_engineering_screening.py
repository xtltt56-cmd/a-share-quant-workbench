from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from a_share_quant.research.historical_screening import (
    HistoricalCandidate,
    HistoricalEngineeringScreen,
    ScreeningConfig,
)


def _snapshot(session_count: int = 1750):
    start = date(2019, 1, 1)
    sessions = [start + timedelta(days=index) for index in range(session_count)]
    feature_rows: list[dict[str, object]] = []
    label_rows: list[dict[str, object]] = []
    for session_index, current in enumerate(sessions):
        for symbol_index in range(3):
            symbol = f"{symbol_index + 1:06d}"
            feature_rows.append(
                {
                    "symbol": symbol,
                    "date": current,
                    "available_at": current,
                    "feature_momentum": float((session_index + symbol_index) % 11) / 11,
                    "price": 10.0,
                }
            )
            label_rows.append(
                {
                    "symbol": symbol,
                    "date": current,
                    "forward_excess_return_5": 0.01 if symbol_index == 0 else -0.002,
                    "horizon_days": 5,
                }
            )

    class Snapshot:
        canonical_sha256 = "a" * 64
        signal_cutoff = sessions[-1]

        @property
        def features(self):
            return pd.DataFrame(feature_rows)

        @property
        def labels(self):
            return pd.DataFrame(label_rows)

    return Snapshot()


def _candidates():
    return (
        HistoricalCandidate("rule-baseline", model_family="rule-baseline"),
        HistoricalCandidate("logistic", model_family="sklearn-logistic"),
        HistoricalCandidate("lightgbm", model_family="lightgbm"),
        HistoricalCandidate("qlib", model_family="qlib"),
    )


def test_screen_uses_fixed_purged_windows_and_evaluates_every_candidate():
    result = HistoricalEngineeringScreen().run(_snapshot(), _candidates(), seed=20260814)

    assert result.trial_count == 4
    assert len(result.folds) >= 3
    for fold in result.folds:
        assert fold.train_end < fold.validation_start <= fold.validation_end
        assert fold.validation_end < fold.test_start <= fold.test_end
        assert fold.embargo_sessions == 126
        assert fold.train_sessions == 756
        assert fold.validation_sessions == 252
        assert fold.test_sessions == 126
    assert {trial.candidate_id for trial in result.trials} == {
        "rule-baseline",
        "logistic",
        "lightgbm",
        "qlib",
    }
    assert all(trial.evidence_mode == "NON_PROMOTIONAL_ENGINEERING" for trial in result.trials)


def test_same_frozen_snapshot_candidates_and_seed_are_reproducible():
    first = HistoricalEngineeringScreen().run(_snapshot(), _candidates(), seed=20260814)
    second = HistoricalEngineeringScreen().run(_snapshot(), _candidates(), seed=20260814)

    assert first.canonical_digest == second.canonical_digest
    assert first.to_dict() == second.to_dict()


def test_historical_metrics_never_rank_or_issue_governance():
    result = HistoricalEngineeringScreen().run(_snapshot(), _candidates(), seed=20260814)

    assert result.evidence_mode == "NON_PROMOTIONAL_ENGINEERING"
    assert result.can_rank_models is False
    assert result.can_issue_provisional is False
    assert result.can_issue_approval_token is False
    assert result.champion_id is None
    assert all(trial.governance_state != "HISTORICAL_PASS" for trial in result.trials)


def test_snapshot_is_frozen_and_future_features_are_blocked():
    snapshot = _snapshot()
    frame = snapshot.features
    frame.loc[0, "available_at"] = snapshot.signal_cutoff + timedelta(days=1)

    class ChangedSnapshot:
        canonical_sha256 = snapshot.canonical_sha256
        signal_cutoff = snapshot.signal_cutoff
        features = frame
        labels = snapshot.labels

    result = HistoricalEngineeringScreen().run(ChangedSnapshot(), _candidates()[:1])
    assert result.status == "ENGINEERING_BLOCKED"
    assert any("future" in flag.lower() for flag in result.trials[0].leakage_flags)


def test_short_history_fails_closed_without_lowering_window_requirements():
    with pytest.raises(ValueError, match="至少.*窗口|windows"):
        HistoricalEngineeringScreen().run(_snapshot(1200), _candidates()[:1])


def test_config_matches_governed_windows():
    config = ScreeningConfig()
    assert config.train_sessions == 756
    assert config.validation_sessions == 252
    assert config.embargo_sessions == 126
    assert config.test_sessions == 126
    assert config.minimum_oos_windows == 3
    assert config.evidence_mode == "NON_PROMOTIONAL_ENGINEERING"


def test_custom_scorer_cannot_observe_realized_labels():
    def malicious(frame):
        # A scorer must never receive the outcome column, even in historical
        # engineering mode where the caller has already joined labels.
        assert "_label" not in frame.columns
        assert not any(
            str(column) in {"label", "research_return"}
            or str(column).startswith(("forward_", "excess_return"))
            for column in frame.columns
        )
        return [0.0] * len(frame)

    candidate = HistoricalCandidate("safe-scorer", scorer=malicious)
    result = HistoricalEngineeringScreen().run(_snapshot(), [candidate])

    assert result.trials[0].status == "ENGINEERING_SCREENED"
    assert result.trials[0].leakage_flags == ()


def test_malicious_scorer_that_requires_labels_is_blocked_without_aborting_others():
    def malicious(frame):
        return frame["_label"]

    result = HistoricalEngineeringScreen().run(
        _snapshot(),
        [
            HistoricalCandidate("malicious", scorer=malicious),
            HistoricalCandidate("valid", model_family="rule-baseline"),
        ],
    )

    trials = {trial.candidate_id: trial for trial in result.trials}
    assert trials["malicious"].status == "ENGINEERING_BLOCKED"
    assert trials["valid"].status in {"ENGINEERING_SCREENED", "ENGINEERING_BLOCKED"}
    assert "candidate_execution_error" in trials["malicious"].leakage_flags


def test_missing_score_column_blocks_only_that_candidate():
    result = HistoricalEngineeringScreen().run(
        _snapshot(),
        [
            HistoricalCandidate("missing", score_column="not_present"),
            HistoricalCandidate("valid", model_family="rule-baseline"),
        ],
    )

    trials = {trial.candidate_id: trial for trial in result.trials}
    assert trials["missing"].status == "ENGINEERING_BLOCKED"
    assert "missing_input" in trials["missing"].leakage_flags
    assert trials["valid"].status in {"ENGINEERING_SCREENED", "ENGINEERING_BLOCKED"}


def test_all_realized_label_columns_are_excluded_from_implicit_features():
    snapshot = _snapshot()
    changed_features = snapshot.features.assign(
        label=1.0,
        research_return=1.0,
        excess_return_5=1.0,
        forward_return_5=1.0,
    )

    class ChangedSnapshot:
        canonical_sha256 = snapshot.canonical_sha256
        signal_cutoff = snapshot.signal_cutoff
        labels = snapshot.labels
        features = changed_features

    result = HistoricalEngineeringScreen().run(
        ChangedSnapshot(), [HistoricalCandidate("valid", model_family="rule-baseline")]
    )
    assert result.trials[0].status in {"ENGINEERING_SCREENED", "ENGINEERING_BLOCKED"}


def test_cost_uses_actual_price_and_lot_costs():
    snapshot = _snapshot()
    changed_features = snapshot.features.assign(price=100.0, quantity=100)

    class ChangedSnapshot:
        canonical_sha256 = snapshot.canonical_sha256
        signal_cutoff = snapshot.signal_cutoff
        labels = snapshot.labels
        features = changed_features

    result = HistoricalEngineeringScreen().run(
        ChangedSnapshot(), [HistoricalCandidate("priced", score_column="price")]
    )
    costs = [metric.estimated_cost for metric in result.trials[0].fold_metrics]
    assert costs
    # At a 100 yuan price the minimum commission must not be diluted by
    # pretending that every selected name costs the same 10 yuan.
    assert all(cost > 0.0012 for cost in costs)


def test_nondeterministic_custom_scorer_is_blocked_and_not_reproducible():
    state = {"value": 0}

    def unstable(frame):
        state["value"] += 1
        return [state["value"]] * len(frame)

    result = HistoricalEngineeringScreen().run(
        _snapshot(), [HistoricalCandidate("unstable", scorer=unstable)]
    )
    trial = result.trials[0]
    assert trial.status == "ENGINEERING_BLOCKED"
    assert trial.reproducible is False
    assert "reproducibility_failure" in trial.leakage_flags
    assert trial.canonical_parameters == {}


def test_missing_unadjusted_price_blocks_candidate():
    snapshot = _snapshot()
    changed_features = snapshot.features.drop(columns=["price"])

    class ChangedSnapshot:
        canonical_sha256 = snapshot.canonical_sha256
        signal_cutoff = snapshot.signal_cutoff
        labels = snapshot.labels
        features = changed_features

    result = HistoricalEngineeringScreen().run(
        ChangedSnapshot(), [HistoricalCandidate("no-price")]
    )
    trial = result.trials[0]
    assert trial.status == "ENGINEERING_BLOCKED"
    assert "cost_data_unavailable" in trial.leakage_flags


def test_adjusted_close_is_not_used_as_execution_cost_price():
    snapshot = _snapshot()
    changed_features = snapshot.features.drop(columns=["price"]).assign(adj_close=10.0)

    class ChangedSnapshot:
        canonical_sha256 = snapshot.canonical_sha256
        signal_cutoff = snapshot.signal_cutoff
        labels = snapshot.labels
        features = changed_features

    result = HistoricalEngineeringScreen().run(
        ChangedSnapshot(), [HistoricalCandidate("adjusted-only")]
    )
    assert "cost_data_unavailable" in result.trials[0].leakage_flags


def test_unknown_label_maturity_blocks_training_boundary():
    snapshot = _snapshot()
    changed_labels = snapshot.labels.drop(columns=["horizon_days"])

    class ChangedSnapshot:
        canonical_sha256 = snapshot.canonical_sha256
        signal_cutoff = snapshot.signal_cutoff
        labels = changed_labels
        features = snapshot.features

    result = HistoricalEngineeringScreen().run(
        ChangedSnapshot(), [HistoricalCandidate("unknown-maturity")]
    )
    assert "missing_input" in result.trials[0].leakage_flags


def test_horizon_uses_sessions_not_calendar_days_at_weekend_boundary():
    snapshot = _snapshot()
    sessions = pd.to_datetime(snapshot.features["date"]).dt.date
    changed_labels = snapshot.labels.copy()
    changed_labels["horizon_days"] = 5
    changed_labels["date"] = sessions

    class ChangedSnapshot:
        canonical_sha256 = snapshot.canonical_sha256
        signal_cutoff = snapshot.signal_cutoff
        labels = changed_labels
        features = snapshot.features

    result = HistoricalEngineeringScreen().run(
        ChangedSnapshot(), [HistoricalCandidate("session-horizon")]
    )
    assert result.trials[0].status in {"ENGINEERING_SCREENED", "ENGINEERING_BLOCKED"}


def test_illegal_horizon_is_blocked():
    snapshot = _snapshot()
    changed_labels = snapshot.labels.assign(horizon_days=0)

    class ChangedSnapshot:
        canonical_sha256 = snapshot.canonical_sha256
        signal_cutoff = snapshot.signal_cutoff
        labels = changed_labels
        features = snapshot.features

    result = HistoricalEngineeringScreen().run(
        ChangedSnapshot(), [HistoricalCandidate("bad-horizon")]
    )
    assert result.trials[0].status == "ENGINEERING_BLOCKED"


def test_maturity_date_must_be_strictly_after_label_date():
    snapshot = _snapshot()
    changed_labels = snapshot.labels.drop(columns=["horizon_days"]).assign(
        maturity_date=lambda frame: frame["date"]
    )

    class ChangedSnapshot:
        canonical_sha256 = snapshot.canonical_sha256
        signal_cutoff = snapshot.signal_cutoff
        labels = changed_labels
        features = snapshot.features

    result = HistoricalEngineeringScreen().run(
        ChangedSnapshot(), [HistoricalCandidate("same-day-maturity")]
    )
    assert result.trials[0].status == "ENGINEERING_BLOCKED"
