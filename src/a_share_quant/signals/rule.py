"""Rule-based multi-factor signal provider."""

from __future__ import annotations

from datetime import date

import pandas as pd

from a_share_quant.features.rule_factors import RuleFactorEngine

from .adapter import prediction_frame_to_records
from .frequency import ModelFrequency, ensure_model_frequency
from .schema import SignalRecord


class RuleBasedSignalProvider:
    strategy_id = "rule_multifactor"
    frequency = ModelFrequency.DAILY

    def __init__(
        self,
        engine: RuleFactorEngine,
        *,
        strategy_version: str = "rule_multifactor_v1",
        model_version: str = "rule_none_v1",
    ) -> None:
        self.engine = engine
        self.strategy_version = strategy_version
        self.model_version = model_version

    def generate_signals(
        self,
        feature_frame: pd.DataFrame,
        *,
        signal_date: date,
        experiment_id: str,
        data_frequency: str = "daily",
    ) -> list[SignalRecord]:
        ensure_model_frequency(self.frequency, data_frequency)
        scored = self.engine.score(feature_frame)
        predictions = scored.loc[:, ["symbol", "rule_score"]].rename(
            columns={"rule_score": "raw_score"}
        )
        predictions["signal_date"] = signal_date
        predictions["strategy_id"] = self.strategy_id
        predictions["strategy_version"] = self.strategy_version
        predictions["model_version"] = self.model_version
        predictions["feature_version"] = self.engine.feature_version
        return prediction_frame_to_records(
            predictions,
            data_version=self.engine.data_version,
            experiment_id=experiment_id,
        )
