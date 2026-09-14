"""Bounded, metadata-only CNINFO announcement adapter.

The public endpoint is not treated as an exchange real-time feed or as a
contractually stable API.  Requests are deliberately low-frequency, bounded,
and fail closed at the symbol level.  PDF bodies are never downloaded.
"""

from __future__ import annotations

import html
import json
import re
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from a_share_quant.data.normalization import normalize_symbol
from a_share_quant.intelligence.contracts import (
    EventRiskLevel,
    PublicRiskAssessment,
    PublicRiskEvent,
    PublicRiskSnapshot,
)

_COMPANY_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_PDF_BASE = "https://static.cninfo.com.cn/"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_CODE = re.compile(r"^[0-9]{6}$")
_HTML_TAG = re.compile(r"<[^>]+>")

Transport = Callable[[str, str, bytes | None, Mapping[str, str], float], bytes]

_RELIEF_PATTERNS = (
    re.compile(r"(?<!申请)撤销.*?退市风险警示"),
    re.compile(r"(?<!申请)撤销.*?其他风险警示"),
    re.compile(r"解除冻结"),
    re.compile(r"解除质押"),
    re.compile(r"复牌"),
)
_RESOLUTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("DELISTING_RISK", re.compile(r"(?<!申请)撤销.*?退市风险警示")),
    ("TRADING_SUSPENSION", re.compile(r"复牌")),
)
_BLOCK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("DELISTING_RISK", re.compile(r"退市风险警示|终止上市|退市整理|重大违法强制退市")),
    (
        "REGULATORY_INVESTIGATION",
        re.compile(r"(?:证监会|中国证券监督管理委员会).{0,12}立案|立案调查|立案告知"),
    ),
    ("TRADING_SUSPENSION", re.compile(r"停牌")),
    ("BANKRUPTCY_RISK", re.compile(r"破产清算|被申请破产|预重整|破产重整")),
)
_REVIEW_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OTHER_RISK_WARNING", re.compile(r"(?:被|继续|实施).*其他风险警示")),
    ("REGULATORY_ACTION", re.compile(r"行政处罚|监管措施|警示函|监管工作函|纪律处分")),
    ("LITIGATION_RISK", re.compile(r"重大诉讼|重大仲裁|诉讼进展|仲裁进展")),
    ("OWNERSHIP_RISK", re.compile(r"司法冻结|轮候冻结|减持计划|被动减持|控制权变更")),
    ("DEBT_RISK", re.compile(r"债务逾期|债券违约|未能清偿|违规担保|担保逾期")),
    ("IMPAIRMENT_RISK", re.compile(r"计提.*减值|资产减值准备")),
    ("EARNINGS_RISK", re.compile(r"业绩预亏|业绩预减|预计亏损|由盈转亏")),
    ("MARKET_ABNORMALITY", re.compile(r"股票交易异常波动|严重异常波动|风险提示公告")),
    ("AUDIT_RISK", re.compile(r"无法表示意见|否定意见|保留意见.*审计|内部控制.*否定意见")),
)


def classify_announcement_title(title: str) -> tuple[EventRiskLevel, tuple[str, ...]]:
    """Classify title-only evidence conservatively and without sentiment guessing."""

    cleaned = _clean_title(title)
    risk_text = cleaned
    for pattern in _RELIEF_PATTERNS:
        risk_text = pattern.sub("", risk_text)
    blocked = tuple(code for code, pattern in _BLOCK_PATTERNS if pattern.search(risk_text))
    if blocked:
        return EventRiskLevel.BLOCKED, blocked
    review = tuple(code for code, pattern in _REVIEW_PATTERNS if pattern.search(risk_text))
    if review:
        return EventRiskLevel.REVIEW, review
    return EventRiskLevel.CLEAR, ()


