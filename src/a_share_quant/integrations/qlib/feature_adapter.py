"""Official Alpha158 feature construction behind a thin project adapter."""

from __future__ import annotations

from datetime import date
from typing import Any

from .calendar_adapter import QlibCalendarAdapter, QlibDependencyError


class QlibAlpha158FeatureAdapter:
    def __init__(
        self,
        calendar: QlibCalendarAdapter,
        *,
        feature_version: str = "alpha158_v1",
    ) -> None:
        self.calendar = calendar
        self.feature_version = feature_version

    def build_handler(
        self,
        *,
        instruments: list[str],
        start_date: date | str,
        end_date: date | str,
        label_expression: str,
    ) -> Any:
        self.calendar.initialize()
        try:
            from qlib.contrib.data.handler import Alpha158
        except ImportError as exc:  # pragma: no cover - guarded by initialize
            raise QlibDependencyError("Qlib Alpha158 is unavailable") from exc

        return Alpha158(
            instruments=instruments,
            start_time=start_date,
            end_time=end_date,
            freq="day",
            infer_processors=[],
            learn_processors=[],
            process_type="independent",
            label=([label_expression], ["LABEL0"]),
        )

    @staticmethod
    def fetch_raw(handler: Any):
        return handler.fetch(col_set="__raw", data_key="raw")
