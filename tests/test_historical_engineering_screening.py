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
                }
            )
            label_rows.append(
                {
                    "symbol": symbol,
                    "date": current,
                    "forward_excess_return_5": 0.01 if symbol_index == 0 else -0.002,
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
