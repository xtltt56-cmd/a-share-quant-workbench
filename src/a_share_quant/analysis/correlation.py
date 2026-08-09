"""Cross-model score, rank, overlap, and portfolio-return correlations."""

from __future__ import annotations

from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd


def _date_column(frame: pd.DataFrame) -> str:
    if "signal_date" in frame.columns:
        return "signal_date"
    if "date" in frame.columns:
        return "date"
    raise ValueError("signals require signal_date or date")


def _corr(left: pd.Series, right: pd.Series) -> float:
    value = left.corr(right)
    return float(value) if pd.notna(value) else float("nan")


def _pair_top_k(
    frame: pd.DataFrame, date_column: str, left: str, right: str, top_k: int
) -> list[float]:
    overlaps: list[float] = []
    for _, current in frame.groupby(date_column, sort=True):
        left_top = set(
            current.nlargest(top_k, f"score_{left}")["symbol"].astype(str).tolist()
        )
        right_top = set(
            current.nlargest(top_k, f"score_{right}")["symbol"].astype(str).tolist()
        )
        denominator = min(top_k, len(left_top), len(right_top))
        if denominator:
            overlaps.append(len(left_top & right_top) / denominator)
    return overlaps


def compare_signal_models(predictions: pd.DataFrame, *, top_k: int = 20) -> list[dict[str, Any]]:
    """Return deterministic pairwise model correlation summaries."""

    if top_k < 1:
        raise ValueError("top_k must be positive")
    date_column = _date_column(predictions)
    required = {date_column, "symbol", "strategy_id", "raw_score"}
    missing = sorted(required.difference(predictions.columns))
    if missing:
        raise ValueError(f"predictions are missing fields: {', '.join(missing)}")
    frame = predictions.copy()
    frame["raw_score"] = pd.to_numeric(frame["raw_score"], errors="coerce")
    frame = frame.dropna(subset=[date_column, "symbol", "strategy_id", "raw_score"])
    strategies = sorted(frame["strategy_id"].astype(str).unique())
    results: list[dict[str, Any]] = []
    for left, right in combinations(strategies, 2):
        left_frame = frame[frame["strategy_id"].astype(str) == left][
            [date_column, "symbol", "raw_score"]
        ].rename(columns={"raw_score": f"score_{left}"})
        right_frame = frame[frame["strategy_id"].astype(str) == right][
            [date_column, "symbol", "raw_score"]
        ].rename(columns={"raw_score": f"score_{right}"})
        merged = left_frame.merge(right_frame, on=[date_column, "symbol"], how="inner")
        if merged.empty:
            continue
        score_correlation = _corr(merged[f"score_{left}"], merged[f"score_{right}"])
        rank_values: list[float] = []
        for _, current in merged.groupby(date_column, sort=True):
            if len(current) >= 2:
                rank_values.append(
                    _corr(
                        current[f"score_{left}"].rank(method="average"),
                        current[f"score_{right}"].rank(method="average"),
                    )
                )
        overlaps = _pair_top_k(
            merged.assign(
                **{
                    f"score_{left}": merged[f"score_{left}"],
                    f"score_{right}": merged[f"score_{right}"],
                }
            ),
            date_column,
            left,
            right,
            top_k,
        )
        result: dict[str, Any] = {
            "left_strategy": left,
            "right_strategy": right,
            "score_correlation": score_correlation,
            "rank_correlation": float(np.nanmean(rank_values)) if rank_values else float("nan"),
            "top_k": top_k,
            "top_k_overlap": float(np.nanmean(overlaps)) if overlaps else float("nan"),
            "top20_overlap": float(np.nanmean(overlaps)) if overlaps and top_k == 20 else None,
            "shared_sample_count": int(len(merged)),
        }
        if "portfolio_return" in predictions.columns:
            returns = predictions[[date_column, "strategy_id", "portfolio_return"]].copy()
            returns["strategy_id"] = returns["strategy_id"].astype(str)
            returns["portfolio_return"] = pd.to_numeric(
                returns["portfolio_return"], errors="coerce"
            )
            wide = returns[returns["strategy_id"].isin([left, right])].pivot_table(
                index=date_column, columns="strategy_id", values="portfolio_return", aggfunc="mean"
            )
            result["portfolio_return_correlation"] = (
                _corr(wide[left], wide[right]) if left in wide and right in wide else float("nan")
            )
        else:
            result["portfolio_return_correlation"] = float("nan")
        results.append(result)
    return results
