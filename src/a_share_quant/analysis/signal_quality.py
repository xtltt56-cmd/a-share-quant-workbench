"""Out-of-sample signal-quality metrics with no model fitting or optimization."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd

from .correlation import compare_signal_models
from .regime import MarketRegimeProvider


def _label_column(frame: pd.DataFrame, requested: str | None) -> str:
    if requested is not None:
        if requested not in frame.columns:
            raise ValueError(f"label column not found: {requested}")
        return requested
    for candidate in ("forward_return", "forward_excess_return", "forward_excess_return_5d"):
        if candidate in frame.columns:
            return candidate
    raise ValueError("predictions require a forward return label")


def _date_column(frame: pd.DataFrame) -> str:
    if "signal_date" in frame.columns:
        return "signal_date"
    if "date" in frame.columns:
        return "date"
    raise ValueError("predictions require signal_date or date")


def _safe_corr(left: pd.Series, right: pd.Series) -> float:
    value = left.corr(right)
    return float(value) if pd.notna(value) else float("nan")


def _safe_mean(values: list[float]) -> float:
    return float(np.nanmean(values)) if values and not np.isnan(values).all() else float("nan")


def _safe_std(values: list[float]) -> float:
    return float(np.nanstd(values, ddof=1)) if len(values) > 1 else float("nan")


def _information_ratio(mean: float, standard_deviation: float) -> float:
    if not np.isfinite(mean) or not np.isfinite(standard_deviation):
        return float("nan")
    if standard_deviation == 0:
        return float("inf") if mean > 0 else float("-inf") if mean < 0 else 0.0
    return mean / standard_deviation


def _ic_summary(daily: pd.DataFrame, *, sample_count: int) -> dict[str, float | int]:
    ics = daily["ic"].dropna().astype(float).tolist()
    rank_ics = daily["rank_ic"].dropna().astype(float).tolist()
    ic_mean = _safe_mean(ics)
    rank_mean = _safe_mean(rank_ics)
    ic_std = _safe_std(ics)
    rank_std = _safe_std(rank_ics)
    return {
        "ic": ic_mean,
        "rank_ic": rank_mean,
        "ic_mean": ic_mean,
        "rank_ic_mean": rank_mean,
        "ic_std": ic_std,
        "rank_ic_std": rank_std,
        "icir": _information_ratio(ic_mean, ic_std),
        "rank_icir": _information_ratio(rank_mean, rank_std),
        "positive_ic_ratio": float(np.mean(np.asarray(ics) > 0)) if ics else float("nan"),
        "positive_rank_ic_ratio": float(np.mean(np.asarray(rank_ics) > 0))
        if rank_ics
        else float("nan"),
        "sample_count": int(sample_count),
        "valid_date_count": int(len(ics)),
    }


def _daily_metrics(
    frame: pd.DataFrame,
    *,
    date_column: str,
    label_column: str,
    min_group_count: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for current_date, group in frame.groupby(date_column, sort=True):
        valid = group.dropna(subset=["raw_score", label_column]).copy()
        if len(valid) < min_group_count:
            continue
        score = pd.to_numeric(valid["raw_score"], errors="coerce")
        label = pd.to_numeric(valid[label_column], errors="coerce")
        valid = valid.loc[score.notna() & label.notna()]
        if len(valid) < min_group_count:
            continue
        score = pd.to_numeric(valid["raw_score"], errors="coerce")
        label = pd.to_numeric(valid[label_column], errors="coerce")
        rows.append(
            {
                "date": current_date,
                "ic": _safe_corr(score, label),
                "rank_ic": _safe_corr(score.rank(method="average"), label.rank(method="average")),
                "sample_count": int(len(valid)),
            }
        )
    return pd.DataFrame(rows, columns=["date", "ic", "rank_ic", "sample_count"])


def _quantile_summary(
    frame: pd.DataFrame,
    *,
    date_column: str,
    label_column: str,
    quantiles: int,
    min_group_count: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for current_date, group in frame.groupby(date_column, sort=True):
        valid = group.dropna(subset=["raw_score", label_column]).copy()
        if len(valid) < min_group_count:
            continue
        valid["quantile"] = (
            valid["raw_score"].rank(method="first")
            .sub(1)
            .mul(quantiles)
            .floordiv(len(valid))
            .add(1)
            .clip(1, quantiles)
            .astype(int)
        )
        for quantile, current in valid.groupby("quantile", sort=True):
            row: dict[str, Any] = {
                "quantile": int(quantile),
                "mean_return": float(current[label_column].mean()),
                "sample_count": int(len(current)),
                "date": current_date,
            }
            if "forward_excess_return" in current.columns:
                row["mean_excess_return"] = float(current["forward_excess_return"].mean())
            rows.append(row)
    table = pd.DataFrame(rows)
    if table.empty:
        return {"rows": [], "monotonicity_score": float("nan")}
    aggregate = table.groupby("quantile", sort=True)["mean_return"].mean()
    monotonicity = _safe_corr(
        pd.Series(aggregate.index, dtype=float), aggregate.reset_index(drop=True).astype(float)
    )
    result_rows = table.groupby("quantile", sort=True).agg(
        mean_return=("mean_return", "mean"), sample_count=("sample_count", "sum")
    ).reset_index()
    if "mean_excess_return" in table.columns:
        excess = table.groupby("quantile", sort=True)["mean_excess_return"].mean()
        result_rows["mean_excess_return"] = result_rows["quantile"].map(excess)
    return {
        "rows": result_rows.to_dict("records"),
        "monotonicity_score": monotonicity,
        "quantile_count": int(len(result_rows)),
    }


def _top_k_summary(
    frame: pd.DataFrame,
    *,
    date_column: str,
    label_column: str,
    top_k: int,
) -> dict[str, Any]:
    returns: list[float] = []
    hits: list[float] = []
    top_sets: list[set[str]] = []
    for _, group in frame.groupby(date_column, sort=True):
        current = group.dropna(subset=["raw_score", label_column]).sort_values(
            ["raw_score", "symbol"], ascending=[False, True], kind="stable"
        )
        if current.empty:
            continue
        top = current.head(top_k)
        returns.append(float(top[label_column].mean()))
        hits.append(float((top[label_column] > 0).mean()))
        if "symbol" in top.columns:
            top_sets.append(set(top["symbol"].astype(str)))
    turnover: list[float] = []
    for previous, current in zip(top_sets, top_sets[1:]):
        denominator = max(len(previous | current), 1)
        turnover.append(1 - len(previous & current) / denominator)
    return {
        "top_k": top_k,
        "mean_return": _safe_mean(returns),
        "hit_rate": _safe_mean(hits),
        "turnover": _safe_mean(turnover),
        "date_count": len(returns),
    }


def _period_summary(
    frame: pd.DataFrame,
    *,
    date_column: str,
    label_column: str,
    min_group_count: int,
    frequency: str,
) -> dict[str, dict[str, float | int]]:
    current = frame.copy()
    current["_period"] = pd.to_datetime(current[date_column]).dt.to_period(frequency).astype(str)
    result: dict[str, dict[str, float | int]] = {}
    for period, group in current.groupby("_period", sort=True):
        daily = _daily_metrics(
            group,
            date_column=date_column,
            label_column=label_column,
            min_group_count=min_group_count,
        )
        result[str(period)] = _ic_summary(
            daily, sample_count=int(group[label_column].notna().sum())
        )
    return result


def _stability(frame: pd.DataFrame, *, date_column: str, top_k: int) -> dict[str, Any]:
    current = frame.copy()
    current["_period"] = pd.to_datetime(current[date_column]).dt.to_period("M").astype(str)
    distributions = current.groupby("_period")["raw_score"].agg(["mean", "std"]).reset_index()
    drift: list[dict[str, Any]] = []
    for previous, following in zip(
        distributions.to_dict("records"), distributions.iloc[1:].to_dict("records")
    ):
        drift.append(
            {
                "from_period": previous["_period"],
                "to_period": following["_period"],
                "mean_delta": float(following["mean"] - previous["mean"]),
                "std_delta": float(following["std"] - previous["std"]),
            }
        )
    rank_stability: list[float] = []
    score_correlation: list[float] = []
    if "symbol" in current.columns:
        current["_rank"] = current.groupby("_period")["raw_score"].rank(
            ascending=False, method="average"
        )
        for left, right in combinations(sorted(current["_period"].unique()), 2):
            pair = current[current["_period"].isin([left, right])].pivot_table(
                index="symbol", columns="_period", values=["_rank", "raw_score"], aggfunc="mean"
            )
            if ("_rank", left) in pair and ("_rank", right) in pair:
                ranks = pair[["_rank"]].dropna()
                if len(ranks) >= 2:
                    rank_stability.append(
                        _safe_corr(ranks[("_rank", left)], ranks[("_rank", right)])
                    )
            if ("raw_score", left) in pair and ("raw_score", right) in pair:
                scores = pair[["raw_score"]].dropna()
                if len(scores) >= 2:
                    score_correlation.append(
                        _safe_corr(scores[("raw_score", left)], scores[("raw_score", right)])
                    )
    return {
        "score_distribution": distributions.to_dict("records"),
        "score_distribution_drift": drift,
        "rank_stability": _safe_mean(rank_stability),
        "cross_period_score_correlation": _safe_mean(score_correlation),
        "period_count": int(distributions.shape[0]),
        "top_k": top_k,
    }


def _group_quality(
    frame: pd.DataFrame,
    *,
    group_column: str,
    date_column: str,
    label_column: str,
    min_group_count: int,
) -> dict[str, dict[str, float | int]]:
    return {
        str(group_value): _ic_summary(
            _daily_metrics(
                group,
                date_column=date_column,
                label_column=label_column,
                min_group_count=min_group_count,
            ),
            sample_count=int(group[label_column].notna().sum()),
        )
        for group_value, group in frame.dropna(subset=[group_column]).groupby(
            group_column, sort=True
        )
    }


@dataclass(frozen=True)
class SignalQualityAnalyzer:
    top_k: int = 20
    quantiles: int = 5
    min_group_count: int = 3
    weak_monotonicity_threshold: float = 0.5

    def __post_init__(self) -> None:
        if self.top_k < 1 or self.quantiles < 2 or self.min_group_count < 2:
            raise ValueError("top_k, quantiles, and min_group_count are invalid")

    def analyze(
        self,
        predictions: pd.DataFrame,
        *,
        label_column: str | None = None,
        benchmark: pd.DataFrame | None = None,
    ) -> dict[str, Any]:
        date_column = _date_column(predictions)
        label = _label_column(predictions, label_column)
        required = {date_column, "symbol", "strategy_id", "raw_score", label}
        missing = sorted(required.difference(predictions.columns))
        if missing:
            raise ValueError(f"predictions are missing fields: {', '.join(missing)}")
        frame = predictions.copy()
        frame[date_column] = pd.to_datetime(frame[date_column], errors="coerce").dt.date
        frame["raw_score"] = pd.to_numeric(frame["raw_score"], errors="coerce")
        frame[label] = pd.to_numeric(frame[label], errors="coerce")
        if benchmark is not None and "regime" not in frame.columns:
            regime = MarketRegimeProvider().classify(benchmark)
            frame = frame.merge(regime, left_on=date_column, right_on="date", how="left")

        models: dict[str, Any] = {}
        for strategy_id, model_frame in frame.groupby("strategy_id", sort=True):
            daily = _daily_metrics(
                model_frame,
                date_column=date_column,
                label_column=label,
                min_group_count=self.min_group_count,
            )
            quantile_result = _quantile_summary(
                model_frame,
                date_column=date_column,
                label_column=label,
                quantiles=self.quantiles,
                min_group_count=self.min_group_count,
            )
            diagnostics: list[str] = []
            monotonicity = quantile_result["monotonicity_score"]
            if not np.isfinite(monotonicity) or monotonicity < self.weak_monotonicity_threshold:
                diagnostics.append("WEAK_SIGNAL_MONOTONICITY")
            if len(daily) < 1:
                diagnostics.append("INSUFFICIENT_SIGNAL_SAMPLE")
            if "regime" in model_frame.columns and model_frame["regime"].notna().any():
                by_regime = _group_quality(
                    model_frame,
                    group_column="regime",
                    date_column=date_column,
                    label_column=label,
                    min_group_count=self.min_group_count,
                )
                regime_metadata = {"available": True, "source": "provided_or_benchmark"}
            else:
                by_regime = {}
                regime_metadata = {"available": False, "reason": "regime data not supplied"}
            if (
                "volatility_regime" in model_frame.columns
                and model_frame["volatility_regime"].notna().any()
            ):
                by_volatility = _group_quality(
                    model_frame,
                    group_column="volatility_regime",
                    date_column=date_column,
                    label_column=label,
                    min_group_count=self.min_group_count,
                )
                volatility_metadata = {"available": True, "source": "provided_or_benchmark"}
            else:
                by_volatility = {}
                volatility_metadata = {
                    "available": False,
                    "reason": "volatility regime data not supplied",
                }
            if "sector" in model_frame.columns and model_frame["sector"].notna().any():
                sector_exposure = (
                    model_frame.dropna(subset=["sector"])
                    .groupby("sector", sort=True)
                    .agg(
                        sample_count=(label, "count"),
                        mean_return=(label, "mean"),
                    )
                    .reset_index()
                    .to_dict("records")
                )
                sector_metadata = {"available": True}
            else:
                sector_exposure = []
                sector_metadata = {"available": False, "reason": "sector data not supplied"}
            models[str(strategy_id)] = {
                "sample_count": int(model_frame[label].notna().sum()),
                "overall": _ic_summary(daily, sample_count=int(model_frame[label].notna().sum())),
                "quantiles": quantile_result,
                "top_k": _top_k_summary(
                    model_frame, date_column=date_column, label_column=label, top_k=self.top_k
                ),
                "by_period": {
                    "month": _period_summary(
                        model_frame,
                        date_column=date_column,
                        label_column=label,
                        min_group_count=self.min_group_count,
                        frequency="M",
                    ),
                    "quarter": _period_summary(
                        model_frame,
                        date_column=date_column,
                        label_column=label,
                        min_group_count=self.min_group_count,
                        frequency="Q",
                    ),
                    "year": _period_summary(
                        model_frame,
                        date_column=date_column,
                        label_column=label,
                        min_group_count=self.min_group_count,
                        frequency="Y",
                    ),
                },
                "by_regime": by_regime,
                "regime_metadata": regime_metadata,
                "by_volatility": by_volatility,
                "volatility_metadata": volatility_metadata,
                "sector_exposure": sector_exposure,
                "sector_metadata": sector_metadata,
                "stability": _stability(model_frame, date_column=date_column, top_k=self.top_k),
                "diagnostics": diagnostics,
            }
        return {
            "analysis_version": "stage3a-v1",
            "label_column": label,
            "top_k": self.top_k,
            "quantiles": self.quantiles,
            "models": models,
            "model_correlation": compare_signal_models(frame, top_k=self.top_k),
        }
