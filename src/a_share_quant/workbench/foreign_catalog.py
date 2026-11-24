"""Validated public US and Hong Kong equity directories."""

from __future__ import annotations

import csv
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from uuid import uuid4

import pandas as pd
from opencc import OpenCC

from a_share_quant.data.providers.foreign_daily import HKEX_SECURITIES, ForeignDataError, _get

_HK_ZH = "https://www.hkex.com.hk/chi/services/trading/securities/securitieslists/ListOfSecurities_c.xlsx"
_US_BASE = "https://www.nasdaqtrader.com/dynamic/SymDir/"
_US_SYMBOL = re.compile(r"[A-Z][A-Z0-9]{0,6}(?:\.[A-Z0-9]{1,3})?")
_EXCHANGES = {"A": "XASE", "F": "TXSE", "M": "XCHI", "N": "XNYS", "P": "ARCX",
              "V": "IEXG", "Z": "BATS"}
_EXCLUDED_US = re.compile(
    r"\b(?:preferred(?: stock| shares)?|pfd|warrants?|rights?|"
    r"notes? due|debentures?|exchange.traded (?:fund|note)|beneficial interest)\b"
    r"|\b(?:units?|fund|trust)$", re.IGNORECASE,
)
_HK_CATEGORIES = {
    "Equity Securities (Main Board)", "Equity Securities (GEM)", "Depositary Receipts",
    "Trading Only Securities", "Investment Companies",
}
_US_ALIASES = {
    "AAPL": "苹果", "MSFT": "微软", "NVDA": "英伟达", "TSLA": "特斯拉", "PLTR": "帕兰提尔",
    "UBER": "优步", "BRK.A": "伯克希尔哈撒韦", "BRK.B": "伯克希尔哈撒韦", "AMZN": "亚马逊",
    "BABA": "阿里巴巴", "TSM": "台积电", "BIDU": "百度", "META": "脸书", "GOOGL": "谷歌",
}


def legacy_listing_id(market: str, symbol: str) -> str:
    return f"{market}-{symbol}"


@lru_cache(maxsize=2048)
def search_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def normalize_code(market: str, value: str) -> str:
    code = search_key(value).upper().replace("-", ".")
    return code.zfill(5) if market == "HK" and re.fullmatch(r"[0-9]{1,5}", code) else code


def rank_matches(market: str, rows: dict, query: str) -> list[dict]:
    key = search_key(query)
    if not key:
        return []
    code = normalize_code(market, query)
    prefix = key.upper().replace("-", ".")
    ranked = []
    for symbol, row in rows.items():
        if code == symbol:
            rank = 0
        elif key in row["keys"]:
            rank = 1
        elif symbol.startswith(prefix):
            rank = 2
        elif any(key in alias for alias in row["keys"]):
            rank = 3
        else:
            continue
        ranked.append((rank, symbol, row))
    ranked.sort()
    return [row for _, _, row in ranked]


def _us_file(filename: str, symbol_column: str) -> tuple[str, dict]:
    lines = _get(_US_BASE + filename).text.splitlines()
    footer = (
        re.fullmatch(r"File Creation Time:\s*(\d{10}:\d{2})\|+", lines[-1]) if lines else None
    )
    if footer is None:
        raise ForeignDataError("Nasdaq Trader 名录缺少文件日期")
    as_of = datetime.strptime(footer[1], "%m%d%Y%H:%M").date().isoformat()
    reader = csv.DictReader(lines[:-1], delimiter="|")
    required = {symbol_column, "Security Name", "Test Issue", "ETF",
                "Market Category" if symbol_column == "Symbol" else "Exchange"}
    if not required.issubset(reader.fieldnames or ()):
        raise ForeignDataError("Nasdaq Trader 名录列发生变化")
    rows = {}
    for row in reader:
        symbol, name = row[symbol_column], row["Security Name"]
        if (
            row["Test Issue"] != "N" or row["ETF"] != "N" or row.get("NextShares") == "Y"
            or not symbol or not name or not _US_SYMBOL.fullmatch(symbol)
            or _EXCLUDED_US.search(name)
        ):
            continue
        exchange = "XNAS" if symbol_column == "Symbol" else _EXCHANGES.get(row["Exchange"])
        if not exchange:
            continue
        if symbol in rows:
            raise ForeignDataError("Nasdaq Trader 名录包含重复股票代码")
        rows[symbol] = {
            "symbol": symbol, "name": name, "exchange": exchange, "currency": "USD",
            "identity": search_key(name),
            "keys": [search_key(name), search_key(_US_ALIASES.get(symbol, ""))],
        }
    if len(rows) < 1000:
        raise ForeignDataError("Nasdaq Trader 名录股票数量异常")
    return as_of, rows


def _hk_file(url: str, columns: list[str]) -> tuple[str, pd.DataFrame]:
    with pd.ExcelFile(BytesIO(_get(url).content), engine="openpyxl") as file:
        header = str(file.parse(header=None, nrows=2).iloc[1, 0])
        match = re.search(r"\d{2}/\d{2}/\d{4}", header)
        if not match:
            raise ForeignDataError("港交所证券名录缺少生效日期")
        as_of = datetime.strptime(match[0], "%d/%m/%Y").date().isoformat()
        return as_of, file.parse(header=2, dtype=str, usecols=columns).fillna("")[columns]


def _hk_rows() -> tuple[str, dict]:
    with ThreadPoolExecutor(max_workers=2) as executor:
        english = executor.submit(_hk_file, HKEX_SECURITIES, [
            "Stock Code", "Name of Securities", "Category", "Sub-Category", "Board Lot", "ISIN",
            "Subject to Stamp Duty", "Trading Currency",
        ])
        chinese = executor.submit(_hk_file, _HK_ZH, ["股份代號", "股份名稱"])
        as_of, frame = english.result()
        zh_as_of, names = chinese.result()
    if as_of != zh_as_of:
        raise ForeignDataError("港交所中英文名录日期暂未同步")
    if names.iloc[:, 0].duplicated().any():
        raise ForeignDataError("港交所中文名录包含重复代码")
    zh_names = dict(names.itertuples(index=False, name=None))
    converter = OpenCC("t2s")
    rows = {}
    for code, name, category, sub_category, lot, isin, levy, currency in frame.itertuples(
        index=False, name=None
    ):
        if category != "Equity" or sub_category not in _HK_CATEGORIES:
            continue
        if not re.fullmatch(r"[0-9]{5}", code) or not name or not isin or not currency.strip():
            raise ForeignDataError("港交所股票名录关键字段缺失")
        if code in rows:
            raise ForeignDataError("港交所名录包含重复股票代码")
        currency = {"RMB": "CNY", "CNH": "CNY"}.get(currency.strip(), currency.strip())
        traditional = zh_names.get(code, "")
        simplified = converter.convert(traditional) if traditional else ""
        row = {
            "symbol": code, "name": traditional or name, "exchange": "XHKG",
            "currency": currency, "identity": f"{isin}:{currency}:{code}",
            "keys": [search_key(alias) for alias in (name, traditional, simplified) if alias],
            "board_lot_as_of": as_of,
        }
        if lot.replace(",", "").isdigit():
            row["board_lot"] = int(lot.replace(",", ""))
        if levy in {"Y", "N"}:
            row["stamp_duty"] = levy == "Y"
        rows[code] = row
    if len(rows) < 1000:
        raise ForeignDataError("港交所名录股票数量异常")
    return as_of, rows


def fetch_catalog(market: str) -> tuple[str, dict]:
    if market == "HK":
        return _hk_rows()
    with ThreadPoolExecutor(max_workers=2) as executor:
        nasdaq = executor.submit(_us_file, "nasdaqlisted.txt", "Symbol")
        other = executor.submit(_us_file, "otherlisted.txt", "ACT Symbol")
        nasdaq_date, nasdaq_rows = nasdaq.result()
        other_date, other_rows = other.result()
    if nasdaq_rows.keys() & other_rows.keys():
        raise ForeignDataError("美国两份名录含有冲突代码")
    return min(nasdaq_date, other_date), {**nasdaq_rows, **other_rows}


def update_listings(
    market: str, previous: dict, rows: dict, seeds: dict, as_of: str
) -> tuple[dict, dict]:
    """Keep identity when the source can confirm it, archive superseded listings."""
    old = previous.get("rows", {})
    archived = dict(previous.get("archived", {}))
    former_codes = {row["symbol"] for row in archived.values()}
    current = {}
    for symbol, row in rows.items():
        prior = old.get(symbol)
        if prior and prior["identity"] == row["identity"]:
            listing_id = prior["listing_id"]
            if prior.get("history_from"):
                row = {**row, "history_from": prior["history_from"]}
            row = {**row, "keys": list(dict.fromkeys([*row["keys"], *prior["keys"]]))}
        elif not previous and symbol in seeds:
            listing_id = legacy_listing_id(market, symbol)
        else:
            listing_id = uuid4().hex
            if prior or symbol in former_codes:
                row = {**row, "history_from": as_of}
        if symbol in seeds and listing_id == legacy_listing_id(market, symbol):
            keys = [*row["keys"], search_key(seeds[symbol]["name"])]
            row = {**row, "keys": list(dict.fromkeys(keys))}
        current[symbol] = {**row, "listing_id": listing_id}
    for symbol, row in old.items():
        if symbol not in current or row["listing_id"] != current[symbol]["listing_id"]:
            archived[row["listing_id"]] = row
    return current, archived


def public_security(row: dict) -> dict:
    internal = {"keys", "identity", "history_from"}
    return {key: value for key, value in row.items() if key not in internal}
