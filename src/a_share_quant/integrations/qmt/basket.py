"""Local CSV and JSON exports for manually reviewed QMT baskets."""

from __future__ import annotations

import csv
import io
import json
import os
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PureWindowsPath
from typing import Any
from uuid import uuid4

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
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")
_TEMP_FILE_ATTEMPTS = 16
_WINDOWS_RESERVED_DEVICE_NAMES = frozenset(
    ("CON", "PRN", "AUX", "NUL", "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³")
    + tuple(f"COM{number}" for number in range(1, 10))
    + tuple(f"LPT{number}" for number in range(1, 10))
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
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for item in items:
            writer.writerow(_csv_row(item))
        _atomic_write(target, buffer.getvalue().encode("utf-8"))
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
        serialized_payload = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        _atomic_write(target, serialized_payload.encode("utf-8"))
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
        "name": _escape_csv_text(item.name),
        "recommendation": _escape_csv_text(item.recommendation),
        "quantity": item.quantity,
        "maximum_acceptable_price": f"{item.maximum_acceptable_price:.4f}",
        "reason_codes": _escape_csv_text(";".join(item.reason_codes)),
    }


def _escape_csv_text(value: str) -> str:
    if value.startswith(_CSV_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def _atomic_write(target: Path, contents: bytes) -> None:
    """Best-effort atomic replacement of a revalidated local artifact.

    Path-only platforms cannot fully eliminate an adversarial path-swap race, so
    this helper revalidates before each filesystem transition and rejects any
    detected link or junction rather than claiming a handle-bound guarantee.
    """

    temporary_path: Path | None = None
    file_descriptor: int | None = None
    try:
        _assert_ready_local_output_target(target)
        temporary_path, file_descriptor = _create_temporary_output_file(target)
        _assert_safe_temporary_output_file(target, temporary_path)
        with os.fdopen(file_descriptor, "wb") as handle:
            file_descriptor = None
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_safe_temporary_output_file(target, temporary_path)
        os.replace(temporary_path, target)
        temporary_path = None
        _assert_ready_local_output_target(target)
    except BaseException:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            _cleanup_temporary_output_file(temporary_path)
        raise


def _create_temporary_output_file(target: Path) -> tuple[Path, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    for _ in range(_TEMP_FILE_ATTEMPTS):
        temporary_path = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        _assert_ready_local_output_target(target)
        _assert_local_output_target(temporary_path)
        try:
            file_descriptor = os.open(temporary_path, flags, 0o600)
        except FileExistsError:
            continue
        try:
            _assert_safe_temporary_output_file(target, temporary_path)
        except BaseException:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
            _cleanup_temporary_output_file(temporary_path)
            raise
        return temporary_path, file_descriptor
    raise OSError("unable to create a unique temporary basket output file")


def _assert_ready_local_output_target(target: Path) -> None:
    _assert_local_output_target(target)
    try:
        target_status = os.lstat(target)
    except FileNotFoundError:
        target_status = None
    except OSError as exc:
        raise ValueError("output path must be a regular local file") from exc
    if target_status is not None and not stat.S_ISREG(target_status.st_mode):
        raise ValueError("output path must be a regular local file")
    if not target.parent.is_dir():
        raise ValueError("output path parent is not a directory")


def _assert_safe_temporary_output_file(target: Path, temporary_path: Path) -> None:
    if temporary_path.parent != target.parent:
        raise ValueError("temporary basket output must use the target parent directory")
    _assert_ready_local_output_target(target)
    _assert_local_output_target(temporary_path)
    try:
        temporary_status = os.lstat(temporary_path)
    except OSError as exc:
        raise ValueError("temporary basket output file is unavailable") from exc
    if not stat.S_ISREG(temporary_status.st_mode):
        raise ValueError("temporary basket output must be a regular local file")


def _cleanup_temporary_output_file(temporary_path: Path) -> None:
    try:
        _assert_local_output_target(temporary_path.parent)
        os.unlink(temporary_path)
    except (OSError, ValueError):
        pass


def _prepare_output_path(output_path: str | Path, *, suffix: str) -> Path:
    if not isinstance(output_path, (str, Path)):
        raise ValueError("output path must be a local file path")
    raw_path = str(output_path).strip()
    if not raw_path or _is_non_local_path_text(raw_path):
        raise ValueError("output path must be a local-only file path")
    candidate = Path(raw_path)
    if not candidate.name or candidate.name in {".", ".."}:
        raise ValueError("output path must name a file")
    _validate_windows_path_components(raw_path)
    if candidate.suffix.casefold() != suffix:
        raise ValueError(f"output path must end in {suffix}")
    lexical_target = candidate if candidate.is_absolute() else Path.cwd() / candidate
    _assert_local_output_target(lexical_target)
    target = lexical_target.resolve(strict=False)
    _assert_local_output_target(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _assert_ready_local_output_target(target)
    return target


def _validate_windows_path_components(raw_path: str) -> None:
    windows_path = PureWindowsPath(raw_path)
    anchor_components = {
        component
        for component in (windows_path.anchor, windows_path.drive, windows_path.root)
        if component
    }
    for component in windows_path.parts:
        if component in anchor_components:
            continue
        if ":" in component:
            raise ValueError("output path must not contain ADS colon components")
        normalized_base = component.rstrip(" .").split(".", maxsplit=1)[0].rstrip(" .")
        if normalized_base.upper() in _WINDOWS_RESERVED_DEVICE_NAMES:
            raise ValueError("output path contains a reserved DOS device name")


def _is_non_local_path_text(value: str) -> bool:
    normalized = value.casefold()
    return (
        value.startswith(("\\\\", "//"))
        or "://" in value
        or normalized.startswith(("http:", "https:"))
    )


def _assert_local_output_target(target: Path) -> None:
    if _is_network_target(target):
        raise ValueError("output path must be local-only")
    for ancestor in _path_and_parents(target):
        if _is_link_or_junction(ancestor):
            raise ValueError(
                "output path must be local-only; symlink or junction parents are not allowed"
            )


def _path_and_parents(path: Path) -> tuple[Path, ...]:
    result: list[Path] = []
    current = path
    while True:
        result.append(current)
        parent = current.parent
        if parent == current:
            return tuple(result)
        current = parent


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return bool(reparse_point and attributes & reparse_point)
    except OSError:
        return False


def _is_network_target(path: Path) -> bool:
    if str(path).startswith(("\\\\", "//")):
        return True
    if os.name != "nt" or not path.drive:
        return False
    try:
        import ctypes

        return ctypes.windll.kernel32.GetDriveTypeW(f"{path.drive}\\") == 4
    except (AttributeError, OSError):
        return False


__all__ = [
    "ManualBasketExporter",
    "ManualBasketItem",
    "QmtBasketExporter",
    "export_manual_basket_csv",
    "export_manual_basket_json",
]
