"""Free-source daily bars for overseas research."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from bisect import bisect_right
from datetime import date, datetime, time, timedelta
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import dotenv_values

SINA_SOURCE = "新浪财经港股日线"
SINA_US_SOURCE = "新浪财经美股日线"
ALPACA_SOURCE = "Alpaca SIP 历史日线"
HKEX_SECURITIES = (
    "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"
)
_US_SINA_FACTOR = "https://finance.sina.com.cn/us_stock/company/reinstatement/{}_qfq.js"
HTTP_TIMEOUT = (4, 8)


class ForeignDataError(RuntimeError):
    """A provider failure with a safe message for the local UI."""


def alpaca_credentials(root: Path) -> dict[str, str]:
    names = ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")
    values = dotenv_values(root / ".env", interpolate=False) if not all(
        os.getenv(name) for name in names
    ) else {}
    key, secret = ((os.getenv(name) or values.get(name) or "").strip() for name in names)
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret} if key and secret else {}


def _get(url: str, **kwargs) -> requests.Response:
    try:
        response = requests.get(url, timeout=HTTP_TIMEOUT, allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise ForeignDataError("行情连接失败或超时") from None
    if response.status_code in (401, 403):
        raise ForeignDataError("行情认证或数据权限未通过")
    if response.status_code == 429:
        raise ForeignDataError("行情源限流，请稍后重试")
    if response.status_code != 200:
        raise ForeignDataError(f"行情源返回 HTTP {response.status_code}")
    return response


def sina_daily(symbol: str, start: date, end: date, *, market: str = "HK") -> list[dict]:
    if market == "HK":
        base = f"https://finance.sina.com.cn/stock/hkstock/{symbol}"
        encoded = _get(base + "/klc2_kl.js").text
        factor_url = base + "/qfq.js"
    else:
        encoded = _get(f"https://finance.sina.com.cn/staticdata/us/{symbol}").text
        factor_url = _US_SINA_FACTOR.format(symbol)
    match = re.match(r'\s*var\s+[\w$]+\s*=\s*"([A-Za-z0-9+/=]+)"\s*;', encoded)
    if match is None:
        raise ForeignDataError("新浪日线返回格式异常")
    factor_text = _get(factor_url).text.partition("=")[2].strip().rstrip(";")
    factors = json.JSONDecoder().raw_decode(factor_text)[0]["data"]
    changes = sorted(
        (date.fromisoformat(x["d"]), float(x["f"]), float(x.get("c", 0))) for x in factors
    )
    if not changes:
        raise ForeignDataError("新浪日线缺少复权因子")
    dates = [row[0] for row in changes]
    try:
        # Native V8 failures on Windows must not terminate the HTTP server.
        response = subprocess.run(
            [sys.executable, "-m", "a_share_quant.data.providers.sina_decode"],
            input=json.dumps([market, match[1]]), capture_output=True, encoding="utf-8",
            timeout=25, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        decoded = json.loads(response.stdout)
    except (subprocess.SubprocessError, OSError, ValueError):
        raise ForeignDataError("新浪日线解码失败或超时，请稍后重试") from None
    bars = []
    for row in decoded:
        day = date.fromisoformat(row["date"][:10])
        if start <= day <= end:
            factor_index = bisect_right(dates, day) - 1
            if factor_index < 0:
                raise ForeignDataError("新浪日线复权因子覆盖不足")
            # The compressed feed occasionally emits one extra floating-point digit.
            # Normalize at the source boundary before checking the OHLC bounds.
            prices = {key: round(float(row[key]), 4) for key in ("open", "high", "low", "close")}
            _, factor, adjustment = changes[factor_index]
            bars.append({
                **prices,
                "volume": row["volume"],
                "date": day.isoformat(),
                "adjusted_close": prices["close"] * factor + adjustment,
            })
    return bars


def alpaca_daily(
    symbols: list[str], start: date, end: date, credentials: dict[str, str]
) -> dict[str, list[dict]]:
    zone = ZoneInfo("America/New_York")
    params = {
        "symbols": ",".join(symbols), "timeframe": "1Day", "feed": "sip",
        "start": datetime.combine(start, time.min, zone).isoformat(),
        "end": (datetime.combine(end + timedelta(days=1), time.min, zone)
                - timedelta(seconds=1)).isoformat(),
        "limit": 10000, "currency": "USD",
    }
    raw = _alpaca_bars(credentials, params, "raw")
    adjusted = _alpaca_bars(credentials, params, "all")
    result = {}
    for symbol in symbols:
        adj_close = {bar["t"]: bar["c"] for bar in adjusted.get(symbol, [])}
        rows = []
        for bar in raw.get(symbol, []):
            day = datetime.fromisoformat(bar["t"].replace("Z", "+00:00")).astimezone(zone).date()
            if start <= day <= end:
                rows.append({
                    "date": day.isoformat(), "open": bar["o"], "high": bar["h"],
                    "low": bar["l"], "close": bar["c"], "volume": bar["v"],
                    "adjusted_close": adj_close.get(bar["t"]),
                })
        result[symbol] = rows
    return result


def _alpaca_bars(credentials: dict[str, str], params: dict, adjustment: str) -> dict:
    rows: dict[str, list] = {}
    query = {**params, "adjustment": adjustment}
    for _ in range(12):
        payload = _get(
            "https://data.alpaca.markets/v2/stocks/bars",
            params=query, headers=credentials,
        ).json()
        for symbol, bars in (payload["bars"] or {}).items():
            rows.setdefault(symbol, []).extend(bars)
        token = payload.get("next_page_token")
        if not token:
            return rows
        query["page_token"] = token
    raise ForeignDataError("Alpaca 日线分页超过单次刷新范围")


def alpaca_iex_trades(symbols: list[str], credentials: dict[str, str]) -> dict:
    data = _get(
        "https://data.alpaca.markets/v2/stocks/trades/latest",
        params={"symbols": ",".join(symbols), "feed": "iex", "currency": "USD"},
        headers=credentials,
    ).json()
    return {symbol: (row["p"], row["t"]) for symbol, row in data["trades"].items()}


def sina_hk_trades(symbols: list[str]) -> dict:
    response = _get(
        "https://hq.sinajs.cn/list=" + ",".join("rt_hk" + symbol for symbol in symbols),
        headers={"Referer": "https://finance.sina.com.cn/"},
    )
    result = {}
    for match in re.finditer(r'var hq_str_rt_hk(\d{5})="([^"\r\n]+)";', response.text):
        columns = match[2].split(",")
        if len(columns) >= 19:
            try:
                when = datetime.strptime(
                    columns[17] + " " + columns[18], "%Y/%m/%d %H:%M:%S"
                ).replace(tzinfo=ZoneInfo("Asia/Hong_Kong"))
                result[match[1]] = (float(columns[6]), when.isoformat())
            except ValueError:
                continue
    if not result:
        raise ForeignDataError("新浪港股盘中快照暂无有效时间")
    return result


def hkex_board_lots(symbols: list[str]) -> dict:
    response = _get(HKEX_SECURITIES)
    with pd.ExcelFile(BytesIO(response.content), engine="openpyxl") as file:
        date_header = str(file.parse(header=None, nrows=2).iloc[1, 0])
        match = re.search(r"\d{2}/\d{2}/\d{4}", date_header)
        if match is None:
            raise ForeignDataError("港交所证券名录缺少生效日期")
        as_of = datetime.strptime(match[0], "%d/%m/%Y").date().isoformat()
        required = ["Stock Code", "Board Lot", "Category", "Subject to Stamp Duty"]
        metadata = file.parse(header=2, dtype=str, usecols=required)
    records = {}
    for code, lot, category, stamp in metadata[required].itertuples(index=False, name=None):
        if code in symbols and category == "Equity":
            amount = int(lot.replace(",", ""))
            if amount > 0 and stamp in {"Y", "N"}:
                records[code] = {"board_lot": amount, "stamp_duty": stamp == "Y"}
    if not records:
        raise ForeignDataError("港交所证券名录未找到研究样本")
    return {"as_of": as_of, "items": records}
