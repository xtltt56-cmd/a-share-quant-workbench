"""Deterministic candidate portfolio strategies for fast research."""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

from a_share_quant.contracts.stage3 import ExecutionSpec, PortfolioTarget, SignalFrame
from a_share_quant.data.normalization import normalize_symbol

from .contracts import PortfolioSpec


def _as_frame(signal_frame: SignalFrame | pd.DataFrame) -> pd.DataFrame:
    if isinstance(signal_frame, SignalFrame):
        return signal_frame.to_frame()
    if isinstance(signal_frame, pd.DataFrame):
        return signal_frame.copy()
    raise TypeError("portfolio strategies require a SignalFrame or DataFrame")


def _candidate_frame(
    signal_frame: SignalFrame | pd.DataFrame,
    execution_spec: ExecutionSpec,
    portfolio_spec: PortfolioSpec,
) -> pd.DataFrame:
    frame = _as_frame(signal_frame)
    if "date" not in frame.columns or "symbol" not in frame.columns:
        raise ValueError("signal frame requires date and symbol")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    selected = frame.loc[frame["date"] == execution_spec.signal_date].copy()
    if selected.empty:
        raise ValueError("signal frame has no rows for execution_spec.signal_date")
    if "raw_score" not in selected:
        raise ValueError("signal frame requires raw_score")
    selected["symbol"] = selected["symbol"].map(normalize_symbol)
    selected["raw_score"] = pd.to_numeric(selected["raw_score"], errors="coerce")
    selected = selected.loc[np.isfinite(selected["raw_score"])]
    if "is_suspended" in selected:
        selected = selected.loc[~selected["is_suspended"].fillna(False).astype(bool)]
    if "is_tradable" in selected:
        selected = selected.loc[selected["is_tradable"].fillna(False).astype(bool)]
    selected = selected.dropna(subset=["symbol"]).drop_duplicates("symbol", keep="first")
    selected = selected.sort_values(
        ["raw_score", "symbol"], ascending=[False, True], kind="stable"
    ).reset_index(drop=True)
    limit = min(portfolio_spec.top_k, portfolio_spec.max_positions)
    return selected.head(limit)


def _capped_weights(
    allocation: Mapping[str, float],
    portfolio_spec: PortfolioSpec,
) -> dict[str, float]:
    clean = {
        normalize_symbol(symbol): max(0.0, float(weight))
        for symbol, weight in allocation.items()
        if math.isfinite(float(weight)) and float(weight) > 0
    }
    if not clean or portfolio_spec.target_gross_exposure == 0:
        return {}
    total = sum(clean.values())
    scale = portfolio_spec.target_gross_exposure / total
    return {
        symbol: min(portfolio_spec.max_single_position, weight * scale)
        for symbol, weight in clean.items()
    }


def _targets(
    weights: Mapping[str, float],
    execution_spec: ExecutionSpec,
    strategy_id: str,
    strategy_version: str,
    reason: str,
) -> list[PortfolioTarget]:
    return [
        PortfolioTarget(
            date=execution_spec.execution_date,
            symbol=symbol,
            target_weight=weight,
            source_strategy=f"{strategy_id}:{strategy_version}",
            rebalance_reason=reason,
        )
        for symbol, weight in sorted(weights.items())
        if weight > 0
    ]


class _CandidateStrategy:
    strategy_id = "candidate"
    strategy_version = "stage3b-v1"

    def build_targets(
        self,
        signal_frame: SignalFrame | pd.DataFrame,
        portfolio_spec: PortfolioSpec,
        execution_spec: ExecutionSpec,
        current_positions: Mapping[str, float] | None = None,
    ) -> list[PortfolioTarget]:
        return self.generate_targets(
            signal_frame,
            portfolio_spec,
            execution_spec,
            current_positions=current_positions,
        )


class TopKEqualWeight(_CandidateStrategy):
    strategy_id = "topk_equal_weight"

    def generate_targets(
        self,
        signal_frame: SignalFrame | pd.DataFrame,
        portfolio_spec: PortfolioSpec,
        execution_spec: ExecutionSpec,
        current_positions: Mapping[str, float] | None = None,
    ) -> list[PortfolioTarget]:
        candidates = _candidate_frame(signal_frame, execution_spec, portfolio_spec)
        allocation = {symbol: 1.0 for symbol in candidates["symbol"]}
        return _targets(
            _capped_weights(allocation, portfolio_spec),
            execution_spec,
            self.strategy_id,
            self.strategy_version,
            "top_k_equal_weight",
        )


