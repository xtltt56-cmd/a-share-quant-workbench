"""Normalize provider-specific quote and minute-bar frames."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from a_share_quant.contracts.realtime import MinuteBar, RealTimeQuote
from a_share_quant.data.normalization import normalize_symbol

_LOCAL_TZ = timezone(timedelta(hours=8))

_QUOTE_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "代码", "code", "ts_code", "order_book_id"),
    "name": ("name", "名称", "security_name"),
    "last": ("last", "最新价", "close", "收盘", "last_price"),
    "open": ("open", "今开", "开盘", "open_price"),
    "high": ("high", "最高", "最高价", "high_price"),
    "low": ("low", "最低", "最低价", "low_price"),
    "previous_close": ("previous_close", "昨收", "pre_close", "prev_close"),
    "volume": ("volume", "成交量", "vol", "volume_total"),
    "amount": ("amount", "成交额", "amount_total"),
    "bid1": ("bid1", "买一", "bid_price1"),
    "ask1": ("ask1", "卖一", "ask_price1"),
    "change": ("change", "涨跌额", "change_amount"),
    "change_pct": ("change_pct", "涨跌幅", "pct_chg", "change_percent"),
    "turnover_rate": ("turnover_rate", "换手率", "turnover"),
    "timestamp": ("timestamp", "时间", "trade_time", "datetime", "date"),
}

_BAR_ALIASES = {
    "open": ("open", "开盘", "open_price"),
    "high": ("high", "最高", "high_price"),
    "low": ("low", "最低", "low_price"),
    "close": ("close", "收盘", "last", "最新价", "close_price"),
    "volume": ("volume", "成交量", "vol"),
    "amount": ("amount", "成交额", "total_turnover"),
    "timestamp": ("timestamp", "时间", "trade_time", "datetime", "date"),
}


def _column(frame: pd.DataFrame, aliases: Iterable[str]) -> str | None:
    return next((alias for alias in aliases if alias in frame.columns), None)


def _value(row: pd.Series, aliases: Iterable[str]) -> Any:
    column = _column(row.to_frame().T, aliases)
    return row[column] if column is not None else None


def _number(
    value: Any,
    *,
    positive: bool = False,
    allow_negative: bool = False,
) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    if positive and parsed <= 0:
        return None
    if not positive and not allow_negative and parsed < 0:
        return None
    return parsed


def _timestamp(value: Any, *, received_at: datetime) -> tuple[datetime, bool]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return received_at, False
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return received_at, False
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize(_LOCAL_TZ)
    return parsed.to_pydatetime().astimezone(timezone.utc), True


def normalize_realtime_quotes(
    raw: pd.DataFrame,
    *,
    source: str,
    received_at: datetime | None = None,
    market: str = "A",
) -> tuple[RealTimeQuote, ...]:
    if not isinstance(raw, pd.DataFrame):
        raise ValueError("real-time quote response must be a DataFrame")
    received = received_at or datetime.now(timezone.utc)
    if received.tzinfo is None or received.utcoffset() is None:
        raise ValueError("received_at must be timezone-aware")
    quotes: list[RealTimeQuote] = []
    for _, row in raw.iterrows():
        raw_symbol = _value(row, _QUOTE_ALIASES["symbol"])
        try:
            symbol = normalize_symbol(raw_symbol)
        except (TypeError, ValueError):
            continue
        if any(
            _invalid_number(_value(row, _QUOTE_ALIASES[field]), positive=False)
            for field in ("volume", "amount")
        ) or any(
            _invalid_number(_value(row, _QUOTE_ALIASES[field]), positive=True)
            for field in ("last", "open", "high", "low", "previous_close")
        ):
            continue
        last = _number(_value(row, _QUOTE_ALIASES["last"]), positive=True)
        if last is None:
            continue
        exchange_time, has_exchange_time = _timestamp(
            _value(row, _QUOTE_ALIASES["timestamp"]), received_at=received
        )
        optional_positive = {
            field: _number(_value(row, _QUOTE_ALIASES[field]), positive=True)
            for field in ("open", "high", "low", "previous_close")
        }
        quality = "GOOD" if has_exchange_time else "DEGRADED"
        quotes.append(
            RealTimeQuote(
                symbol=symbol,
                name=_text(_value(row, _QUOTE_ALIASES["name"])),
                market=market,
                timestamp_exchange=exchange_time,
                timestamp_received=received,
                last=last,
                open=optional_positive["open"],
                high=optional_positive["high"],
                low=optional_positive["low"],
                previous_close=optional_positive["previous_close"],
                volume=_number(_value(row, _QUOTE_ALIASES["volume"])),
                amount=_number(_value(row, _QUOTE_ALIASES["amount"])),
                bid1=_number(_value(row, _QUOTE_ALIASES["bid1"]), positive=True),
                ask1=_number(_value(row, _QUOTE_ALIASES["ask1"]), positive=True),
                change=_number(
                    _value(row, _QUOTE_ALIASES["change"]), allow_negative=True
                ),
                change_pct=_number(
                    _value(row, _QUOTE_ALIASES["change_pct"]), allow_negative=True
                ),
                turnover_rate=_number(_value(row, _QUOTE_ALIASES["turnover_rate"])),
                source=source,
                quality_flag=quality,
                is_stale=False,
            )
        )
    return tuple(quotes)


def normalize_minute_bars(
    raw: pd.DataFrame,
    *,
    symbol: str,
    frequency: str,
    source: str,
    received_at: datetime | None = None,
) -> tuple[MinuteBar, ...]:
    if not isinstance(raw, pd.DataFrame):
        raise ValueError("minute-bar response must be a DataFrame")
    received = received_at or datetime.now(timezone.utc)
    normalized_symbol = normalize_symbol(symbol)
    bars: list[MinuteBar] = []
    for _, row in raw.iterrows():
        timestamp, _ = _timestamp(_value(row, _BAR_ALIASES["timestamp"]), received_at=received)
        values = {
            field: _number(_value(row, _BAR_ALIASES[field]), positive=True)
            for field in ("open", "high", "low", "close")
        }
        volume = _number(_value(row, _BAR_ALIASES["volume"]))
        amount = _number(_value(row, _BAR_ALIASES["amount"]))
        if any(value is None for value in values.values()) or volume is None or amount is None:
            continue
        current_bucket = received.replace(second=0, microsecond=0)
        is_final = timestamp < current_bucket
        bars.append(
            MinuteBar(
                symbol=normalized_symbol,
                timestamp=timestamp,
                frequency=frequency,
                open=values["open"],
                high=values["high"],
                low=values["low"],
                close=values["close"],
                volume=volume,
                amount=amount,
                source=source,
                is_final=is_final,
                quality_flag="GOOD" if timestamp != received else "DEGRADED",
            )
        )
    return tuple(bars)


def _text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _invalid_number(value: Any, *, positive: bool) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return True
    if pd.isna(parsed):
        return True
    return parsed <= 0 if positive else parsed < 0
