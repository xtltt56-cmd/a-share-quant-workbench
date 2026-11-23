"""Bounded Tencent quotes for an explicitly selected A-share batch."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from a_share_quant.contracts.realtime import RealTimeQuote

_CODES = {"SSE": "sh", "SZSE": "sz", "BSE": "bj"}
_PAYLOAD = re.compile(r'v_((?:sh|sz|bj)[0-9]{6})="([^"\r\n]*)"')
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MAX_RESPONSE = 16_384


def get_tencent_quotes(batch: tuple[tuple[str, str], ...]) -> tuple[RealTimeQuote, ...]:
    requested = {
        _CODES[exchange] + symbol: (exchange, symbol)
        for exchange, symbol in batch
        if exchange in _CODES and re.fullmatch(r"[0-9]{6}", symbol)
    }
    if not requested:
        return ()
    # Only a small request batch built from verified market-and-code pairs reaches this URL.
    with urlopen(f"https://qt.gtimg.cn/q={','.join(requested)}", timeout=5) as response:
        raw = response.read(_MAX_RESPONSE + 1)
    if len(raw) > _MAX_RESPONSE:
        raise ValueError("Tencent quote response exceeded size limit")
    received = datetime.now(timezone.utc)
    return _parse_quotes(raw.decode("gb18030"), requested, received)


def _parse_quotes(
    payload: str, requested: dict[str, tuple[str, str]], received: datetime,
) -> tuple[RealTimeQuote, ...]:
    quotes = {}
    for ticker, raw_fields in _PAYLOAD.findall(payload):
        if ticker not in requested:
            continue
        exchange, code = requested[ticker]
        fields = raw_fields.split("~")
        # The listed code and quote timestamp have fixed positions in the provider response.
        if len(fields) <= 30 or fields[2] != code or not re.fullmatch(r"[0-9]{14}", fields[30]):
            continue
        try:
            stamp = datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(tzinfo=_SHANGHAI)
            quote = RealTimeQuote(
                symbol=code,
                market=exchange,
                timestamp_exchange=stamp,
                timestamp_received=received,
                last=float(fields[3]),
                name=fields[1],
                source="Tencent",
            )
        except ValueError:
            continue
        if stamp <= received:
            quotes[(exchange, code)] = quote
    return tuple(quotes.values())
