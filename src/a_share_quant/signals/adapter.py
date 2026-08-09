"""Convert model prediction frames into the common project signal schema."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

from a_share_quant.contracts.stage3 import SignalFrame

from .schema import SignalRecord


def prediction_frame_to_records(
    predictions: pd.DataFrame,
    *,
    data_version: str,
    experiment_id: str,
    top_k: int | None = None,
    signal_available_at: datetime | pd.Timestamp | None = None,
    intended_execution_date: date | None = None,
) -> list[SignalRecord]:
    required = {
        "signal_date",
        "symbol",
        "raw_score",
        "strategy_id",
        "strategy_version",
        "model_version",
        "feature_version",
    }
    missing = sorted(required.difference(predictions.columns))
    if missing:
        raise ValueError(f"prediction frame is missing signal fields: {', '.join(missing)}")
    records: list[SignalRecord] = []
    for signal_date, group in predictions.groupby("signal_date", sort=True):
        current = group.copy()
        current["raw_score"] = pd.to_numeric(current["raw_score"], errors="coerce")
        if current["raw_score"].isna().any() or not np.isfinite(current["raw_score"]).all():
            raise ValueError("prediction scores must be finite")
        current = current.sort_values(
            ["raw_score", "symbol"], ascending=[False, True], kind="stable"
        )
        if top_k is not None:
            current = current.head(top_k)
        scores = current["raw_score"]
        minimum, maximum = float(scores.min()), float(scores.max())
        if maximum == minimum:
            normalized = pd.Series(50.0, index=current.index)
        else:
            normalized = (scores - minimum) / (maximum - minimum) * 100
        count = len(current)
        for rank, (index, row) in enumerate(current.iterrows(), start=1):
            records.append(
                SignalRecord(
                    signal_date=_as_date(signal_date),
                    symbol=str(row["symbol"]),
                    strategy_id=str(row["strategy_id"]),
                    strategy_version=str(row["strategy_version"]),
                    raw_score=float(row["raw_score"]),
                    normalized_score=float(normalized.loc[index]),
                    rank=rank,
                    confidence=1.0 - (rank - 1) / max(count - 1, 1),
                    model_version=str(row["model_version"]),
                    feature_version=str(row["feature_version"]),
                    data_version=data_version,
                    experiment_id=experiment_id,
                    signal_available_at=signal_available_at,
                    intended_execution_date=intended_execution_date,
                )
            )
    return records


def prediction_frame_to_signal_frame(
    predictions: pd.DataFrame,
    *,
    data_version: str,
    experiment_id: str,
    top_k: int | None = None,
    trading_dates: list[date] | None = None,
    signal_available_at: datetime | pd.Timestamp | None = None,
) -> SignalFrame:
    """Build the Stage 3 contract without changing the source predictions."""

    current = predictions.copy()
    if top_k is not None:
        current = (
            current.sort_values(
                ["signal_date", "raw_score", "symbol"],
                ascending=[True, False, True],
                kind="stable",
            )
            .groupby("signal_date", sort=False, group_keys=False)
            .head(top_k)
            .reset_index(drop=True)
        )
    current["data_version"] = data_version
    return SignalFrame.from_predictions(
        current,
        experiment_id=experiment_id,
        trading_dates=trading_dates,
        signal_available_at=signal_available_at,
    )


def _as_date(value: date | str) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid signal date: {value!r}")
    return parsed.date()
