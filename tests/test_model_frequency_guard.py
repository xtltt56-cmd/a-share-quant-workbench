from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from a_share_quant.features.rule_factors import RuleFactorEngine
from a_share_quant.signals.frequency import (
    ModelFrequency,
    ModelFrequencyError,
    ensure_model_frequency,
)
from a_share_quant.signals.rule import RuleBasedSignalProvider


def test_daily_model_rejects_intraday_input() -> None:
    with pytest.raises(ModelFrequencyError, match="DAILY"):
        ensure_model_frequency(ModelFrequency.DAILY, "1m")


def test_intraday_model_accepts_matching_frequency() -> None:
    assert ensure_model_frequency(ModelFrequency.INTRADAY_5M, "5m") is True


def test_unknown_frequency_is_rejected() -> None:
    with pytest.raises(ModelFrequencyError, match="frequency"):
        ensure_model_frequency("daily", "2m")


def test_rule_baseline_rejects_intraday_invocation() -> None:
    provider = RuleBasedSignalProvider(
        RuleFactorEngine.from_yaml(Path("config/strategy.yaml"))
    )

    with pytest.raises(ModelFrequencyError, match="DAILY"):
        provider.generate_signals(
            # The frequency guard runs before the frame is read.
            feature_frame=pd.DataFrame(),
            signal_date=date(2026, 8, 10),
            experiment_id="frequency-test",
            data_frequency="1m",
        )
