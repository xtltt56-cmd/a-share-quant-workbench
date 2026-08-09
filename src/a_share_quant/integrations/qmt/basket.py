"""Local CSV and JSON exports for manually reviewed QMT baskets."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from a_share_quant.account.contracts import price
from a_share_quant.data.normalization import normalize_symbol

_MISSING = object()
_REVIEW_STATUS = "MANUAL_REVIEW_ONLY"
_CSV_FIELDS = (
    "review_status",
    "manual_execution_required",
    "symbol",
    "name",
    "recommendation",
    "quantity",
    "maximum_acceptable_price",
    "reason_codes",
)


@dataclass(frozen=True)
class ManualBasketItem:
    """A validated, credential-free recommendation for user review."""

    symbol: str
    name: str
    recommendation: str
    quantity: int
    maximum_acceptable_price: Decimal
    reason_codes: tuple[str, ...]
    manual_execution_required: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "recommendation": self.recommendation,
            "quantity": self.quantity,
            "maximum_acceptable_price": f"{self.maximum_acceptable_price:.4f}",
            "reason_codes": list(self.reason_codes),
            "manual_execution_required": True,
        }


class ManualBasketExporter:
    """Export a review artifact only; it has no network or submission behavior."""

    def export_csv(self, recommendations: Iterable[object], output_path: str | Path) -> Path:
        """Write a local CSV whose every row requires manual review and execution."""

        items = _normalize_recommendations(recommendations)
        target = _prepare_output_path(output_path, suffix=".csv")
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDS)
            writer.writeheader()
            for item in items:
                writer.writerow(_csv_row(item))
        return target

    def export_json(self, recommendations: Iterable[object], output_path: str | Path) -> Path:
        """Write a local JSON review artifact with no credential or live-ID fields."""

        items = _normalize_recommendations(recommendations)
        target = _prepare_output_path(output_path, suffix=".json")
        payload = {
            "artifact_type": "QMT_MANUAL_BASKET",
            "review_status": _REVIEW_STATUS,
            "manual_execution_required": True,
            "notice": "This local artifact is for user review only and cannot submit anything.",
            "items": [item.to_dict() for item in items],
        }
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target


QmtBasketExporter = ManualBasketExporter


def export_manual_basket_csv(recommendations: Iterable[object], output_path: str | Path) -> Path:
    """Convenience export for a manual-review CSV basket."""

    return ManualBasketExporter().export_csv(recommendations, output_path)


def export_manual_basket_json(recommendations: Iterable[object], output_path: str | Path) -> Path:
    """Convenience export for a manual-review JSON basket."""

    return ManualBasketExporter().export_json(recommendations, output_path)


def _normalize_recommendations(recommendations: Iterable[object]) -> tuple[ManualBasketItem, ...]:
    if isinstance(recommendations, (str, bytes, bytearray)):
        raise ValueError("recommendations must be an iterable of recommendation objects")
    try:
        items = tuple(_normalize_recommendation(item) for item in recommendations)
    except TypeError as exc:
        raise ValueError("recommendations must be an iterable of recommendation objects") from exc
    if not items:
        raise ValueError("at least one recommendation is required")
    return items


def _normalize_recommendation(recommendation: object) -> ManualBasketItem:
    manual_required = _field(recommendation, ("manual_execution_required",))
    if manual_required is not _MISSING and manual_required is not True:
        raise ValueError("recommendation must require manual execution")
    symbol_value = _field(recommendation, ("symbol", "code", "stock_code"))
    if symbol_value is _MISSING:
        raise ValueError("recommendation symbol is required")
    try:
        symbol = normalize_symbol(symbol_value)
    except Exception as exc:
        raise ValueError("recommendation symbol is invalid") from exc
    quantity = _positive_quantity(
        _field(recommendation, ("quantity", "suggested_quantity", "target_quantity"))
    )
    maximum_acceptable_price = _positive_price(
        _field(
            recommendation,
            ("maximum_acceptable_price", "price", "limit_price", "reference_price"),
        )
    )
    name_value = _field(recommendation, ("name", "stock_name"))
    name = "" if name_value is _MISSING or name_value is None else str(name_value).strip()
    action_value = _field(
        recommendation,
        ("recommendation", "action", "action_zh", "state", "side"),
    )
    action = (
        "UNSPECIFIED" if action_value is _MISSING or action_value is None else _text(action_value)
    )
    reason_codes = _reason_codes(_field(recommendation, ("reason_codes", "reasons")))
    return ManualBasketItem(
        symbol=symbol,
        name=name,
        recommendation=action or "UNSPECIFIED",
        quantity=quantity,
        maximum_acceptable_price=maximum_acceptable_price,
        reason_codes=reason_codes,
    )


def _field(source: object, names: tuple[str, ...]) -> Any:
    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source[name]
        return _MISSING
    for name in names:
        try:
            return getattr(source, name)
        except AttributeError:
            continue
    return _MISSING


def _positive_quantity(value: Any) -> int:
    if value is _MISSING or isinstance(value, bool):
        raise ValueError("recommendation quantity must be a positive integer")
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise ValueError("recommendation quantity must be a positive integer") from exc
    if not parsed.is_finite() or parsed != parsed.to_integral_value() or parsed <= 0:
        raise ValueError("recommendation quantity must be a positive integer")
    return int(parsed)


def _positive_price(value: Any) -> Decimal:
    if value is _MISSING or isinstance(value, bool):
        raise ValueError("recommendation price must be a positive decimal")
    try:
        parsed = price(value)
    except ValueError as exc:
        raise ValueError("recommendation price must be a positive decimal") from exc
    if parsed <= 0:
        raise ValueError("recommendation price must be a positive decimal")
    return parsed


def _reason_codes(value: Any) -> tuple[str, ...]:
    if value is _MISSING or value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (bytes, bytearray)):
        raise ValueError("reason_codes must contain text")
    try:
        return tuple(text for item in value if (text := _text(item)))
    except TypeError as exc:
        raise ValueError("reason_codes must contain text") from exc


def _text(value: Any) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value).strip()


def _csv_row(item: ManualBasketItem) -> dict[str, object]:
    return {
        "review_status": _REVIEW_STATUS,
        "manual_execution_required": "true",
        "symbol": item.symbol,
        "name": item.name,
        "recommendation": item.recommendation,
        "quantity": item.quantity,
        "maximum_acceptable_price": f"{item.maximum_acceptable_price:.4f}",
        "reason_codes": ";".join(item.reason_codes),
    }


def _prepare_output_path(output_path: str | Path, *, suffix: str) -> Path:
    if not isinstance(output_path, (str, Path)):
        raise ValueError("output path must be a local file path")
    raw_path = str(output_path).strip()
    if not raw_path or "://" in raw_path or raw_path.casefold().startswith(("http:", "https:")):
        raise ValueError("output path must be a local file path")
    candidate = Path(raw_path)
    if not candidate.name or candidate.name in {".", ".."}:
        raise ValueError("output path must name a file")
    if candidate.suffix.casefold() != suffix:
        raise ValueError(f"output path must end in {suffix}")
    target = candidate.resolve(strict=False)
    if target.exists() and (target.is_symlink() or target.is_dir()):
        raise ValueError("output path must be a regular local file")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.parent.is_dir():
        raise ValueError("output path parent is not a directory")
    return target


__all__ = [
    "ManualBasketExporter",
    "ManualBasketItem",
    "QmtBasketExporter",
    "export_manual_basket_csv",
    "export_manual_basket_json",
]
