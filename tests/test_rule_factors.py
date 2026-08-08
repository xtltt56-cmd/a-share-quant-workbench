from pathlib import Path

import numpy as np
import pandas as pd

from a_share_quant.features.rule_factors import RuleFactorEngine
from a_share_quant.signals.rule import RuleBasedSignalProvider


def _features() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["000001", "000002", "000003", "000004", "000005"],
            "money_flow": [5, 4, 3, 2, 1],
            "momentum": [5, 4, 3, 2, 1],
            "trend": [1, 2, 3, 4, 5],
            "relative_strength": [5, 4, 3, 2, 1],
            "volume_turnover": [1, 2, 3, 4, 5],
            "quality": [5, 5, 4, 3, 2],
            "valuation": [1, 2, 3, 4, 5],
            "volatility_risk": [5, 4, 3, 2, 1],
        }
    )


def test_rule_factor_engine_reads_yaml_weights_and_outputs_0_to_100_scores() -> None:
    engine = RuleFactorEngine.from_yaml(Path("config/strategy.yaml"))

    scored = engine.score(_features())

    assert engine.config.weights["momentum"] == 0.15
    assert scored["rule_score"].between(0, 100).all()
    assert scored["factor_momentum"].between(0, 100).all()
    assert scored["factor_volatility_risk"].between(0, 100).all()
    assert scored.loc[0, "factor_momentum"] > scored.loc[4, "factor_momentum"]
    assert scored.loc[0, "factor_volatility_risk"] < scored.loc[4, "factor_volatility_risk"]
    assert scored["feature_version"].eq("rule_features_v1").all()
    assert engine.snapshot_metadata["config_hash"]


def test_missing_money_flow_renormalizes_available_weights() -> None:
    features = _features().drop(columns=["money_flow"])
    features.loc[2, "quality"] = np.nan
    engine = RuleFactorEngine.from_yaml(Path("config/strategy.yaml"))

    scored = engine.score(features)

    assert scored["rule_score"].notna().all()
    assert scored["factor_money_flow"].isna().all()
    assert scored.loc[2, "factor_quality"] != scored.loc[2, "factor_quality"]


def test_rule_provider_emits_the_same_versioned_signal_contract() -> None:
    provider = RuleBasedSignalProvider(
        RuleFactorEngine.from_yaml(Path("config/strategy.yaml"))
    )

    records = provider.generate_signals(
        _features(),
        signal_date=pd.Timestamp("2026-08-07").date(),
        experiment_id="exp-rule",
    )

    assert len(records) == 5
    assert records[0].strategy_id == "rule_multifactor"
    assert records[0].model_version == "rule_none_v1"
    assert [record.rank for record in records] == [1, 2, 3, 4, 5]
