"""Small, fixture-only equal-rank ensemble boundary for Stage 3B."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from a_share_quant.contracts.modes import validate_data_mode
from a_share_quant.contracts.stage3 import SignalFrame


class EqualRankEnsemble:
    strategy_id = "equal_rank_ensemble_fixture"
    strategy_version = "stage3b-fixture-v1"

    def combine(
        self,
        signal_frames: Sequence[SignalFrame],
        *,
        data_mode: str = "fixture",
    ) -> SignalFrame:
        mode = validate_data_mode(data_mode)
        if not signal_frames:
            raise ValueError("equal-rank ensemble requires at least one signal frame")
        parts: list[pd.DataFrame] = []
        for signal_frame in signal_frames:
            if not isinstance(signal_frame, SignalFrame):
                raise TypeError("equal-rank ensemble requires SignalFrame inputs")
            current = signal_frame.to_frame()
            strategy_values = current["strategy_id"].astype(str).str.lower()
            if strategy_values.str.contains("rule").all():
                continue
            if signal_frame.data_mode != mode:
                raise ValueError("all ensemble inputs must use the requested data_mode")
            part = current.copy()
            part["rank_score"] = 1.0 / part["rank"].astype(float)
            parts.append(part)
        if not parts:
            raise ValueError("no ensemble-eligible signal frame remains")
        combined = pd.concat(parts, ignore_index=True)
        grouped = combined.groupby(["date", "symbol"], sort=True, as_index=False)
        rows: list[dict[str, object]] = []
        for (signal_date, symbol), group in grouped:
            rank_score = float(group["rank_score"].mean())
            rows.append(
                {
                    "date": signal_date,
                    "symbol": symbol,
                    "strategy_id": self.strategy_id,
                    "strategy_version": self.strategy_version,
                    "model_version": "equal-rank-fixture",
                    "feature_version": "signal-rank-v1",
                    "experiment_id": "equal-rank-fixture",
                    "raw_score": rank_score,
                    "normalized_score": rank_score,
                    "confidence": min(1.0, rank_score),
                    "signal_available_at": group["signal_available_at"].max(),
                    "intended_execution_date": group["intended_execution_date"].max(),
                    "data_mode": mode,
                }
            )
        result = pd.DataFrame(rows)
        result["rank"] = result.groupby("date")["raw_score"].rank(
            ascending=False, method="first"
        )
        result["normalized_score"] = result.groupby("date")["raw_score"].transform(
            lambda values: (values - values.min()) / (values.max() - values.min()) * 100
            if values.max() != values.min()
            else pd.Series(50.0, index=values.index)
        )
        result["normalized_score"] = result["normalized_score"].replace([np.inf, -np.inf], 50.0)
        return SignalFrame.from_frame(result, data_mode=mode)