class CNInfoAnnouncementProvider:
    name = "CNINFO"

    def __init__(
        self,
        *,
        timeout_seconds: float = 12.0,
        delay_seconds: float = 0.10,
        max_pages_per_symbol: int = 2,
        transport: Transport | None = None,
    ) -> None:
        if timeout_seconds <= 0 or delay_seconds < 0:
            raise ValueError("CNINFO timing values are invalid")
        if not 1 <= max_pages_per_symbol <= 5:
            raise ValueError("max_pages_per_symbol must be between 1 and 5")
        self.timeout_seconds = float(timeout_seconds)
        self.delay_seconds = float(delay_seconds)
        self.max_pages_per_symbol = max_pages_per_symbol
        self._transport = transport or _url_transport

    def fetch(
        self,
        symbols: Iterable[str],
        *,
        window_end: date,
        window_days: int = 30,
        now: datetime | None = None,
    ) -> PublicRiskSnapshot:
        if window_days < 1 or window_days > 90:
            raise ValueError("window_days must be between 1 and 90")
        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        checked_at = checked_at.astimezone(timezone.utc)
        normalized = tuple(dict.fromkeys(normalize_symbol(symbol) for symbol in symbols))
        if len(normalized) > 50:
            raise ValueError("CNINFO risk refresh is limited to 50 symbols")
        window_start = window_end - timedelta(days=window_days - 1)
        companies = self._company_map()
        assessments: list[PublicRiskAssessment] = []
        failures = 0
        for index, symbol in enumerate(normalized):
            company = companies.get(symbol)
            if company is None:
                failures += 1
                assessments.append(
                    _unknown_assessment(symbol, symbol, checked_at, "CNINFO_SYMBOL_NOT_FOUND")
                )
                continue
            try:
                events = self._announcements(
                    symbol,
                    org_id=company[0],
                    window_start=window_start,
                    window_end=window_end,
                )
                assessments.append(
                    _assessment(symbol, company[1], events=events, checked_at=checked_at)
                )
            except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
                failures += 1
                assessments.append(
                    _unknown_assessment(symbol, company[1], checked_at, "CNINFO_REQUEST_FAILED")
                )
            if self.delay_seconds and index + 1 < len(normalized):
                time.sleep(self.delay_seconds)
        status = "PARTIAL" if failures else "FRESH"
        notice = (
            f"巨潮公告风险检查完成：{len(normalized) - failures}/{len(normalized)} 只成功，"
            f"窗口 {window_start.isoformat()} 至 {window_end.isoformat()}。"
        )
        return PublicRiskSnapshot(
            source=self.name,
            fetched_at=checked_at,
            window_start=window_start,
            window_end=window_end,
            status=status,
            notice_zh=notice,
            assessments=tuple(assessments),
        )

    def _company_map(self) -> dict[str, tuple[str, str]]:
        raw = self._transport(
            "GET",
            _COMPANY_URL,
            None,
            _headers(referer="https://www.cninfo.com.cn/"),
            self.timeout_seconds,
        )
        payload = _json_object(raw)
        rows = payload.get("stockList")
        if not isinstance(rows, list) or not rows or len(rows) > 20_000:
            raise ValueError("CNINFO company map is invalid")
        result: dict[str, tuple[str, str]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code", "")).strip()
            org_id = str(row.get("orgId", "")).strip()
            name = str(row.get("zwjc", "")).strip()
            if _CODE.fullmatch(code) and org_id and name:
                result[code] = (org_id, name)
        if not result:
            raise ValueError("CNINFO company map has no valid A-share rows")
        return result

    def _announcements(
        self,
        symbol: str,
        *,
        org_id: str,
        window_start: date,
        window_end: date,
    ) -> tuple[PublicRiskEvent, ...]:
        result: dict[tuple[str, str, str], PublicRiskEvent] = {}
        for page in range(1, self.max_pages_per_symbol + 1):
            body = urlencode(
                {
                    "pageNum": str(page),
                    "pageSize": "30",
                    "column": "szse",
                    "tabName": "fulltext",
                    "plate": "",
                    "stock": f"{symbol},{org_id}",
                    "searchkey": "",
                    "secid": "",
                    "category": "",
                    "trade": "",
                    "seDate": f"{window_start.isoformat()}~{window_end.isoformat()}",
                    "sortName": "time",
                    "sortType": "desc",
                    "isHLtitle": "true",
                }
            ).encode("utf-8")
            raw = self._transport(
                "POST",
                _QUERY_URL,
                body,
                _headers(referer="https://www.cninfo.com.cn/new/fulltextSearch"),
                self.timeout_seconds,
            )
            payload = _json_object(raw)
            rows = payload.get("announcements") or []
            if not isinstance(rows, list) or len(rows) > 30:
                raise ValueError("CNINFO announcement page is invalid")
            for row in rows:
                event = _event_from_row(row, expected_symbol=symbol)
                key = (event.symbol, event.announced_at.isoformat(), event.title)
                result[key] = event
            if not payload.get("hasMore") or not rows:
                break
            if self.delay_seconds:
                time.sleep(self.delay_seconds)
        return tuple(sorted(result.values(), key=lambda item: item.announced_at, reverse=True))


def _headers(*, referer: str) -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": referer,
        "User-Agent": "A-Share-Quant-Workbench/0.2 (+local metadata-only risk check)",
        "X-Requested-With": "XMLHttpRequest",
    }


