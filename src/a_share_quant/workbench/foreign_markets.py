"""Market-scoped state for US and Hong Kong end-of-day research."""

from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

import pandas as pd

from a_share_quant.account.foreign_ledger import (
    account_snapshot,
    estimated_hk_levies,
    prepare_event,
)
from a_share_quant.data.providers.foreign_daily import (
    ALPACA_SOURCE,
    SINA_SOURCE,
    SINA_US_SOURCE,
    ForeignDataError,
    alpaca_credentials,
    alpaca_daily,
    alpaca_iex_trades,
    hkex_board_lots,
    sina_daily,
    sina_hk_trades,
)
from a_share_quant.market.foreign_sessions import TIMEZONES, market_session
from a_share_quant.storage.atomic_json import read_checked_json, write_checked_json
from a_share_quant.storage.project_storage import ProjectStoragePolicy
from a_share_quant.workbench.foreign_catalog import (
    fetch_catalog,
    legacy_listing_id,
    normalize_code,
    public_security,
    rank_matches,
    search_key,
    update_listings,
)

MARKET_META = {
    "HK": {"name": "港股", "currency": "HKD", "source": SINA_SOURCE},
    "US": {"name": "美股", "currency": "USD", "source": SINA_US_SOURCE},
}

_HK = (
    ("00001", "长和"), ("00002", "中电控股"), ("00003", "香港中华煤气"),
    ("00005", "汇丰控股"), ("00016", "新鸿基地产"),
    ("00027", "银河娱乐"), ("00066", "港铁公司"), ("00175", "吉利汽车"),
    ("00267", "中信股份"), ("00288", "万洲国际"), ("00322", "康师傅控股"),
    ("00388", "香港交易所"), ("00669", "创科实业"), ("00700", "腾讯控股"),
    ("00883", "中国海洋石油"), ("00939", "建设银行"),
    ("00941", "中国移动"), ("00981", "中芯国际"), ("00992", "联想集团"),
    ("01024", "快手"), ("01211", "比亚迪股份"), ("01299", "友邦保险"),
    ("01810", "小米集团"), ("02318", "中国平安"), ("03690", "美团"),
    ("09618", "京东集团"), ("09961", "携程集团"), ("09988", "阿里巴巴"),
    ("09999", "网易"), ("02020", "安踏体育"),
)
_NASDAQ = (
    ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "NVIDIA"), ("AMZN", "Amazon"),
    ("GOOGL", "Alphabet A"), ("META", "Meta Platforms"), ("TSLA", "Tesla"),
    ("AVGO", "Broadcom"), ("COST", "Costco"), ("NFLX", "Netflix"),
    ("AMD", "AMD"), ("INTC", "Intel"), ("QCOM", "Qualcomm"), ("PEP", "PepsiCo"),
    ("ADBE", "Adobe"), ("CSCO", "Cisco"), ("AMAT", "Applied Materials"), ("WMT", "Walmart"),
)
_NYSE = (
    ("BRK.B", "Berkshire Hathaway B"), ("JPM", "JPMorgan Chase"), ("V", "Visa"),
    ("XOM", "Exxon Mobil"), ("JNJ", "Johnson & Johnson"),
    ("PG", "Procter & Gamble"), ("HD", "Home Depot"), ("UNH", "UnitedHealth"),
    ("KO", "Coca-Cola"), ("DIS", "Disney"), ("CAT", "Caterpillar"),
    ("CRM", "Salesforce"), ("LLY", "Eli Lilly"),
)
UNIVERSES = {
    "HK": {symbol: {"symbol": symbol, "name": name, "exchange": "XHKG"}
           for symbol, name in _HK},
    "US": {symbol: {"symbol": symbol, "name": name, "exchange": exchange}
           for exchange, entries in (("XNAS", _NASDAQ), ("XNYS", _NYSE))
           for symbol, name in entries},
}
_SNAPSHOT_BYTES = 4 * 1024 * 1024
_FAVORITES_BYTES = 128 * 1024
_LEDGER_BYTES = 2 * 1024 * 1024
_CATALOG_BYTES = 24 * 1024 * 1024
_HISTORY_BYTES = 256 * 1024
_BAR_FIELDS = ("open", "high", "low", "close", "volume", "adjusted_close")


def _checked_bars(rows: list[dict], allowed_dates: set[str]) -> tuple[list[dict], int]:
    bars, seen, rejected = {}, set(), set()
    for row in rows:
        day = row["date"]
        if day not in allowed_dates:
            continue
        try:
            values = {key: float(row[key]) for key in _BAR_FIELDS}
        except (ValueError, TypeError, KeyError):
            seen.add(day)
            rejected.add(day)
            continue
        if (
            day in seen or not all(math.isfinite(x) and x > 0 for x in values.values())
            or not (
                values["low"] <= min(values["open"], values["close"])
                <= max(values["open"], values["close"]) <= values["high"]
            )
        ):
            rejected.add(day)
        else:
            bars[day] = {"date": day, **values}
        seen.add(day)
    return [bars[day] for day in sorted(bars.keys() - rejected)], len(rejected)


def _ranking(snapshot: dict, pool: dict, recent_dates: list[str], next_session: str) -> list[dict]:
    features, details = {}, {}
    for symbol, rows in snapshot.items():
        recent = rows[-61:]
        if symbol not in pool or len(recent) != 61 or [x["date"] for x in recent] != recent_dates:
            continue
        adjusted = pd.Series([bar["adjusted_close"] for bar in recent])
        close = adjusted.iloc[-1]
        ret20, ret60 = close / adjusted.iloc[-21] - 1, close / adjusted.iloc[0] - 1
        volatility = adjusted.pct_change().tail(20).std()
        features[symbol] = (ret20, ret60, -volatility)
        details[symbol] = {
            **pool[symbol], "close": recent[-1]["close"], "ret20": ret20, "ret60": ret60,
            "volatility20": volatility, "as_of": recent[-1]["date"], "next_session": next_session,
        }
    if not features:
        return []
    samples = pd.DataFrame.from_dict(features, orient="index")
    scores = samples.rank(pct=True).mul([0.45, 0.35, 0.20]).sum(axis=1) * 100
    ordered = sorted(features, key=lambda symbol: (-scores[symbol], symbol))
    return [dict(details[symbol], score=round(scores[symbol], 2)) for symbol in ordered]


