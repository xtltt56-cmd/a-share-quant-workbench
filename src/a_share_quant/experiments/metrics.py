"""Comparable prediction, portfolio, and yearly-stability metrics."""

from __future__ import annotations

from math import sqrt
from typing import Any

import numpy as np
import pandas as pd


def compute_comparison_metrics(
    predictions: pd.DataFrame,
    equity: pd.DataFrame,
    *,
    top_k: int,
) -> dict[str, Any]:
    if top_k < 1:
        raise ValueError("top_k must be positive")
    label_column = _label_column(predictions)
    excess_column = "forward_excess_return" if "forward_excess_return" in predictions else None
    if excess_column is None and "forward_excess_return_5d" in predictions:
        excess_column = "forward_excess_return_5d"
    cross_section = _cross_section_metrics(
        predictions,
        label_column=label_column,
        excess_column=excess_column,
        top_k=top_k,
    )
    portfolio = _portfolio_metrics(equity)
    stability = _yearly_stability(predictions, label_column=label_column, top_k=top_k)
    return {**cross_section, **portfolio, "stability_by_year": stability}


def _cross_section_metrics(
    predictions: pd.DataFrame,
    *,
    label_column: str,
    excess_column: str | None,
    top_k: int,
) -> dict[str, float]:
    ics: list[float] = []
    rank_ics: list[float] = []
    top_returns: list[float] = []
    excess_returns: list[float] = []
    hits: list[float] = []
    for _, group in predictions.groupby("signal_date", sort=True):
        current = group.dropna(subset=["raw_score", label_column]).sort_values(
            "raw_score", ascending=False, kind="stable"
        )
        if len(current) < 2:
            continue
        ics.append(_corr(current["raw_score"], current[label_column]))
        rank_ics.append(_corr(current["raw_score"].rank(), current[label_column].rank()))
        top = current.head(top_k)
        top_returns.append(float(top[label_column].mean()))
        if excess_column is not None:
            excess_returns.append(float(top[excess_column].mean()))
        else:
            excess_returns.append(float(top[label_column].mean()))
        hits.append(float((top[label_column] > 0).mean()))
    return {
        "ic": _mean_or_nan(ics),
        "rank_ic": _mean_or_nan(rank_ics),
        "icir": _ratio(_mean_or_nan(ics), _std_or_nan(ics)),
        "top_k_return": _mean_or_nan(top_returns),
        "excess_return": _mean_or_nan(excess_returns),
        "hit_rate": _mean_or_nan(hits),
    }


def _portfolio_metrics(equity: pd.DataFrame) -> dict[str, float]:
    if equity.empty or not {"date", "equity"}.issubset(equity.columns):
        raise ValueError("equity requires date and equity columns")
    frame = equity.copy().sort_values("date", kind="stable")
    values = pd.to_numeric(frame["equity"], errors="coerce")
    if values.isna().any() or (values <= 0).any():
        raise ValueError("equity must contain positive finite values")
    returns = values.pct_change().fillna(0.0)
    drawdown = values / values.cummax() - 1
    duration_days = max(
        (pd.to_datetime(frame["date"]).iloc[-1] - pd.to_datetime(frame["date"]).iloc[0]).days,
        1,
    )
    cagr = float((values.iloc[-1] / values.iloc[0]) ** (365.25 / duration_days) - 1)
    downside = returns.where(returns < 0, 0.0)
    downside_deviation = float(np.sqrt(np.mean(np.square(downside))))
    std = float(returns.std(ddof=1))
    sharpe = _ratio(float(returns.mean()) * sqrt(252), std * sqrt(252))
    sortino = _ratio(float(returns.mean()) * sqrt(252), downside_deviation * sqrt(252))
    max_drawdown = float(drawdown.min())
    turnover = (
        float(pd.to_numeric(frame["turnover"], errors="coerce").mean())
        if "turnover" in frame
        else float("nan")
    )
    positive = returns[returns > 0]
    negative = returns[returns < 0]
    profit_factor = _ratio(float(positive.sum()), abs(float(negative.sum())))
    payoff = _ratio(float(positive.mean()), abs(float(negative.mean())))
    result = {
        "cumulative_return": float(values.iloc[-1] / values.iloc[0] - 1),
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "calmar": _ratio(cagr, abs(max_drawdown)),
        "turnover": turnover,
        "win_rate": float((returns > 0).mean()),
        "profit_factor": profit_factor,
        "payoff_ratio": payoff,
        "max_consecutive_loss": float(_max_consecutive(returns < 0)),
    }
    if "benchmark_equity" in frame:
        benchmark = pd.to_numeric(frame["benchmark_equity"], errors="coerce")
        result["benchmark_cumulative_return"] = float(benchmark.iloc[-1] / benchmark.iloc[0] - 1)
        result["benchmark_excess_return"] = (
            result["cumulative_return"] - result["benchmark_cumulative_return"]
        )
    return result


def _yearly_stability(
    predictions: pd.DataFrame,
    *,
    label_column: str,
    top_k: int,
) -> dict[str, dict[str, float]]:
    frame = predictions.copy()
    excess_column = "forward_excess_return" if "forward_excess_return" in frame else None
    if excess_column is None and "forward_excess_return_5d" in frame:
        excess_column = "forward_excess_return_5d"
    frame["year"] = pd.to_datetime(frame["signal_date"]).dt.year.astype(str)
    stability: dict[str, dict[str, float]] = {}
    for year, group in frame.groupby("year", sort=True):
        metrics = _cross_section_metrics(
            group,
            label_column=label_column,
            excess_column=excess_column,
            top_k=top_k,
        )
        stability[str(year)] = {
            key: metrics[key]
            for key in ("ic", "rank_ic", "top_k_return", "excess_return", "hit_rate")
        }
    return stability


def _label_column(frame: pd.DataFrame) -> str:
    for candidate in ("forward_return", "forward_excess_return", "forward_excess_return_5d"):
        if candidate in frame.columns:
            return candidate
    raise ValueError("predictions require a forward return label")


def _corr(left: pd.Series, right: pd.Series) -> float:
    value = left.corr(right)
    return float(value) if pd.notna(value) else float("nan")


def _mean_or_nan(values: list[float]) -> float:
    return float(np.nanmean(values)) if values and not np.isnan(values).all() else float("nan")


def _std_or_nan(values: list[float]) -> float:
    return float(np.nanstd(values, ddof=1)) if len(values) > 1 else float("nan")


def _ratio(numerator: float, denominator: float) -> float:
    if not np.isfinite(numerator) or not np.isfinite(denominator) or denominator == 0:
        return float("nan")
    return numerator / denominator


def _max_consecutive(condition: pd.Series) -> int:
    longest = current = 0
    for value in condition.tolist():
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest
