"""Single-stock chart downloads, independent of the formal daily data lake."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from a_share_quant.contracts.data import ProviderRequestError
from a_share_quant.data.normalization import normalize_daily_bars
from a_share_quant.data.providers.akshare import _normalize_tencent_stock_volume
from a_share_quant.data.realtime.sdk_process import call_sdk_process
from a_share_quant.market.trading_calendar import AShareTradingCalendar
from a_share_quant.storage.atomic_json import atomic_write_bytes
from a_share_quant.storage.project_storage import ProjectStoragePolicy
from a_share_quant.workbench.security_catalog import SecurityCatalog
from a_share_quant.workbench.stock_history import HISTORY_LIMITS, load_history, read_history_file

_LOG = logging.getLogger(__name__)


def fetch_chart_bars(exchange, symbol, start, end, stop):
    params = {
        "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"),
        "adjust": "",
    }
    prefix = {"SSE": "sh", "SZSE": "sz", "BSE": "bj"}[exchange]
    primary_params = {**params, "symbol": f"{prefix}{symbol}"}
    if exchange == "BSE":
        endpoint, source = "stock_zh_a_daily", "akshare-sina"
    else:
        endpoint, source = "stock_zh_a_hist_tx", "akshare-tencent"
        primary_params["timeout"] = 15
    try:
        raw = call_sdk_process(endpoint, primary_params, timeout_seconds=20, stop_event=stop)
        if not raw.empty:
            if source == "akshare-tencent":
                raw = _normalize_tencent_stock_volume(raw, symbol=symbol)
            return normalize_daily_bars(
                raw, symbol=symbol, source=source, data_version="unadjusted",
            )
    except (ProviderRequestError, OSError, ValueError):
        _LOG.info("个股日线主数据源暂不可用，尝试东方财富 (%s)", source)
    raw = call_sdk_process(
        "stock_zh_a_hist", {**params, "symbol": symbol, "period": "daily", "timeout": 15},
        timeout_seconds=22, stop_event=stop,
    )
    if not raw.empty:
        # The Eastmoney daily endpoint reports volume in lots.
        raw["成交量"] = pd.to_numeric(raw["成交量"], errors="raise") * 100
    return normalize_daily_bars(
        raw, symbol=symbol, source="akshare-eastmoney", data_version="unadjusted",
    )


class ChartHistory:
    def __init__(self, repo_root: Path, catalog: SecurityCatalog) -> None:
        self.catalog = catalog
        self._policy = ProjectStoragePolicy(repo_root)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._active: tuple[str, str, str] | None = None
        self._results: dict[tuple[str, str, str], dict] = {}

    def _path(self, exchange: str, symbol: str) -> Path:
        return self._policy.revalidate(f".runtime/watchlist/daily-bars/{exchange}/{symbol}.parquet")

    def read(self, exchange: str, symbol: str, limit: str) -> dict:
        formal = load_history(symbol, limit)
        if self.catalog.lookup(exchange, symbol) is None:
            raise ValueError("证券未通过名录校验")
        cached = read_history_file(self._path(exchange, symbol), symbol, limit)
        if cached["bars"] and (
            len(cached["bars"]) >= len(formal["bars"])
            or cached.get("end_date", "") > formal.get("end_date", "")
        ):
            return cached
        return formal

    def status(self, exchange: str, symbol: str, limit: str) -> dict:
        if limit not in HISTORY_LIMITS or self.catalog.lookup(exchange, symbol) is None:
            raise ValueError("股票代码、交易所或日线范围无效")
        with self._lock:
            key = (exchange, symbol, limit)
            return self._results.get(key, {"status": "IDLE"}).copy()

    def request(self, exchange: str, symbol: str, limit: str) -> dict:
        if limit not in HISTORY_LIMITS or self.catalog.lookup(exchange, symbol) is None:
            raise ValueError("股票代码、交易所或日线范围无效")
        if not self.catalog.allow_network:
            raise ValueError("当前为离线模式，请联网启动后补齐日线")
        key = (exchange, symbol, limit)
        with self._lock:
            if self._stop.is_set():
                raise ValueError("工作台正在退出")
            if self._active is not None:
                if self._active == key:
                    return self._results[key].copy()
                raise ValueError("另一项日线下载正在进行，请稍后再试")
            result = {"status": "RUNNING", "notice_zh": "正在下载该股票的日线…"}
            if len(self._results) >= 256 and key not in self._results:
                self._results.pop(next(iter(self._results)))
            self._results[key] = result
            self._active = key
            self._thread = threading.Thread(target=self._download, args=(key,), daemon=True)
            self._thread.start()
            return result.copy()

    def _download(self, key: tuple[str, str, str]) -> None:
        exchange, symbol, limit = key
        result = {"status": "FAILED", "notice_zh": "暂未取得该股票的有效日线，请稍后重试。"}
        try:
            end = AShareTradingCalendar().latest_completed_session(datetime.now(timezone.utc))
            start = end - timedelta(days=int(limit) * 2)
            bars = fetch_chart_bars(exchange, symbol, start, end, self._stop)
            if not self._stop.is_set() and not bars.empty:
                if not bars["date"].between(start, end).all():
                    raise ValueError("日线记录超出请求范围")
                if not bars["symbol"].eq(symbol).all():
                    raise ValueError("日线记录的证券代码不匹配")
                path = self._path(exchange, symbol)
                if path.exists():
                    existing = pd.read_parquet(path)
                    same_version = existing["data_version"].eq(bars["data_version"].iloc[0]).all()
                    same_source = existing["source"].eq(bars["source"].iloc[0]).all()
                    if same_version and same_source:
                        bars = (
                            pd.concat([existing, bars], ignore_index=True)
                            .drop_duplicates(subset=["date"], keep="last")
                            .sort_values("date")
                        )
                    elif len(bars) < len(existing):
                        raise ValueError("新数据口径不同且历史覆盖少于已保存数据")
                bars = bars.tail(max(map(int, HISTORY_LIMITS)))
                atomic_write_bytes(path, bars.to_parquet(index=False))
                result = {"status": "DONE", "notice_zh": "此股票的日线已更新。"}
        except (ProviderRequestError, OSError, ValueError, KeyError, TypeError) as exc:
            if not self._stop.is_set():
                _LOG.warning("自选日线下载失败，%s %s (%s)", exchange, symbol, type(exc).__name__)
        finally:
            with self._lock:
                self._active = None
                self._results[key] = result

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
