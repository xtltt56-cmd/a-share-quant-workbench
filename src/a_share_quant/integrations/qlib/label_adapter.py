"""Label definitions shared by Qlib baselines."""

from __future__ import annotations

import pandas as pd


class ForwardExcessReturnLabelAdapter:
    name = "forward_excess_return_5d"
    horizon_days = 5
    qlib_raw_name = "LABEL0"

    def apply(self, frame: pd.DataFrame, *, benchmark: str) -> pd.DataFrame:
        if not isinstance(frame.index, pd.MultiIndex) or "datetime" not in frame.index.names:
            raise ValueError("Qlib Alpha158 frame must have datetime/instrument index levels")
        label_column = ("label", self.qlib_raw_name)
        if label_column not in frame.columns:
            raise ValueError(f"Qlib frame is missing raw label column: {label_column}")
        if "instrument" not in frame.index.names:
            raise ValueError("Qlib Alpha158 frame must have an instrument index level")

        benchmark_label = frame[label_column].xs(benchmark, level="instrument", drop_level=True)
        dates = frame.index.get_level_values("datetime")
        benchmark_values = pd.Series(dates, index=frame.index).map(benchmark_label)
        result = frame.copy()
        result["label", self.name] = result[label_column].to_numpy() - benchmark_values.to_numpy()
        result = result.drop(columns=[label_column])
        result = result.loc[result.index.get_level_values("instrument") != benchmark]
        return result.sort_index()
