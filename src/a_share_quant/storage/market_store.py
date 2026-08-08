"""DuckDB manifest and atomic Parquet storage for canonical market data."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import duckdb
import pandas as pd

from a_share_quant.contracts.data import CANONICAL_INSTRUMENT_COLUMNS, DataValidationError
from a_share_quant.data.normalization import (
    normalize_daily_bars,
    normalize_instruments,
    normalize_symbol,
)


class MarketDataStore:
    """Local data lake with idempotent writes and an auditable ingestion manifest."""

    def __init__(self, root: str | Path, database_path: str | Path | None = None) -> None:
        self.root = Path(root)
        configured_database = (
            Path(database_path) if database_path is not None else Path("quant.duckdb")
        )
        self.database_path = (
            configured_database
            if configured_database.is_absolute()
            else self.root / configured_database
        )
        self.daily_dir = self.root / "lake" / "daily_bars"
        self.instrument_dir = self.root / "lake" / "instruments"

    def initialize(self) -> None:
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.instrument_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.database_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ingestion_manifest (
                    dataset VARCHAR NOT NULL,
                    symbol VARCHAR NOT NULL,
                    source VARCHAR NOT NULL,
                    start_date DATE,
                    end_date DATE,
                    row_count BIGINT NOT NULL,
                    data_version VARCHAR NOT NULL,
                    quality_status VARCHAR NOT NULL,
                    file_path VARCHAR NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (dataset, symbol, data_version)
                )
                """
            )

    def write_daily_bars(self, frame: pd.DataFrame) -> None:
        self.initialize()
        if frame.empty:
            return
        if "symbol" not in frame.columns:
            raise DataValidationError("daily bars require symbol")
        for symbol, group in frame.groupby("symbol", sort=True):
            normalized_symbol = normalize_symbol(symbol)
            source = (
                str(group["source"].dropna().iloc[-1]) if "source" in group.columns else "unknown"
            )
            data_version = (
                str(group["data_version"].dropna().iloc[-1])
                if "data_version" in group.columns and not group["data_version"].dropna().empty
                else "canonical-v1"
            )
            path = self.daily_dir / f"{normalized_symbol}.parquet"
            existing = pd.read_parquet(path) if path.exists() else pd.DataFrame()
            merged = (
                pd.concat([existing, group], ignore_index=True)
                if not existing.empty
                else group.copy()
            )
            normalized = normalize_daily_bars(
                merged,
                symbol=normalized_symbol,
                source=source,
                data_version=data_version,
            )
            self._atomic_parquet_write(normalized, path)
            self._upsert_manifest(
                dataset="daily_bars",
                symbol=normalized_symbol,
                source=source,
                start_date=normalized["date"].min(),
                end_date=normalized["date"].max(),
                row_count=len(normalized),
                data_version=data_version,
                file_path=path,
            )

    def write_instruments(self, frame: pd.DataFrame) -> None:
        self.initialize()
        if frame.empty:
            return
        if "as_of" not in frame.columns:
            raise DataValidationError("instrument snapshots require as_of")
        snapshot_date = pd.to_datetime(frame["as_of"], errors="coerce").dt.date
        if snapshot_date.isna().any() or snapshot_date.nunique() != 1:
            raise DataValidationError("instrument snapshot must contain one valid as_of date")
        as_of = snapshot_date.iloc[0]
        source = str(frame["source"].dropna().iloc[-1]) if "source" in frame.columns else "unknown"
        data_version = (
            str(frame["data_version"].dropna().iloc[-1])
            if "data_version" in frame.columns and not frame["data_version"].dropna().empty
            else "canonical-v1"
        )
        normalized = normalize_instruments(
            frame,
            source=source,
            as_of=as_of,
            data_version=data_version,
        )
        path = self.instrument_dir / f"as_of={as_of.isoformat()}.parquet"
        existing = pd.read_parquet(path) if path.exists() else pd.DataFrame()
        merged = (
            pd.concat([existing, normalized], ignore_index=True)
            if not existing.empty
            else normalized
        )
        merged = merged.drop_duplicates(subset=["symbol"], keep="last").reset_index(drop=True)
        self._atomic_parquet_write(merged.loc[:, list(CANONICAL_INSTRUMENT_COLUMNS)], path)
        self._upsert_manifest(
            dataset="instruments",
            symbol=f"__snapshot__:{as_of.isoformat()}",
            source=source,
            start_date=as_of,
            end_date=as_of,
            row_count=len(merged),
            data_version=data_version,
            file_path=path,
        )

    def read_daily_bars(
        self,
        *,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        normalized_symbol = normalize_symbol(symbol)
        path = self.daily_dir / f"{normalized_symbol}.parquet"
        if not path.exists():
            return pd.DataFrame()
        clauses = ["symbol = ?"]
        parameters: list[object] = [normalized_symbol]
        if start_date is not None:
            clauses.append("date >= ?")
            parameters.append(start_date)
        if end_date is not None:
            clauses.append("date <= ?")
            parameters.append(end_date)
        query = f"SELECT * FROM read_parquet(?) WHERE {' AND '.join(clauses)} ORDER BY date"
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            result = connection.execute(query, [str(path), *parameters]).df()
        if not result.empty and "date" in result.columns:
            result["date"] = pd.to_datetime(result["date"], errors="raise").dt.date
        return result

    def read_instruments(self, *, as_of: date | None = None) -> pd.DataFrame:
        paths = sorted(self.instrument_dir.glob("*.parquet"))
        if not paths:
            return pd.DataFrame(columns=list(CANONICAL_INSTRUMENT_COLUMNS))
        query = "SELECT * FROM read_parquet(?)"
        parameters: list[object] = [str(self.instrument_dir / "*.parquet")]
        if as_of is not None:
            query += " WHERE as_of <= ?"
            parameters.append(as_of)
        query += (
            " QUALIFY row_number() OVER (PARTITION BY symbol ORDER BY as_of DESC) = 1"
            " ORDER BY symbol"
        )
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            return connection.execute(query, parameters).df()

    def latest_date(self, symbol: str) -> date | None:
        normalized_symbol = normalize_symbol(symbol)
        path = self.daily_dir / f"{normalized_symbol}.parquet"
        if not path.exists():
            return None
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            value = connection.execute(
                "SELECT max(date) FROM read_parquet(?)", [str(path)]
            ).fetchone()[0]
        if value is None:
            return None
        return value.date() if hasattr(value, "date") else value

    def manifest_rows(self, *, dataset: str, symbol: str | None = None) -> list[dict[str, object]]:
        self.initialize()
        query = "SELECT * FROM ingestion_manifest WHERE dataset = ?"
        parameters: list[object] = [dataset]
        if symbol is not None:
            query += " AND symbol = ?"
            parameters.append(symbol)
        query += " ORDER BY updated_at"
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            result = connection.execute(query, parameters)
            columns = [item[0] for item in result.description]
            return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]

    def _atomic_parquet_write(self, frame: pd.DataFrame, path: Path) -> None:
        temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp.parquet")
        try:
            frame.to_parquet(temporary, index=False)
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _upsert_manifest(
        self,
        *,
        dataset: str,
        symbol: str,
        source: str,
        start_date: date,
        end_date: date,
        row_count: int,
        data_version: str,
        file_path: Path,
    ) -> None:
        updated_at = datetime.now(timezone.utc)
        with duckdb.connect(str(self.database_path)) as connection:
            connection.execute(
                "DELETE FROM ingestion_manifest "
                "WHERE dataset = ? AND symbol = ? AND data_version = ?",
                [dataset, symbol, data_version],
            )
            connection.execute(
                """
                INSERT INTO ingestion_manifest
                (dataset, symbol, source, start_date, end_date, row_count,
                 data_version, quality_status, file_path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'valid', ?, ?)
                """,
                [
                    dataset,
                    symbol,
                    source,
                    start_date,
                    end_date,
                    row_count,
                    data_version,
                    str(file_path),
                    updated_at,
                ],
            )
