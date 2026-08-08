"""Build a local Qlib binary provider from canonical daily bars."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd

from a_share_quant.contracts.data import DataValidationError
from a_share_quant.data.normalization import normalize_symbol

QLIB_MARKET_FIELDS = ("open", "high", "low", "close", "volume", "vwap")


@dataclass(frozen=True)
class QlibProviderArtifact:
    root: Path
    dataset_hash: str
    symbols: tuple[str, ...]
    calendar: tuple[date, ...]


class QlibProviderAdapter:
    """Materialize Qlib's documented local file layout without changing source data."""

    def __init__(self, root: Path, *, freq: str = "day", market: str = "all") -> None:
        self.root = Path(root)
        self.freq = freq
        self.market = market

    def build(self, daily_bars: pd.DataFrame) -> QlibProviderArtifact:
        frame = _prepare_daily_bars(daily_bars)
        calendar = tuple(sorted(frame["date"].unique()))
        symbols = tuple(sorted(frame["symbol"].unique()))
        if not calendar or not symbols:
            raise DataValidationError("Qlib provider requires non-empty daily bars")

        self._write_calendar(calendar)
        self._write_instruments(frame, calendar)
        self._write_features(frame, calendar, symbols)

        digest = sha256(pd.util.hash_pandas_object(frame, index=False).values.tobytes()).hexdigest()
        return QlibProviderArtifact(
            root=self.root,
            dataset_hash=digest,
            symbols=symbols,
            calendar=calendar,
        )

    def _write_calendar(self, calendar: tuple[date, ...]) -> None:
        path = self.root / "calendars" / f"{self.freq}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(item.isoformat() for item in calendar) + "\n", encoding="utf-8")

    def _write_instruments(self, frame: pd.DataFrame, calendar: tuple[date, ...]) -> None:
        path = self.root / "instruments" / f"{self.market}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for symbol, group in frame.groupby("symbol", sort=True):
            rows.append(f"{symbol}\t{group['date'].min().isoformat()}\t{group['date'].max().isoformat()}")
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def _write_features(
        self,
        frame: pd.DataFrame,
        calendar: tuple[date, ...],
        symbols: tuple[str, ...],
    ) -> None:
        calendar_index = pd.Index(calendar, name="date")
        for symbol in symbols:
            symbol_dir = self.root / "features" / symbol
            symbol_dir.mkdir(parents=True, exist_ok=True)
            group = frame.loc[frame["symbol"] == symbol].set_index("date")
            for field in QLIB_MARKET_FIELDS:
                values = group[field].reindex(calendar_index).to_numpy(dtype="float32")
                np.concatenate((np.array([0.0], dtype="float32"), values)).astype("<f4").tofile(
                    symbol_dir / f"{field}.{self.freq}.bin"
                )


def _prepare_daily_bars(daily_bars: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(daily_bars, pd.DataFrame) or daily_bars.empty:
        raise DataValidationError("Qlib provider daily bars must be a non-empty DataFrame")
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = sorted(required.difference(daily_bars.columns))
    if missing:
        raise DataValidationError(f"missing Qlib provider columns: {', '.join(missing)}")

    frame = daily_bars.copy()
    frame["symbol"] = frame["symbol"].map(normalize_symbol)
    parsed = pd.to_datetime(frame["date"], errors="coerce")
    if parsed.isna().any():
        raise DataValidationError("invalid daily bar date for Qlib provider")
    frame["date"] = parsed.dt.date
    numeric_fields = ["open", "high", "low", "close", "volume"]
    for field in numeric_fields:
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
        if frame[field].isna().any():
            raise DataValidationError(f"invalid Qlib provider numeric field: {field}")
    if "vwap" in frame.columns:
        frame["vwap"] = pd.to_numeric(frame["vwap"], errors="coerce")
    else:
        amount = pd.to_numeric(frame.get("amount"), errors="coerce")
        frame["vwap"] = frame["close"]
        valid_amount = amount.notna() & frame["volume"].gt(0)
        frame.loc[valid_amount, "vwap"] = amount[valid_amount] / frame.loc[valid_amount, "volume"]
    frame["vwap"] = frame["vwap"].fillna(frame["close"])
    if frame[list(QLIB_MARKET_FIELDS)].isna().any().any():
        raise DataValidationError("Qlib provider fields cannot be null")
    frame = frame.sort_values(["symbol", "date"], kind="stable")
    frame = frame.drop_duplicates(["symbol", "date"], keep="last").reset_index(drop=True)
    return frame.loc[:, ["symbol", "date", *QLIB_MARKET_FIELDS]]