def _url_transport(
    method: str,
    url: str,
    data: bytes | None,
    headers: Mapping[str, str],
    timeout: float,
) -> bytes:
    request = Request(url, data=data, headers=dict(headers), method=method)
    with urlopen(request, timeout=timeout) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > _MAX_RESPONSE_BYTES:
            raise ValueError("CNINFO response is too large")
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("CNINFO response is too large")
    return raw


def _json_object(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("CNINFO response size is invalid")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("CNINFO response must be a JSON object")
    return payload


def _event_from_row(row: Any, *, expected_symbol: str) -> PublicRiskEvent:
    if not isinstance(row, dict):
        raise ValueError("CNINFO announcement row is invalid")
    symbol = str(row.get("secCode", "")).strip()
    if symbol != expected_symbol or not _CODE.fullmatch(symbol):
        raise ValueError("CNINFO announcement symbol mismatch")
    title = _clean_title(str(row.get("announcementTitle", "")))
    event_id = str(row.get("announcementId", "")).strip()
    name = str(row.get("secName", "")).strip()
    raw_time = row.get("announcementTime")
    if not isinstance(raw_time, (int, float)) or raw_time <= 0:
        raise ValueError("CNINFO announcement time is invalid")
    adjunct = str(row.get("adjunctUrl", "")).strip().lstrip("/")
    if not adjunct or ".." in adjunct or not adjunct.casefold().endswith(".pdf"):
        raise ValueError("CNINFO announcement URL is invalid")
    level, reasons = classify_announcement_title(title)
    return PublicRiskEvent(
        event_id=event_id,
        symbol=symbol,
        name=name,
        title=title,
        announced_at=datetime.fromtimestamp(float(raw_time) / 1000, tz=timezone.utc),
        source="CNINFO",
        source_url=_PDF_BASE + adjunct,
        level=level,
        reason_codes=reasons,
    )


def _assessment(
    symbol: str,
    name: str,
    *,
    events: tuple[PublicRiskEvent, ...],
    checked_at: datetime,
) -> PublicRiskAssessment:
    resolution_times: dict[str, datetime] = {}
    for event in events:
        for reason, pattern in _RESOLUTION_PATTERNS:
            if pattern.search(event.title):
                previous = resolution_times.get(reason)
                if previous is None or event.announced_at > previous:
                    resolution_times[reason] = event.announced_at

    active: list[tuple[PublicRiskEvent, tuple[str, ...]]] = []
    for event in events:
        unresolved = tuple(
            reason
            for reason in event.reason_codes
            if resolution_times.get(reason, event.announced_at) <= event.announced_at
        )
        if unresolved:
            active.append((event, unresolved))

    if any(event.level is EventRiskLevel.BLOCKED for event, _ in active):
        level = EventRiskLevel.BLOCKED
    elif active:
        level = EventRiskLevel.REVIEW
    else:
        level = EventRiskLevel.CLEAR
    reasons = tuple(code for _, codes in active for code in codes)
    ordered = tuple(
        sorted(
            events,
            key=lambda item: (
                item.level is EventRiskLevel.CLEAR,
                -item.announced_at.timestamp(),
            ),
        )
    )
    return PublicRiskAssessment(
        symbol=symbol,
        name=name,
        level=level,
        reason_codes=reasons,
        events=ordered[:8],
        checked_at=checked_at,
    )


def _unknown_assessment(
    symbol: str,
    name: str,
    checked_at: datetime,
    reason: str,
) -> PublicRiskAssessment:
    return PublicRiskAssessment(
        symbol=symbol,
        name=name,
        level=EventRiskLevel.UNKNOWN,
        reason_codes=(reason,),
        events=(),
        checked_at=checked_at,
    )


def _clean_title(value: str) -> str:
    cleaned = html.unescape(_HTML_TAG.sub("", str(value))).replace("\u3000", " ")
    return " ".join(cleaned.split()).strip()
