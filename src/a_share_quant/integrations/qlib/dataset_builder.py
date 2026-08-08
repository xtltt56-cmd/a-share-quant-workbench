"""Build a Qlib DatasetH from project-owned point-in-time data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from a_share_quant.features.universe import HistoricalUniverse

from .calendar_adapter import QlibCalendarAdapter
from .feature_adapter import QlibAlpha158FeatureAdapter
from .instrument_adapter import QlibInstrumentAdapter
from .label_adapter import ForwardExcessReturnLabelAdapter
from .provider import QlibProviderAdapter, QlibProviderArtifact


@dataclass(frozen=True)
class QlibDatasetArtifact:
    dataset: Any
    frame: pd.DataFrame
    provider: QlibProviderArtifact
    feature_version: str
    label_name: str
    benchmark: str


class QlibDatasetBuilder:
    def __init__(
        self,
        provider_root: Path,
        *,
        feature_version: str = "alpha158_v1",
        kernels: int = 1,
    ) -> None:
        self.provider_root = Path(provider_root)
        self.feature_version = feature_version
        self.kernels = kernels

    def build(
        self,
        daily_bars: pd.DataFrame,
        *,
        universe: HistoricalUniverse | None,
        symbols: list[str],
        benchmark: str,
        start_date: date | str,
        end_date: date | str,
        segments: dict[str, tuple[date | str, date | str]],
    ) -> QlibDatasetArtifact:
        provider = QlibProviderAdapter(self.provider_root).build(daily_bars)
        instrument_adapter = QlibInstrumentAdapter()
        candidate_symbols = instrument_adapter.normalize_symbols([*symbols, benchmark])
        calendar = QlibCalendarAdapter(provider.root, kernels=self.kernels)
        feature_adapter = QlibAlpha158FeatureAdapter(
            calendar,
            feature_version=self.feature_version,
        )
        handler = feature_adapter.build_handler(
            instruments=list(candidate_symbols),
            start_date=start_date,
            end_date=end_date,
            label_expression="Ref($close, -5)/$close - 1",
        )
        raw = feature_adapter.fetch_raw(handler)
        frame = ForwardExcessReturnLabelAdapter().apply(raw, benchmark=benchmark)
        frame = self._apply_historical_universe(frame, universe, symbols)

        from qlib.data.dataset import DatasetH
        from qlib.data.dataset.handler import DataHandlerLP

        qlib_handler = DataHandlerLP.from_df(frame)
        dataset = DatasetH(handler=qlib_handler, segments=segments)
        return QlibDatasetArtifact(
            dataset=dataset,
            frame=frame,
            provider=provider,
            feature_version=self.feature_version,
            label_name=ForwardExcessReturnLabelAdapter.name,
            benchmark=benchmark,
        )

    @staticmethod
    def _apply_historical_universe(
        frame: pd.DataFrame,
        universe: HistoricalUniverse | None,
        symbols: list[str],
    ) -> pd.DataFrame:
        allowed_symbols = set(QlibInstrumentAdapter.normalize_symbols(symbols))
        rows = frame.loc[frame.index.get_level_values("instrument").isin(allowed_symbols)].copy()
        if universe is None:
            return rows

        cache: dict[date, set[str]] = {}
        keep: list[bool] = []
        for timestamp, symbol in rows.index:
            current_date = pd.Timestamp(timestamp).date()
            if current_date not in cache:
                cache[current_date] = set(universe.tradable_universe(current_date)["symbol"])
            keep.append(symbol in cache[current_date])
        return rows.loc[keep].sort_index()
