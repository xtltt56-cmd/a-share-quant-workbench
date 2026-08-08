from datetime import date

import pytest

from a_share_quant.signals.schema import SignalRecord


def test_signal_record_contains_common_version_and_score_fields() -> None:
    record = SignalRecord(
        signal_date=date(2026, 8, 8),
        symbol="000001",
        strategy_id="rule_multifactor",
        strategy_version="rule_multifactor_v1",
        raw_score=1.2,
        normalized_score=87.5,
        rank=3,
        confidence=None,
        model_version="rule_none_v1",
        feature_version="rule_features_v1",
        data_version="canonical-v1",
        experiment_id="exp-test",
    )

    assert record.symbol == "000001"
    assert record.normalized_score == 87.5
    assert record.to_dict()["signal_date"] == date(2026, 8, 8)


def test_signal_record_rejects_invalid_normalized_scores() -> None:
    with pytest.raises(ValueError, match="normalized_score"):
        SignalRecord(
            signal_date=date(2026, 8, 8),
            symbol="000001",
            strategy_id="rule_multifactor",
            strategy_version="rule_multifactor_v1",
            raw_score=1.2,
            normalized_score=101,
            rank=1,
            confidence=None,
            model_version="rule_none_v1",
            feature_version="rule_features_v1",
            data_version="canonical-v1",
            experiment_id="exp-test",
        )
