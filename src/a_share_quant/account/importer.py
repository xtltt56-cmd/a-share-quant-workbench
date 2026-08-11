"""Preview-only broker-file normalization for local manual account records."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from a_share_quant.data.normalization import normalize_symbol

from .contracts import FillEvent, TradeSide, price

_REQUIRED_FIELDS = ("symbol", "quantity", "price")
_MAX_IMPORT_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class ImportIssue:
    row_number: int
    reason: str


@dataclass(frozen=True)
class ImportPreview:
    preview_id: str
    source_sha256: str
    candidate_events: tuple[FillEvent, ...]
    rejected_rows: tuple[ImportIssue, ...]

    @property
    def accepted_rows(self) -> int:
        return len(self.candidate_events)


def preview_broker_rows(
    *,
    rows: Iterable[Mapping[str, Any]],
    mapping: Mapping[str, str],
    default_trade_date: date,
    source_bytes: bytes,
) -> ImportPreview:
    """Normalize an explicit whitelist of broker columns without mutating a ledger."""

    normalized_mapping = _validate_mapping(mapping)
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    candidates: list[FillEvent] = []
    rejected: list[ImportIssue] = []
    for row_number, row in enumerate(rows, start=1):
        try:
            candidates.append(
                _row_to_event(
                    row,
                    row_number=row_number,
                    mapping=normalized_mapping,
                    default_trade_date=default_trade_date,
                    source_sha256=source_sha256,
                )
            )
        except (KeyError, TypeError, ValueError):
            rejected.append(ImportIssue(row_number=row_number, reason="VALIDATION_ERROR"))
    preview_id = hashlib.sha256(
        _canonical_json(
            {
                "source_sha256": source_sha256,
                "mapping": normalized_mapping,
                "default_trade_date": default_trade_date.isoformat(),
            }
        ).encode("utf-8")
    ).hexdigest()
    return ImportPreview(
        preview_id=preview_id,
        source_sha256=source_sha256,
        candidate_events=tuple(candidates),
        rejected_rows=tuple(rejected),
    )


def read_broker_file(source: Path) -> tuple[list[dict[str, Any]], bytes]:
    """Read a small local CSV/XLS(X) file without retaining unmapped columns."""

    path = Path(source)
    if not path.is_file():
        raise ValueError("broker import source must be a regular file")
    raw = path.read_bytes()
    if len(raw) > _MAX_IMPORT_BYTES:
        raise ValueError("broker import source exceeds the size limit")
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        text = _decode_csv(raw)
        return [dict(row) for row in csv.DictReader(text.splitlines())], raw
    if suffix not in {".xlsx", ".xls"}:
        raise ValueError("broker import supports only CSV, XLSX, or XLS")
    try:
        import pandas as pd

        frame = pd.read_excel(path, dtype=object)
    except Exception as exc:
        if suffix == ".xls":
            delimited_rows = _read_legacy_text_export(raw)
            if delimited_rows is not None:
                return delimited_rows, raw
        raise ValueError("Excel import could not be read") from exc
    sanitized = frame.where(frame.notna(), None)
    return [dict(row) for row in sanitized.to_dict(orient="records")], raw


def _row_to_event(
    row: Mapping[str, Any],
    *,
    row_number: int,
    mapping: Mapping[str, str],
    default_trade_date: date,
    source_sha256: str,
) -> FillEvent:
    side = _parse_trade_side(row.get(mapping.get("side", "")))
    symbol = normalize_symbol(_clean_export_value(row[mapping["symbol"]]))
    quantity = _positive_integer(row[mapping["quantity"]], field="quantity")
    parsed_price = price(row[mapping["price"]])
    if parsed_price <= 0:
        raise ValueError("price must be positive")
    trade_date = _parse_trade_date(row.get(mapping.get("trade_date", "")), default_trade_date)
    name = str(row.get(mapping.get("name", ""), "")).strip()
    if side is TradeSide.BUY and not name:
        raise ValueError("buy imports require a name")
    return FillEvent(
        event_id=f"import-{source_sha256[:16]}-{row_number}",
        side=side,
        symbol=symbol,
        quantity=quantity,
        price=parsed_price,
        trade_date=trade_date,
        name=name,
        source="broker_import",
    )


def _validate_mapping(mapping: Mapping[str, str]) -> dict[str, str]:
    normalized = {str(key): str(value) for key, value in mapping.items()}
    missing = [field for field in _REQUIRED_FIELDS if not normalized.get(field, "").strip()]
    if missing:
        raise ValueError(f"mapping missing required fields: {', '.join(missing)}")
    supported = {"name", "symbol", "quantity", "price", "side", "trade_date"}
    unexpected = set(normalized).difference(supported)
    if unexpected:
        raise ValueError("mapping has unsupported fields")
    return normalized


def _positive_integer(value: Any, *, field: str) -> int:
    parsed = Decimal(str(value))
    if not parsed.is_finite() or parsed <= 0 or parsed != parsed.to_integral_value():
        raise ValueError(f"{field} must be a positive integer")
    return int(parsed)


def _parse_trade_date(value: Any, default: date) -> date:
    if value is None or not str(value).strip():
        return default
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _parse_trade_side(value: Any) -> TradeSide:
    if value is None or not str(value).strip():
        return TradeSide.BUY
    normalized = str(value).strip().upper()
    if normalized in {"BUY", "B", "买入", "证券买入", "买"}:
        return TradeSide.BUY
    if normalized in {"SELL", "S", "卖出", "证券卖出", "卖"}:
        return TradeSide.SELL
    raise ValueError("unsupported trade side")


def _clean_export_value(value: Any) -> str:
    text = str(value).strip()
    if len(text) >= 4 and text.startswith('="') and text.endswith('"'):
        return text[2:-1].strip()
    if text.startswith("'"):
        return text[1:].strip()
    return text


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode_csv(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV encoding must be UTF-8 or GB18030")


def _read_legacy_text_export(raw: bytes) -> list[dict[str, Any]] | None:
    try:
        text = _decode_csv(raw)
    except ValueError:
        return None
    if "\t" not in text or "\n" not in text:
        return None
    rows = [dict(row) for row in csv.DictReader(text.splitlines(), delimiter="\t")]
    if not rows or not any(str(key).strip() for key in rows[0]):
        return None
    return rows
