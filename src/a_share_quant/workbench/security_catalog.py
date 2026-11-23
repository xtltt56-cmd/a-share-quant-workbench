"""Background refresh and local search of listed A-share securities."""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from a_share_quant.contracts.data import ProviderRequestError
from a_share_quant.data.realtime.sdk_process import call_sdk_process
from a_share_quant.storage.atomic_json import read_checked_json, write_checked_json
from a_share_quant.storage.project_storage import ProjectStoragePolicy

EXCHANGES = frozenset({"SSE", "SZSE", "BSE"})
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
SIX_DIGITS = re.compile(r"[0-9]{6}")
_REFRESH_INTERVAL = timedelta(days=1)
_FAILURE_INTERVAL = timedelta(minutes=15)
_LOG = logging.getLogger(__name__)


def _search_key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def fetch_catalog(stop: threading.Event) -> list[dict[str, str]]:
    feeds = (
        ("SSE", "stock_info_sh_name_code", {"symbol": "主板A股"}, "证券代码", "证券简称"),
        ("SSE", "stock_info_sh_name_code", {"symbol": "科创板"}, "证券代码", "证券简称"),
        ("SZSE", "stock_info_sz_name_code", {"symbol": "A股列表"}, "A股代码", "A股简称"),
        ("BSE", "stock_info_bj_name_code", {}, "证券代码", "证券简称"),
    )
    rows = []
    for exchange, endpoint, kwargs, code, name in feeds:
        frame = call_sdk_process(endpoint, kwargs, timeout_seconds=45, stop_event=stop)
        if frame.empty:
            raise ValueError("证券名录缺少一个市场或板块")
        rows.extend(
            {"exchange": exchange, "symbol": str(symbol).strip(), "name": str(title).strip()}
            for symbol, title in frame[[code, name]].itertuples(index=False, name=None)
        )
    return rows


def _index_rows(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    result = {}
    for item in rows:
        exchange, symbol, name = item["exchange"], item["symbol"], item["name"]
        if (
            exchange not in EXCHANGES
            or not SIX_DIGITS.fullmatch(symbol)
            or not name or name.lower() in {"nan", "none", "<na>"}
        ):
            raise ValueError("证券名录字段无效")
        key = (exchange, symbol)
        if key in result and result[key]["name"] != name:
            raise ValueError("证券名录有冲突的代码")
        result[key] = {"exchange": exchange, "symbol": symbol, "name": name}
    if {exchange for exchange, _ in result} != EXCHANGES:
        raise ValueError("证券名录未覆盖沪深北")
    return result


class SecurityCatalog:
    def __init__(self, repo_root: Path, *, allow_network: bool) -> None:
        self._policy = ProjectStoragePolicy(repo_root)
        self._path = ".runtime/watchlist/catalog.json"
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._timer: threading.Thread | None = None
        self._attempted_at: datetime | None = None
        self.allow_network = allow_network
        self._updated_at: datetime | None = None
        self._rows: dict[tuple[str, str], dict[str, str]] = {}
        self._failed = False
        try:
            path = self._policy.authorize(self._path)
            if path.exists():
                snapshot = read_checked_json(path, maximum_bytes=MAX_SNAPSHOT_BYTES)
                updated = datetime.fromisoformat(snapshot["updated_at"])
                if updated.tzinfo is None or updated.utcoffset() is None:
                    raise ValueError("证券名录缺少时区")
                self._rows = _index_rows(snapshot["rows"])
                self._updated_at = updated
        except (OSError, ValueError, TypeError, KeyError):
            self._failed = True
            _LOG.warning("本地证券名录缓存未通过校验")

    def export(self) -> dict:
        with self._lock:
            if self._updated_at is None:
                raise ValueError("尚无通过校验的证券名录")
            return {
                "updated_at": self._updated_at.isoformat(),
                "rows": [item.copy() for item in self._rows.values()],
            }

    def lookup(self, exchange: str, symbol: str) -> dict[str, str] | None:
        with self._lock:
            item = self._rows.get((exchange, symbol))
            return item.copy() if item is not None else None

    def resolve(self, symbol: str) -> dict[str, str] | None:
        with self._lock:
            matches = [item for (_, code), item in self._rows.items() if code == symbol]
            return matches[0].copy() if len(matches) == 1 else None

    def search(self, query: str) -> dict:
        if len(query) > 80:
            raise ValueError("搜索词过长")
        key = _search_key(query)
        with self._lock:
            ranked = []
            if key:
                for item in self._rows.values():
                    code, name = item["symbol"], _search_key(item["name"])
                    if code == key:
                        rank = 0
                    elif code.startswith(key):
                        rank = 1
                    elif key in name:
                        rank = 2 if key == name else 3
                    else:
                        continue
                    ranked.append((rank, code, item["exchange"], item))
                ranked.sort()
            loading = self._thread is not None and self._thread.is_alive()
            stale = (
                self._updated_at is not None
                and datetime.now(timezone.utc) - self._updated_at >= _REFRESH_INTERVAL
            )
            return {
                "rows": [row.copy() for _, _, _, row in ranked[:30]],
                "matches": len(ranked),
                "updated_at": self._updated_at.isoformat() if self._updated_at else None,
                "source": "AKShare",
                "refreshing": loading,
                "refresh_failed": self._failed,
                "stale": stale,
                "allow_network": self.allow_network,
            }

    def request_refresh(self, *, operator_requested: bool = False) -> None:
        with self._lock:
            if not self.allow_network or self._stop.is_set():
                return
            if self._thread is not None and self._thread.is_alive():
                return
            now = datetime.now(timezone.utc)
            if not operator_requested:
                if self._updated_at and now - self._updated_at < _REFRESH_INTERVAL:
                    return
                if self._attempted_at and now - self._attempted_at < _FAILURE_INTERVAL:
                    return
            self._attempted_at = now
            self._thread = threading.Thread(target=self._refresh, daemon=True)
            self._thread.start()

    def _refresh(self) -> None:
        try:
            rows = _index_rows(fetch_catalog(self._stop))
            if self._stop.is_set():
                return
            updated_at = datetime.now(timezone.utc)
            with self._lock:
                for exchange in EXCHANGES:
                    previous = sum(key[0] == exchange for key in self._rows)
                    count = sum(key[0] == exchange for key in rows)
                    if 2 * count < previous:
                        raise ValueError("证券名录记录数量异常减少")
                body = {"updated_at": updated_at.isoformat(), "rows": list(rows.values())}
                path = self._policy.revalidate(self._path)
                write_checked_json(path, body, maximum_bytes=MAX_SNAPSHOT_BYTES)
                self._updated_at, self._rows, self._failed = updated_at, rows, False
        except (
            ProviderRequestError, OSError, ValueError, TypeError, KeyError
        ) as exc:
            with self._lock:
                self._failed = True
            if not self._stop.is_set():
                _LOG.warning("沪深北证券名录刷新失败 (%s)", type(exc).__name__)

    def start(self) -> None:
        if not self.allow_network:
            return
        self.request_refresh()
        self._timer = threading.Thread(target=self._periodic_refresh, daemon=True)
        self._timer.start()

    def _periodic_refresh(self) -> None:
        while not self._stop.wait(_FAILURE_INTERVAL.total_seconds()):
            self.request_refresh()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout=3)
        if self._timer is not None:
            self._timer.join(timeout=1)
