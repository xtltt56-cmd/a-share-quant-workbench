"""Stable candidate-strategy registry used by reports and future adapters."""

from __future__ import annotations

from .candidates import RankWeighted, TopKDropout, TopKEqualWeight, TopKScoreWeight


def candidate_strategy_factories() -> dict[str, type]:
    return {
        "topk_equal_weight": TopKEqualWeight,
        "topk_score_weight": TopKScoreWeight,
        "rank_weighted": RankWeighted,
        "topk_dropout": TopKDropout,
    }


def build_candidate_strategies() -> dict[str, object]:
    return {name: factory() for name, factory in candidate_strategy_factories().items()}