class ForeignMarkets:
    def __init__(self, root: Path, *, allow_network: bool) -> None:
        self.root = root
        self.allow_network = allow_network
        self.policy = ProjectStoragePolicy(root)
        self.locks = {market: threading.RLock() for market in MARKET_META}
        self.running: set[str] = set()
        self.failures: dict[str, str] = {}
        self.stop = threading.Event()
        self.quote_locks = {market: threading.Lock() for market in MARKET_META}
        self.quote_cache: dict[str, tuple[datetime, tuple[str, ...], dict]] = {}
        self.catalogs: dict[str, dict] = {}
        self.catalog_running: set[str] = set()
        self.catalog_errors: dict[str, str] = {}
        self.catalog_attempts: dict[str, float] = {}
        self.history_executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="foreign-stock"
        )
        self.history_lock = threading.Lock()
        self.history_pending: set[str] = set()
        self.history_results: dict[str, tuple[float, str, str]] = {}

    def close(self) -> None:
        with self.history_lock:
            self.stop.set()
            self.history_executor.shutdown(wait=False, cancel_futures=True)

    def _load(self, market: str, name: str, maximum: int) -> dict:
        path = self.policy.revalidate(f".runtime/foreign-markets/{market}/{name}.json")
        return read_checked_json(path, maximum_bytes=maximum) if path.exists() else {}

    def _save(self, market: str, name: str, data: dict, maximum: int) -> None:
        path = self.policy.revalidate(f".runtime/foreign-markets/{market}/{name}.json")
        write_checked_json(path, data, maximum_bytes=maximum)

    def _catalog(self, market: str) -> dict:
        if market not in self.catalogs:
            try:
                self.catalogs[market] = self._load(market, "catalog", _CATALOG_BYTES)
            except (ValueError, OSError):
                self.catalog_errors[market] = "本地名录校验失败，请更新名录"
                self.catalogs[market] = {}
        return self.catalogs[market]

    def _current_listings(self, market: str) -> dict:
        catalog = self._catalog(market)
        if catalog:
            return catalog["rows"]
        return {
            symbol: {**row, "currency": MARKET_META[market]["currency"],
                     "listing_id": legacy_listing_id(market, symbol),
                     "keys": [search_key(row["name"])]}
            for symbol, row in UNIVERSES[market].items()
        }

    def _lookup(self, market: str, symbol: str, listing_id: str = "") -> tuple[dict | None, bool]:
        row = self._current_listings(market).get(symbol)
        if row is not None and (not listing_id or row["listing_id"] == listing_id):
            return row, True
        if listing_id:
            archived = self._catalog(market).get("archived", {}).get(listing_id)
            if archived and archived["symbol"] == symbol:
                return archived, False
            seed = UNIVERSES[market].get(symbol)
            if seed and listing_id == legacy_listing_id(market, symbol):
                return {
                    **seed, "listing_id": listing_id, "currency": MARKET_META[market]["currency"],
                }, False
        return None, False

    def refresh_catalog(self, market: str, *, force: bool = False) -> None:
        if not self.allow_network or self.stop.is_set():
            return
        with self.locks[market]:
            clock = datetime.now(timezone.utc)
            catalog = self._catalog(market)
            updated = catalog.get("updated_at")
            if market in self.catalog_running:
                return
            if not force and (
                (updated and clock - datetime.fromisoformat(updated) < timedelta(days=1))
                or monotonic() - self.catalog_attempts.get(market, -60) < 60
            ):
                return
            self.catalog_attempts[market] = monotonic()
            self.catalog_running.add(market)
            threading.Thread(
                target=self._refresh_catalog, args=(market,), daemon=True,
                name=f"foreign-catalog-{market}",
            ).start()

    def _refresh_catalog(self, market: str) -> None:
        try:
            as_of, rows = fetch_catalog(market)
            with self.locks[market]:
                if self.stop.is_set():
                    return
                previous = self._catalog(market)
                too_small = len(rows) < len(previous.get("rows", {})) * 0.8
                if as_of < previous.get("as_of", "") or too_small:
                    raise ForeignDataError("新证券名录日期或数量异常，继续使用已有名录")
                current, archived = update_listings(
                    market, previous, rows, UNIVERSES[market], as_of
                )
                catalog = {
                    "as_of": as_of, "updated_at": datetime.now(timezone.utc).isoformat(),
                    "rows": current, "archived": archived,
                }
                self._save(market, "catalog", catalog, _CATALOG_BYTES)
                self.catalogs[market] = catalog
                self.catalog_errors.pop(market, None)
        except ForeignDataError as exc:
            with self.locks[market]:
                self.catalog_errors[market] = str(exc)
        except (OSError, KeyError, ValueError, TypeError, ImportError):
            with self.locks[market]:
                self.catalog_errors[market] = "证券名录更新失败，已保留已有名录"
        finally:
            with self.locks[market]:
                self.catalog_running.discard(market)

    def search(self, market: str, query: str) -> dict:
        if len(query) > 80:
            raise ValueError("搜索词过长")
        self.refresh_catalog(market)
        with self.locks[market]:
            catalog = self._catalog(market)
            rows = self._current_listings(market)
            matches = rank_matches(market, rows, query)
            return {
                "rows": [public_security(row) for row in matches[:30]], "matches": len(matches),
                "size": len(rows), "complete_catalog": bool(catalog), "as_of": catalog.get("as_of"),
                "running": market in self.catalog_running, "error": self.catalog_errors.get(market),
                "network": self.allow_network,
            }

    def _favorites(self, market: str) -> list[dict]:
        saved = self._load(market, "favorites", _FAVORITES_BYTES)
        if "items" in saved:
            return saved["items"]
        return [
            {**UNIVERSES[market].get(symbol, {"symbol": symbol, "name": symbol}),
             "listing_id": legacy_listing_id(market, symbol),
             "currency": MARKET_META[market]["currency"]}
            for symbol in saved.get("symbols", [])
        ]

    def _history(self, market: str, row: dict, snapshot: dict) -> dict:
        listing_id, symbol = row["listing_id"], row["symbol"]
        try:
            history = self._load(market, f"history/{listing_id}", _HISTORY_BYTES)
        except (OSError, ValueError):
            history = {}
        if listing_id == legacy_listing_id(market, symbol):
            bars = snapshot.get("bars", {}).get(symbol, [])
            if bars and (not history or bars[-1]["date"] > history["bars"][-1]["date"]):
                source = snapshot.get("source", MARKET_META[market]["source"])
                return {"source": source, "bars": bars}
        return history

    def state(self, market: str, *, now: datetime | None = None) -> dict:
        self.refresh_catalog(market)
        clock = now or datetime.now(timezone.utc)
        with self.locks[market]:
            snapshot = self._load(market, "daily", _SNAPSHOT_BYTES)
            favorites = self._favorites(market)
            pool = {
                symbol: {**row, "listing_id": legacy_listing_id(market, symbol)}
                for symbol, row in UNIVERSES[market].items()
            }
            configured = market == "US" and bool(alpaca_credentials(self.root))
            preferred = ALPACA_SOURCE if configured else MARKET_META[market]["source"]
            source = snapshot.get("source", preferred)
            session, cutoff, next_day, schedule = market_session(
                market, clock, vendor_day_bar=source == ALPACA_SOURCE
            )
            as_of = snapshot.get("as_of")
            fresh = as_of == cutoff.isoformat()
            recent = [] if not as_of else [
                day.date().isoformat() for day in schedule.loc[:as_of].index[-61:]
            ]
            rank_session = next_day
            if as_of and not fresh:
                following = schedule.index[schedule.index > pd.Timestamp(as_of)]
                if len(following):
                    rank_session = following[0].date()
            active_pool = {
                symbol: row for symbol, row in pool.items()
                if self._lookup(market, symbol, legacy_listing_id(market, symbol))[1]
            }
            ranks = _ranking(
                snapshot.get("bars", {}), active_pool, recent, rank_session.isoformat()
            )
            eligible = {row["symbol"] for row in ranks}
            favorited = []
            for saved in favorites:
                row, active = self._lookup(market, saved["symbol"], saved["listing_id"])
                row = row or saved
                bars = self._history(market, row, snapshot).get("bars", [])
                favorited.append({**public_security(row), "active": active,
                                  "bar": bars[-1] if bars else None})
            return {
                **MARKET_META[market], "market": market, "alpaca_configured": configured,
                "source": source, "next_source": preferred,
                "network": self.allow_network, "session": session, "expected": cutoff.isoformat(),
                "as_of": as_of, "fresh": fresh, "updated_at": snapshot.get("updated_at"),
                "running": market in self.running, "error": self.failures.get(market),
                "pool": [{**item, "eligible": symbol in eligible} for symbol, item in pool.items()],
                "favorites": favorited, "coverage": len(ranks),
                "candidates": ranks if fresh and len(ranks) * 5 >= len(pool) * 4 else [],
                "historical_candidates": (
                    ranks if as_of and as_of < cutoff.isoformat()
                    and len(ranks) * 5 >= len(pool) * 4 else []
                ),
                "symbol_errors": snapshot.get("symbol_errors", {}),
            }

    def security(
        self, market: str, symbol: str, listing_id: str = "", *, force: bool = False
    ) -> dict:
        symbol = normalize_code(market, symbol)
        with self.locks[market]:
            row, active = self._lookup(market, symbol, listing_id)
            if row is None:
                raise ValueError("未在本市场证券名录中找到该股票")
            snapshot = self._load(market, "daily", _SNAPSHOT_BYTES)
            history = self._history(market, row, snapshot)
            source = history.get("source", MARKET_META[market]["source"])
            bars = history.get("bars", [])
            _, cutoff, _, _ = market_session(
                market, datetime.now(timezone.utc), vendor_day_bar=source == ALPACA_SOURCE
            )
            fresh = bool(bars and bars[-1]["date"] == str(cutoff))
            status = "ready" if fresh else "missing"
            notice = ""
            if not active:
                status, notice = "inactive", "该证券已移出当前名录，可查看原证券的本地记录"
            elif not self.allow_network:
                if not fresh:
                    status, notice = "offline", "当前为离线模式，暂不能更新该股票的日线"
            else:
                status, notice = self._request_history(market, row, force=force, fresh=fresh)
            return {
                **MARKET_META[market], **public_security(row), "market": market, "active": active,
                "source": source, "bars": bars, "status": status, "notice": notice,
                "expected": str(cutoff), "research_sample": (
                    symbol in UNIVERSES[market]
                    and row["listing_id"] == legacy_listing_id(market, symbol)
                ),
                **self._security_rules(market, row, snapshot),
            }

    def _request_history(
        self, market: str, row: dict, *, force: bool, fresh: bool
    ) -> tuple[str, str]:
        listing_id = row["listing_id"]
        with self.history_lock:
            if listing_id in self.history_pending:
                return "loading", "正在下载该股票的日线"
            prior = self.history_results.get(listing_id)
            if not force and prior and monotonic() - prior[0] < 120:
                return prior[1], prior[2]
            if fresh and not force:
                return "ready", ""
            if self.stop.is_set() or len(self.history_pending) >= 16:
                return "busy", "日线下载任务较多，请稍后重试"
            self.history_pending.add(listing_id)
            if len(self.history_results) >= 256:
                self.history_results.pop(next(iter(self.history_results)))
            self.history_executor.submit(self._download_history, market, row)
        return "loading", "正在下载该股票的日线"

    def _download_history(self, market: str, row: dict) -> None:
        symbol, listing_id = row["symbol"], row["listing_id"]
        status, notice = "error", "暂时无法取得该股票的日线，请稍后重试"
        try:
            credentials = alpaca_credentials(self.root) if market == "US" else {}
            source = ALPACA_SOURCE if credentials else MARKET_META[market]["source"]
            now = datetime.now(timezone.utc)
            _, cutoff, _, schedule = market_session(
                market, now, vendor_day_bar=bool(credentials)
            )
            start = cutoff - timedelta(days=370)
            if row.get("history_from"):
                start = max(start, datetime.fromisoformat(row["history_from"]).date())
            if start > cutoff:
                raise ForeignDataError("该证券尚无符合当前上市身份的完整交易日")
            allowed = {
                day.date().isoformat() for day in schedule.loc[str(start):str(cutoff)].index
            }
            if credentials:
                rows = alpaca_daily([symbol], start, cutoff, credentials).get(symbol, [])
            else:
                rows = sina_daily(symbol, start, cutoff, market=market)
            bars, rejected = _checked_bars(rows, allowed)
            if not bars:
                raise ForeignDataError("免费行情源暂无该证券的有效日线")
            with self.locks[market]:
                if self.stop.is_set():
                    return
                snapshot = self._load(market, "daily", _SNAPSHOT_BYTES)
                previous = self._history(market, row, snapshot).get("bars", [])
                if not previous or previous[-1]["date"] <= bars[-1]["date"]:
                    self._save(market, f"history/{listing_id}", {
                        "source": source, "adjustment": "raw-ohlc-adjusted-close",
                        "market": market, "listing_id": listing_id, "bars": bars,
                    }, _HISTORY_BYTES)
            status, notice = "ready", ""
            if bars[-1]["date"] < str(cutoff):
                status, notice = "stale", "当前来源尚无最近交易日日线，显示已有数据"
            if rejected:
                notice = (notice + " · " if notice else "") + f"已排除 {rejected} 个异常交易日"
        except ForeignDataError as exc:
            notice = str(exc)
        except Exception:
            pass
        finally:
            with self.history_lock:
                self.history_pending.discard(listing_id)
                self.history_results[listing_id] = (monotonic(), status, notice)

    @staticmethod
    def _security_rules(market: str, row: dict, snapshot: dict) -> dict:
        if market == "US":
            return {"board_lot": 1}
        metadata = snapshot.get("exchange_metadata", {})
        official = {key: row[key] for key in ("board_lot", "board_lot_as_of", "stamp_duty")
                    if key in row}
        if official:
            return official
        if row["listing_id"] != legacy_listing_id(market, row["symbol"]):
            return {}
        return {
            "board_lot_as_of": metadata.get("as_of"),
            **metadata.get("items", {}).get(row["symbol"], {}),
        }

    def account(self, market: str) -> dict:
        now = datetime.now(timezone.utc)
        with self.locks[market]:
            events = self._load(market, "ledger", _LEDGER_BYTES).get("events", [])
            return self._account_view(market, events, now)

    def _account_view(self, market: str, events: list[dict], now: datetime) -> dict:
        today = now.astimezone(ZoneInfo(TIMEZONES[market])).date().isoformat()
        records = [
            dict(event, listing_id=event.get(
                "listing_id", legacy_listing_id(market, event["symbol"])
            ))
            if "symbol" in event else event for event in events
        ]
        result = account_snapshot(records, today)
        snapshot = self._load(market, "daily", _SNAPSHOT_BYTES)
        for holding in result["positions"]:
            symbol = holding["symbol"]
            listing_id = holding["listing_id"]
            row, active = self._lookup(market, symbol, listing_id)
            if row is None:
                row = next(
                    (record["security"] for record in reversed(records)
                     if record.get("listing_id") == listing_id and record.get("security")),
                    {"symbol": symbol, "name": symbol, "listing_id": listing_id,
                     "currency": MARKET_META[market]["currency"]},
                )
            rows = self._history(market, row, snapshot).get("bars", [])
            holding.update(public_security(row), active=active, bar=rows[-1] if rows else None)
        return {**result, "today": today, "history": events[-20:][::-1]}

    def record(self, market: str, data: dict, *, commit: bool) -> dict:
        now = datetime.now(timezone.utc)
        with self.locks[market]:
            events = self._load(market, "ledger", _LEDGER_BYTES).get("events", [])
            symbol = data.get("symbol", "") if isinstance(data, dict) else ""
            if not isinstance(symbol, str):
                raise ValueError("股票代码无效")
            symbol = normalize_code(market, symbol)
            requested_id = data.get("listing_id", "") if isinstance(data, dict) else ""
            if not isinstance(requested_id, str):
                raise ValueError("证券标识无效")
            row, active = self._lookup(market, symbol, requested_id)
            if row and row["currency"] != MARKET_META[market]["currency"]:
                raise ValueError("证券柜台币种与本市场账本币种不同")
            fields = ({key: value for key, value in data.items() if key != "listing_id"}
                      if isinstance(data, dict) else data)
            if isinstance(fields, dict) and "symbol" in fields:
                fields["symbol"] = symbol
            event = prepare_event(market, fields, {symbol: row} if row and active else {}, now)
            existing = next((record for record in events if record["id"] == event["id"]), None)
            if existing is not None:
                saved_id = existing.get("listing_id", legacy_listing_id(market, symbol))
                if any(existing.get(key) != value for key, value in event.items()) or (
                    row and saved_id != row["listing_id"]
                ):
                    raise ValueError("该记录编号已用于另一笔交易")
            if "symbol" in event:
                event.update(listing_id=row["listing_id"], security=public_security(row))
            updated = events if existing else [*events, event]
            result = self._account_view(market, updated, now)
            if commit and existing is None:
                self._save(market, "ledger", {"events": updated}, _LEDGER_BYTES)
            if event["action"] != "deposit":
                snapshot = self._load(market, "daily", _SNAPSHOT_BYTES)
                rules = self._security_rules(market, row, snapshot)
                result.update(rules)
                if market == "HK" and rules.get("board_lot_as_of") == result["today"]:
                    result["levies"] = estimated_hk_levies(event, rules)
            return {**result, "event": event, "committed": commit}

    def favorite(self, market: str, symbol: str, action: str, listing_id: str = "") -> None:
        symbol = normalize_code(market, symbol)
        if action not in {"add", "remove"}:
            raise ValueError("自选操作或股票代码无效")
        with self.locks[market]:
            row, active = self._lookup(market, symbol, listing_id)
            if action == "add" and not active:
                raise ValueError("请从当前市场证券名录中选择股票")
            target_id = listing_id or (
                row["listing_id"] if row else legacy_listing_id(market, symbol)
            )
            items = [item for item in self._favorites(market) if item["listing_id"] != target_id]
            if action == "add":
                if len(items) >= 200:
                    raise ValueError("自选已达 200 只，请先移除部分股票")
                items.append(public_security(row))
            self._save(market, "favorites", {"items": items}, _FAVORITES_BYTES)
        if action == "add" and self.allow_network:
            try:
                self.security(market, symbol, target_id)
            except (OSError, ValueError):
                # A cache problem must not invalidate a favorite already saved.
                pass

    def quotes(self, market: str) -> dict:
        source = "Alpaca IEX 单交易所成交" if market == "US" else "新浪财经港股快照"
        with self.locks[market]:
            favorites = tuple(
                item["symbol"] for item in self._favorites(market)
                if self._lookup(market, item["symbol"], item["listing_id"])[1]
            )
        empty = {"source": source, "quotes": {}}
        if not favorites or not self.allow_network:
            return empty
        with self.quote_locks[market]:
            now = datetime.now(timezone.utc)
            status, _, _, _ = market_session(market, now)
            if status != "常规交易时段":
                return {**empty, "notice_zh": status + "，显示最近日线收盘"}
            cached = self.quote_cache.get(market)
            if cached and cached[1] == favorites and (now - cached[0]).total_seconds() < 15:
                return cached[2]
            credentials = alpaca_credentials(self.root) if market == "US" else {}
            if market == "US" and not credentials:
                return {**empty, "notice_zh": "配置 Alpaca Basic 密钥后可查看 IEX 单交易所成交"}
            try:
                ticks = {}
                for start in range(0, len(favorites), 30):
                    batch = list(favorites[start:start + 30])
                    try:
                        ticks.update(alpaca_iex_trades(batch, credentials) if credentials
                                     else sina_hk_trades(batch))
                    except ForeignDataError:
                        if len(favorites) <= 30:
                            raise
                result = {**empty, "quotes": {}}
                received = datetime.now(timezone.utc)
                for symbol in favorites:
                    tick = ticks.get(symbol)
                    if not tick:
                        continue
                    value, raw_time = tick
                    event = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                    age = (received - event).total_seconds()
                    if math.isfinite(float(value)) and float(value) > 0 and -3 <= age:
                        result["quotes"][symbol] = {
                            "price": float(value), "timestamp": event.isoformat(),
                            "fresh": age <= 90,
                        }
            except ForeignDataError as error:
                result = {**empty, "notice_zh": str(error)}
            except (ValueError, KeyError, TypeError):
                result = {**empty, "notice_zh": "盘中快照字段无效"}
            self.quote_cache[market] = (now, favorites, result)
            return result

    def refresh(self, market: str) -> None:
        if not self.allow_network:
            raise ValueError("当前工作台以离线模式运行")
        credentials = alpaca_credentials(self.root) if market == "US" else {}
        with self.locks[market]:
            if market in self.running:
                return
            self.running.add(market)
            self.failures.pop(market, None)
            threading.Thread(
                target=self._refresh, args=(market, credentials), daemon=True,
                name=f"foreign-daily-{market}",
            ).start()

    def _refresh(self, market: str, credentials: dict) -> None:
        try:
            self._download(market, credentials)
        except ForeignDataError as exc:
            with self.locks[market]:
                self.failures[market] = str(exc)
        except Exception:
            with self.locks[market]:
                self.failures[market] = "日线刷新失败，请检查行情源或本地缓存"
        finally:
            with self.locks[market]:
                self.running.discard(market)

    def _download(self, market: str, credentials: dict) -> None:
        now = datetime.now(timezone.utc)
        source = ALPACA_SOURCE if credentials else MARKET_META[market]["source"]
        _, cutoff, _, schedule = market_session(market, now, vendor_day_bar=source == ALPACA_SOURCE)
        start = cutoff - timedelta(days=370)
        allowed = {day.date().isoformat() for day in schedule.loc[str(start):str(cutoff)].index}
        with self.locks[market]:
            symbols = [
                symbol for symbol in UNIVERSES[market]
                if self._lookup(market, symbol, legacy_listing_id(market, symbol))[1]
            ]
        errors, bars = {}, {}

        def validate(rows: list[dict]) -> tuple[list[dict], str | None]:
            clean, rejected = _checked_bars(rows, allowed)
            notice = f"已排除 {rejected} 个异常交易日" if rejected else None
            return clean, notice

        def read_sina(symbol: str) -> tuple[str, list[dict], str | None]:
            if self.stop.is_set():
                return symbol, [], "刷新已停止"
            try:
                clean, notice = validate(sina_daily(symbol, start, cutoff, market=market))
                return symbol, clean, notice
            except ForeignDataError as exc:
                return symbol, [], str(exc)
            except Exception:
                return symbol, [], "新浪日线或复权格式异常"

        if source != ALPACA_SOURCE:
            with ThreadPoolExecutor(max_workers=4) as executor:
                for symbol, rows, error in executor.map(read_sina, symbols):
                    if rows:
                        bars[symbol] = rows
                    if error:
                        errors[symbol] = error
        else:
            for symbol, rows in alpaca_daily(symbols, start, cutoff, credentials).items():
                try:
                    clean, notice = validate(rows)
                    if clean:
                        bars[symbol] = clean
                    if notice:
                        errors[symbol] = notice
                except (ValueError, TypeError, KeyError):
                    errors[symbol] = "日线数值或复权配对无效"
        for symbol in symbols:
            if symbol not in bars or bars[symbol][-1]["date"] != cutoff.isoformat():
                errors.setdefault(symbol, "目标交易日日线尚未到齐")
        if not bars:
            raise ForeignDataError("本次没有通过校验的日线")
        exchange_metadata = None
        if market == "HK" and not self.stop.is_set():
            try:
                exchange_metadata = hkex_board_lots(symbols)
            except (ForeignDataError, OSError, ValueError, KeyError):
                pass  # The quotes remain usable and an older metadata timestamp remains visible.
        with self.locks[market]:
            if self.stop.is_set():
                return
            previous = self._load(market, "daily", _SNAPSHOT_BYTES)
            if previous.get("source", MARKET_META[market]["source"]) == source:
                for symbol, rows in previous.get("bars", {}).items():
                    if rows and (symbol not in bars or rows[-1]["date"] > bars[symbol][-1]["date"]):
                        bars[symbol] = rows
            as_of = max(rows[-1]["date"] for rows in bars.values())
            self._save(market, "daily", {
                "as_of": as_of, "updated_at": now.isoformat(), "bars": bars,
                "symbol_errors": errors,
                "source": source,
                "exchange_metadata": exchange_metadata or previous.get("exchange_metadata", {}),
            }, _SNAPSHOT_BYTES)
