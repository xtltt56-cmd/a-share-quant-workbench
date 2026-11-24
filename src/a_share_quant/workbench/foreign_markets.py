"""Market-scoped state for US and Hong Kong end-of-day research."""

from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
_FAVORITES_BYTES = 8192
_LEDGER_BYTES = 2 * 1024 * 1024
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

    def close(self) -> None:
        self.stop.set()

    def _load(self, market: str, name: str, maximum: int) -> dict:
        path = self.policy.revalidate(f".runtime/foreign-markets/{market}/{name}.json")
        return read_checked_json(path, maximum_bytes=maximum) if path.exists() else {}

    def _save(self, market: str, name: str, data: dict, maximum: int) -> None:
        path = self.policy.revalidate(f".runtime/foreign-markets/{market}/{name}.json")
        write_checked_json(path, data, maximum_bytes=maximum)

    def state(self, market: str, *, now: datetime | None = None) -> dict:
        clock = now or datetime.now(timezone.utc)
        with self.locks[market]:
            snapshot = self._load(market, "daily", _SNAPSHOT_BYTES)
            favorites = self._load(market, "favorites", _FAVORITES_BYTES).get("symbols", [])
            pool = UNIVERSES[market]
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
            ranks = _ranking(snapshot.get("bars", {}), pool, recent, rank_session.isoformat())
            eligible = {row["symbol"] for row in ranks}
            favorited = []
            for symbol in favorites:
                if symbol in pool:
                    rows = snapshot.get("bars", {}).get(symbol, [])
                    favorited.append({**pool[symbol], "bar": rows[-1] if rows else None})
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

    def security(self, market: str, symbol: str) -> dict:
        known = UNIVERSES[market].get(symbol)
        if known is None:
            raise ValueError("股票代码不在本市场研究样本内")
        with self.locks[market]:
            snapshot = self._load(market, "daily", _SNAPSHOT_BYTES)
            return {**known, **MARKET_META[market], "market": market,
                    "source": snapshot.get("source", MARKET_META[market]["source"]),
                    "bars": snapshot.get("bars", {}).get(symbol, []),
                    **self._security_rules(market, symbol, snapshot)}

    @staticmethod
    def _security_rules(market: str, symbol: str, snapshot: dict) -> dict:
        if market == "US":
            return {"board_lot": 1}
        metadata = snapshot.get("exchange_metadata", {})
        return {
            "board_lot_as_of": metadata.get("as_of"),
            **metadata.get("items", {}).get(symbol, {}),
        }

    def account(self, market: str) -> dict:
        now = datetime.now(timezone.utc)
        with self.locks[market]:
            events = self._load(market, "ledger", _LEDGER_BYTES).get("events", [])
            return self._account_view(market, events, now)

    def _account_view(self, market: str, events: list[dict], now: datetime) -> dict:
        today = now.astimezone(ZoneInfo(TIMEZONES[market])).date().isoformat()
        result = account_snapshot(events, today)
        snapshot = self._load(market, "daily", _SNAPSHOT_BYTES)
        for holding in result["positions"]:
            symbol = holding["symbol"]
            rows = snapshot.get("bars", {}).get(symbol, [])
            holding.update(UNIVERSES[market][symbol], bar=rows[-1] if rows else None)
        return {**result, "today": today, "history": events[-20:][::-1]}

    def record(self, market: str, data: dict, *, commit: bool) -> dict:
        now = datetime.now(timezone.utc)
        event = prepare_event(market, data, UNIVERSES[market], now)
        with self.locks[market]:
            events = self._load(market, "ledger", _LEDGER_BYTES).get("events", [])
            existing = next((record for record in events if record["id"] == event["id"]), None)
            if existing is not None and existing != event:
                raise ValueError("该记录编号已用于另一笔交易")
            updated = events if existing else [*events, event]
            result = self._account_view(market, updated, now)
            if commit and existing is None:
                self._save(market, "ledger", {"events": updated}, _LEDGER_BYTES)
            if event["action"] != "deposit":
                snapshot = self._load(market, "daily", _SNAPSHOT_BYTES)
                rules = self._security_rules(market, event["symbol"], snapshot)
                result.update(rules)
                if market == "HK" and rules.get("board_lot_as_of") == result["today"]:
                    result["levies"] = estimated_hk_levies(event, rules)
            return {**result, "event": event, "committed": commit}

    def favorite(self, market: str, symbol: str, action: str) -> None:
        if action not in {"add", "remove"} or symbol not in UNIVERSES[market]:
            raise ValueError("自选操作或股票代码无效")
        with self.locks[market]:
            symbols = self._load(market, "favorites", _FAVORITES_BYTES).get("symbols", [])
            symbols = [item for item in symbols if item != symbol]
            if action == "add":
                symbols.append(symbol)
            self._save(market, "favorites", {"symbols": symbols}, _FAVORITES_BYTES)

    def quotes(self, market: str) -> dict:
        source = "Alpaca IEX 单交易所成交" if market == "US" else "新浪财经港股快照"
        with self.locks[market]:
            favorites = tuple(self._load(market, "favorites", _FAVORITES_BYTES).get("symbols", []))
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
                ticks = (
                    alpaca_iex_trades(list(favorites), credentials)
                    if market == "US" else sina_hk_trades(list(favorites))
                )
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
        symbols = list(UNIVERSES[market])
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