class TopKScoreWeight(_CandidateStrategy):
    strategy_id = "topk_score_weight"

    def generate_targets(
        self,
        signal_frame: SignalFrame | pd.DataFrame,
        portfolio_spec: PortfolioSpec,
        execution_spec: ExecutionSpec,
        current_positions: Mapping[str, float] | None = None,
    ) -> list[PortfolioTarget]:
        candidates = _candidate_frame(signal_frame, execution_spec, portfolio_spec)
        scores = np.clip(candidates["raw_score"].to_numpy(dtype=float), -1e12, 1e12)
        shifted = scores - float(np.min(scores))
        span = float(np.max(shifted))
        if span <= 1e-12:
            stable_scores = np.ones(len(scores), dtype=float)
        else:
            stable_scores = shifted + max(span * 1e-6, 1e-12)
        allocation = dict(zip(candidates["symbol"], stable_scores, strict=True))
        return _targets(
            _capped_weights(allocation, portfolio_spec),
            execution_spec,
            self.strategy_id,
            self.strategy_version,
            "top_k_score_weight",
        )


class RankWeighted(_CandidateStrategy):
    strategy_id = "rank_weighted"

    def __init__(self, weight_mode: str | None = None) -> None:
        self.weight_mode = weight_mode

    def generate_targets(
        self,
        signal_frame: SignalFrame | pd.DataFrame,
        portfolio_spec: PortfolioSpec,
        execution_spec: ExecutionSpec,
        current_positions: Mapping[str, float] | None = None,
    ) -> list[PortfolioTarget]:
        candidates = _candidate_frame(signal_frame, execution_spec, portfolio_spec)
        mode = self.weight_mode or portfolio_spec.rank_weight_mode
        if mode == "linear":
            weights = [len(candidates) - index for index in range(len(candidates))]
        elif mode == "inverse_rank":
            weights = [1.0 / (index + 1) for index in range(len(candidates))]
        else:
            raise ValueError("weight_mode must be linear or inverse_rank")
        allocation = dict(zip(candidates["symbol"], weights, strict=True))
        return _targets(
            _capped_weights(allocation, portfolio_spec),
            execution_spec,
            self.strategy_id,
            self.strategy_version,
            f"rank_weighted_{mode}",
        )


class TopKDropout(_CandidateStrategy):
    strategy_id = "topk_dropout"

    def generate_targets(
        self,
        signal_frame: SignalFrame | pd.DataFrame,
        portfolio_spec: PortfolioSpec,
        execution_spec: ExecutionSpec,
        current_positions: Mapping[str, float] | None = None,
    ) -> list[PortfolioTarget]:
        candidates = _candidate_frame(signal_frame, execution_spec, portfolio_spec)
        ranked = list(candidates["symbol"])
        current = {
            normalize_symbol(symbol): float(weight)
            for symbol, weight in (current_positions or {}).items()
            if math.isfinite(float(weight)) and float(weight) > 0
        }
        limit = min(portfolio_spec.top_k, portfolio_spec.max_positions)
        held = set(current)
        if len(held) > limit:
            held = set(
                sorted(held, key=lambda symbol: (-current[symbol], symbol))[:limit]
            )
        desired = set(held)
        if len(held) >= limit:
            stale = sorted(
                held.difference(ranked), key=lambda symbol: (current[symbol], symbol)
            )
            desired.difference_update(stale[: min(portfolio_spec.n_drop, len(stale))])
        for symbol in ranked:
            if len(desired) >= limit:
                break
            desired.add(symbol)

        retained = {
            symbol: min(current[symbol], portfolio_spec.max_single_position)
            for symbol in desired
            if symbol in current
        }
        retained_total = sum(retained.values())
        if retained_total > portfolio_spec.target_gross_exposure and retained_total > 0:
            scale = portfolio_spec.target_gross_exposure / retained_total
            retained = {symbol: weight * scale for symbol, weight in retained.items()}
            retained_total = sum(retained.values())
        new_symbols = sorted(desired.difference(retained))
        remaining = max(0.0, portfolio_spec.target_gross_exposure - retained_total)
        new_allocation = {symbol: 1.0 for symbol in new_symbols}
        if new_allocation:
            new_weights = _capped_weights(
                new_allocation,
                PortfolioSpec(
                    top_k=portfolio_spec.top_k,
                    target_gross_exposure=remaining,
                    max_single_position=portfolio_spec.max_single_position,
                    max_positions=portfolio_spec.max_positions,
                    cash_buffer=max(0.0, 1.0 - remaining),
                    rebalance_policy=portfolio_spec.rebalance_policy,
                    rank_weight_mode=portfolio_spec.rank_weight_mode,
                    n_drop=portfolio_spec.n_drop,
                ),
            )
        else:
            new_weights = {}
        return _targets(
            {**retained, **new_weights},
            execution_spec,
            self.strategy_id,
            self.strategy_version,
            "top_k_dropout",
        )
