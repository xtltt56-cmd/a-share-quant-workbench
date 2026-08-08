"""Config-driven, cross-sectional rule baseline factors."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from a_share_quant.contracts.data import DataValidationError

RULE_FACTOR_NAMES = (
    "money_flow",
    "momentum",
    "trend",
    "relative_strength",
    "volume_turnover",
    "quality",
    "valuation",
    "volatility_risk",
)


@dataclass(frozen=True)
class RuleFactorConfig:
    weights: dict[str, float]
    directions: dict[str, int]
    winsorize_quantiles: tuple[float, float] = (0.01, 0.99)
    lower_bound: float = 0.0
    upper_bound: float = 100.0

    def __post_init__(self) -> None:
        if set(self.weights) != set(RULE_FACTOR_NAMES):
            raise ValueError("rule factor configuration must define all required factors")
        if any(weight < 0 for weight in self.weights.values()):
            raise ValueError("rule factor weights cannot be negative")
        if not np.isclose(sum(self.weights.values()), 1.0):
            raise ValueError("rule factor weights must sum to 1")
        if set(self.directions) != set(RULE_FACTOR_NAMES):
            raise ValueError("rule factor configuration must define all factor directions")
        if any(direction not in {-1, 1} for direction in self.directions.values()):
            raise ValueError("factor directions must be positive or negative")
        lower, upper = self.winsorize_quantiles
        if not 0 <= lower < upper <= 1:
            raise ValueError("winsorize quantiles must be between 0 and 1")


class RuleFactorEngine:
    """Compute a fixed-weight score without parameter search or future data."""

    def __init__(
        self,
        config: RuleFactorConfig,
        *,
        feature_version: str = "rule_features_v1",
        data_version: str = "canonical-v1",
    ) -> None:
        self.config = config
        self.feature_version = feature_version
        self.data_version = data_version
        canonical = json.dumps(
            {
                "weights": config.weights,
                "directions": config.directions,
                "winsorize_quantiles": config.winsorize_quantiles,
            },
            sort_keys=True,
        ).encode("utf-8")
        self._config_hash = sha256(canonical).hexdigest()

    @classmethod
    def from_yaml(cls, path: Path) -> RuleFactorEngine:
        with Path(path).open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        strategy = raw.get("strategy", {})
        factors = strategy.get("factors", {})
        score = strategy.get("score", {})
        weights = {name: float(factors[name]["weight"]) for name in RULE_FACTOR_NAMES}
        directions = {
            name: 1 if str(factors[name].get("direction", "positive")) == "positive" else -1
            for name in RULE_FACTOR_NAMES
        }
        quantiles = tuple(float(value) for value in score.get("winsorize_quantiles", (0.01, 0.99)))
        return cls(
            RuleFactorConfig(
                weights=weights,
                directions=directions,
                winsorize_quantiles=quantiles,  # type: ignore[arg-type]
                lower_bound=float(score.get("lower_bound", 0)),
                upper_bound=float(score.get("upper_bound", 100)),
            )
        )

    @property
    def snapshot_metadata(self) -> dict[str, str]:
        return {
            "feature_version": self.feature_version,
            "data_version": self.data_version,
            "config_hash": self._config_hash,
            "factor_engine": "cross_sectional_winsorized_zscore_v1",
        }

    def score(self, frame: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise DataValidationError("rule factor input must be a non-empty DataFrame")
        if "symbol" not in frame.columns:
            raise DataValidationError("rule factor input requires symbol")
        result = frame.copy()
        factor_scores: dict[str, pd.Series] = {}
        for factor in RULE_FACTOR_NAMES:
            source = (
                factor
                if factor in result.columns
                else "value"
                if factor == "valuation"
                else None
            )
            if source is None:
                factor_scores[factor] = pd.Series(np.nan, index=result.index, dtype="float64")
            else:
                factor_scores[factor] = _standardize_factor(
                    result[source],
                    direction=self.config.directions[factor],
                    quantiles=self.config.winsorize_quantiles,
                )
            result[f"factor_{factor}"] = factor_scores[factor]

        weighted = pd.DataFrame(factor_scores, index=result.index)
        weights = pd.Series(self.config.weights)
        available_weights = weighted.notna().mul(weights, axis="columns")
        denominator = available_weights.sum(axis="columns")
        numerator = weighted.fillna(0).mul(weights, axis="columns").sum(axis="columns")
        result["rule_score"] = (numerator / denominator).clip(
            self.config.lower_bound,
            self.config.upper_bound,
        )
        result["feature_version"] = self.feature_version
        result["data_version"] = self.data_version
        result["config_hash"] = self._config_hash
        return result


def _standardize_factor(
    values: pd.Series,
    *,
    direction: int,
    quantiles: tuple[float, float],
) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=values.index, dtype="float64")
    lower, upper = valid.quantile(quantiles[0]), valid.quantile(quantiles[1])
    clipped = numeric.clip(lower=lower, upper=upper)
    mean = clipped.mean()
    std = clipped.std(ddof=0)
    z_score = pd.Series(0.0, index=values.index, dtype="float64")
    if std > 0:
        z_score = (clipped - mean) / std
    z_score = z_score * direction
    z_score.loc[numeric.isna()] = np.nan
    return (50 + 25 * z_score.clip(-2, 2)).clip(0, 100)
