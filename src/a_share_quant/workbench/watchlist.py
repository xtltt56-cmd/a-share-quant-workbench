"""Project-local durable favorites keyed by exchange and stock code."""

from __future__ import annotations

import threading
from pathlib import Path

from a_share_quant.storage.atomic_json import read_checked_json, write_checked_json
from a_share_quant.storage.project_storage import ProjectStoragePolicy
from a_share_quant.workbench.chart_history import ChartHistory
from a_share_quant.workbench.security_catalog import EXCHANGES, SIX_DIGITS, SecurityCatalog

_MAX_FAVORITES = 200
_MAX_FILE_BYTES = 128 * 1024


class Watchlist:
    def __init__(self, repo_root: Path, catalog: SecurityCatalog) -> None:
        self.catalog = catalog
        self.history = ChartHistory(repo_root, catalog)
        self._policy = ProjectStoragePolicy(repo_root)
        self._path = ".runtime/watchlist/favorites.json"
        self._lock = threading.RLock()

    def _load(self) -> list[dict[str, str]]:
        path = self._policy.revalidate(self._path)
        if not path.exists():
            return []
        rows = read_checked_json(path, maximum_bytes=_MAX_FILE_BYTES)["rows"]
        if not isinstance(rows, list) or len(rows) > _MAX_FAVORITES:
            raise ValueError("自选文件无效")
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row) != {"exchange", "symbol", "name"}
                or not all(isinstance(value, str) for value in row.values())
                or row["exchange"] not in EXCHANGES | {""}
                or not SIX_DIGITS.fullmatch(row["symbol"])
                or not row["name"]
            ):
                raise ValueError("自选文件无效")
        return rows

    def _save(self, rows: list[dict[str, str]]) -> None:
        if len(rows) > _MAX_FAVORITES:
            raise ValueError("自选已达 200 只，请先移除部分股票")
        path = self._policy.revalidate(self._path)
        write_checked_json(path, {"rows": rows}, maximum_bytes=_MAX_FILE_BYTES)

    def items(self) -> list[dict]:
        with self._lock:
            result = []
            for item in self._load():
                exchange, symbol = item["exchange"], item["symbol"]
                known = (
                    self.catalog.lookup(exchange, symbol)
                    if exchange else self.catalog.resolve(symbol)
                )
                result.append(
                    {**(known or item), "saved_exchange": exchange, "verified": known is not None}
                )
            return result

    def add(self, exchange: str, symbol: str) -> None:
        known = self.catalog.lookup(exchange, symbol)
        if known is None:
            raise ValueError("该股票未在本机证券名录中通过核验")
        with self._lock:
            rows = self._load()
            retained = [
                item for item in rows
                if item["symbol"] != symbol or item["exchange"] not in {"", exchange}
            ]
            if len(rows) - len(retained) == 1 and known in rows:
                return
            self._save([*retained, known])

    def remove(self, exchange: str, symbol: str) -> None:
        with self._lock:
            rows = self._load()
            retained = [
                item for item in rows
                if (item["exchange"], item["symbol"]) != (exchange, symbol)
            ]
            if retained != rows:
                self._save(retained)

    def migrate_legacy(self, symbols: list[str]) -> None:
        if (
            not isinstance(symbols, list) or len(symbols) > _MAX_FAVORITES
            or any(
                not isinstance(symbol, str) or not SIX_DIGITS.fullmatch(symbol)
                for symbol in symbols
            )
        ):
            raise ValueError("旧自选数据无效")
        with self._lock:
            rows = self._load()
            existing_symbols = {item["symbol"] for item in rows}
            missing = [
                symbol for symbol in dict.fromkeys(symbols) if symbol not in existing_symbols
            ]
            if missing:
                rows.extend(
                    self.catalog.resolve(symbol)
                    or {"exchange": "", "symbol": symbol, "name": symbol}
                    for symbol in missing
                )
                self._save(rows)
