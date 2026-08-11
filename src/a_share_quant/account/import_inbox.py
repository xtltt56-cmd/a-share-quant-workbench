"""安全的券商导出文件收件箱与导入预览。"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from a_share_quant.data.normalization import normalize_symbol

from .contracts import TradeSide, price
from .importer import ImportIssue, ImportPreview, preview_broker_rows, read_broker_file

_MAX_SCAN_FILES = 100
_MAX_IMPORT_ROWS = 20_000
_MAX_IMPORT_COLUMNS = 100
_SUPPORTED_SUFFIXES = frozenset({".csv", ".xlsx", ".xls"})
_REPARSE_POINT = 0x400


class AccountImportKind(str, Enum):
    FILLS = "FILLS"
    POSITIONS = "POSITIONS"


@dataclass(frozen=True)
class InboxFile:
    file_id: str
    file_name: str
    size_bytes: int
    modified_ns: int


@dataclass(frozen=True)
class ImportedPosition:
    symbol: str
    name: str
    total_quantity: int
    available_quantity: int
    frozen_quantity: int
    average_cost: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        if self.total_quantity < 0:
            raise ValueError("total quantity must be non-negative")
        if self.available_quantity < 0 or self.frozen_quantity < 0:
            raise ValueError("position quantities must be non-negative")
        if self.available_quantity + self.frozen_quantity != self.total_quantity:
            raise ValueError("position quantities are inconsistent")
        parsed_cost = price(self.average_cost)
        if parsed_cost < 0:
            raise ValueError("average cost must be non-negative")
        object.__setattr__(self, "average_cost", parsed_cost)
        object.__setattr__(self, "name", str(self.name).strip())


@dataclass(frozen=True)
class AccountFilePreview:
    preview_id: str
    file_id: str
    source_sha256: str
    source_name: str
    kind: AccountImportKind
    detected_mapping: Mapping[str, str]
    fill_preview: ImportPreview | None
    positions: tuple[ImportedPosition, ...]
    cash: Decimal | None
    as_of: date
    rejected_rows: tuple[ImportIssue, ...]
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind is AccountImportKind.FILLS:
            if self.fill_preview is None or self.positions:
                raise ValueError("fill preview payload is inconsistent")
        elif self.kind is AccountImportKind.POSITIONS:
            if self.fill_preview is not None or not self.positions:
                raise ValueError("position preview payload is inconsistent")
        else:
            raise ValueError("unknown account import kind")


_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("证券名称", "证券简称", "股票名称", "名称", "name"),
    "symbol": ("证券代码", "股票代码", "代码", "symbol", "code"),
    "fill_quantity": ("成交数量", "发生数量", "成交股数", "quantity"),
    "fill_price": ("成交价格", "成交均价", "成交价", "price"),
    "side": ("买卖标志", "买卖方向", "操作", "业务名称", "side"),
    "trade_date": ("成交日期", "发生日期", "交易日期", "trade_date"),
}


class AccountImportInbox:
    """Scan one fixed local directory and expose only generated file IDs."""

    def __init__(
        self,
        root: Path,
        *,
        max_files: int = _MAX_SCAN_FILES,
        max_rows: int = _MAX_IMPORT_ROWS,
        max_columns: int = _MAX_IMPORT_COLUMNS,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if max_files <= 0 or max_files > _MAX_SCAN_FILES:
            raise ValueError("max_files is outside the supported range")
        if max_rows <= 0 or max_rows > _MAX_IMPORT_ROWS:
            raise ValueError("max_rows is outside the supported range")
        if max_columns <= 0 or max_columns > _MAX_IMPORT_COLUMNS:
            raise ValueError("max_columns is outside the supported range")
        self.root = Path(root).resolve(strict=False)
        self.max_files = max_files
        self.max_rows = max_rows
        self.max_columns = max_columns
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._entries: dict[str, tuple[Path, str, int, int]] = {}

    def scan(self) -> tuple[InboxFile, ...]:
        self._entries = {}
        if not self.root.exists():
            return ()
        if not self._is_safe_directory(self.root):
            raise ValueError("account import inbox is not a regular directory")
        files: list[InboxFile] = []
        for path in sorted(self.root.iterdir(), key=lambda item: item.name.casefold()):
            if len(files) >= self.max_files:
                break
            if path.suffix.casefold() not in _SUPPORTED_SUFFIXES:
                continue
            if not self._is_safe_file(path):
                continue
            stat = path.stat()
            raw = path.read_bytes()
            if len(raw) > 25 * 1024 * 1024:
                continue
            source_sha256 = hashlib.sha256(raw).hexdigest()
            file_id = hashlib.sha256(
                _canonical_json(
                    {
                        "name": path.name,
                        "size": stat.st_size,
                        "modified_ns": stat.st_mtime_ns,
                        "source_sha256": source_sha256,
                    }
                ).encode("utf-8")
            ).hexdigest()
            self._entries[file_id] = (path, source_sha256, stat.st_size, stat.st_mtime_ns)
            files.append(
                InboxFile(
                    file_id=file_id,
                    file_name=path.name,
                    size_bytes=stat.st_size,
                    modified_ns=stat.st_mtime_ns,
                )
            )
        return tuple(files)

    def preview(self, file_id: str, *, default_trade_date: date) -> AccountFilePreview:
        path, source_sha256, size_bytes, modified_ns = self._entry(file_id)
        raw = path.read_bytes()
        current_stat = path.stat()
        if (
            hashlib.sha256(raw).hexdigest() != source_sha256
            or current_stat.st_size != size_bytes
            or current_stat.st_mtime_ns != modified_ns
        ):
            raise ValueError("file changed after scan")
        rows, _ = read_broker_file(path)
        self._validate_dimensions(rows)
        headers = tuple(rows[0].keys()) if rows else ()
        mapping = _detect_fill_mapping(headers)
        if not {"symbol", "quantity", "price"}.issubset(mapping):
            raise ValueError("account export columns are not recognized")
        fill_preview = preview_broker_rows(
            rows=rows,
            mapping=mapping,
            default_trade_date=default_trade_date,
            source_bytes=raw,
        )
        preview_id = hashlib.sha256(
            _canonical_json(
                {
                    "file_id": file_id,
                    "source_sha256": source_sha256,
                    "mapping": mapping,
                    "default_trade_date": default_trade_date.isoformat(),
                }
            ).encode("utf-8")
        ).hexdigest()
        return AccountFilePreview(
            preview_id=preview_id,
            file_id=file_id,
            source_sha256=source_sha256,
            source_name=path.name,
            kind=AccountImportKind.FILLS,
            detected_mapping=mapping,
            fill_preview=fill_preview,
            positions=(),
            cash=None,
            as_of=default_trade_date,
            rejected_rows=fill_preview.rejected_rows,
            warnings=(),
        )

    def verify_unchanged(self, preview: AccountFilePreview) -> None:
        path, _, _, _ = self._entry(preview.file_id)
        if not self._is_safe_file(path):
            raise ValueError("account import source is no longer a regular file")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != preview.source_sha256:
            raise ValueError("file changed after preview")

    def _entry(self, file_id: str) -> tuple[Path, str, int, int]:
        if not isinstance(file_id, str) or file_id not in self._entries:
            raise ValueError("unknown account import file")
        return self._entries[file_id]

    def _validate_dimensions(self, rows: list[dict[str, Any]]) -> None:
        if len(rows) > self.max_rows:
            raise ValueError("account import contains too many rows")
        if rows and len(rows[0]) > self.max_columns:
            raise ValueError("account import contains too many columns")

    @staticmethod
    def _is_safe_directory(path: Path) -> bool:
        return path.is_dir() and not _is_reparse(path)

    @staticmethod
    def _is_safe_file(path: Path) -> bool:
        return path.is_file() and not _is_reparse(path)


def _detect_fill_mapping(headers: tuple[str, ...]) -> dict[str, str]:
    normalized_headers = {str(header).strip(): str(header) for header in headers if str(header).strip()}
    result: dict[str, str] = {}
    for field in ("name", "symbol", "fill_quantity", "fill_price", "side", "trade_date"):
        matches = [
            normalized_headers[alias]
            for alias in _ALIASES[field]
            if alias in normalized_headers
        ]
        if len(matches) > 1:
            raise ValueError(f"ambiguous account export column for {field}")
        if matches:
            result[field] = matches[0]
    mapping = {
        key.removeprefix("fill_"): value
        for key, value in result.items()
        if key in {"name", "symbol", "fill_quantity", "fill_price", "side", "trade_date"}
    }
    return mapping


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = os.lstat(path).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & _REPARSE_POINT)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
