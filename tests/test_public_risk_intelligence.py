import json
import threading
from datetime import date, datetime, timezone
from urllib.parse import parse_qs

import pytest

from a_share_quant.advisory.price_contracts import (
    GuidanceLevel,
    GuidanceState,
    PriceGuidancePlan,
    PricePlanType,
)
from a_share_quant.data.public_intelligence.cninfo import (
    CNInfoAnnouncementProvider,
    _assessment,
    classify_announcement_title,
)
from a_share_quant.intelligence.contracts import (
    EventRiskLevel,
    PublicRiskAssessment,
    PublicRiskEvent,
    PublicRiskSnapshot,
)
from a_share_quant.runtime.public_risk import PublicRiskCoordinator
from a_share_quant.signals.realtime import OfficialModelSignal
from a_share_quant.storage.official_signal_store import OfficialSignalStore
from a_share_quant.storage.price_guidance_store import PriceGuidanceStore
from a_share_quant.storage.public_risk_store import PublicRiskStore
from a_share_quant.workbench.service import WorkbenchService, _official_signal_payload

UTC = timezone.utc


def _event(
    *,
    event_id: str = "1",
    title: str = "关于收到中国证监会立案告知书的公告",
    announced_at: datetime = datetime(2026, 9, 10, 8, tzinfo=UTC),
    level: EventRiskLevel = EventRiskLevel.BLOCKED,
    reasons: tuple[str, ...] = ("REGULATORY_INVESTIGATION",),
) -> PublicRiskEvent:
    return PublicRiskEvent(
        event_id=event_id,
        symbol="600519",
        name="贵州茅台",
        title=title,
        announced_at=announced_at,
        source="CNINFO",
        source_url=f"https://static.cninfo.com.cn/finalpage/{event_id}.pdf",
        level=level,
        reason_codes=reasons,
    )


def _snapshot(*, fetched_at: datetime | None = None) -> PublicRiskSnapshot:
    checked_at = fetched_at or datetime(2026, 9, 14, 2, tzinfo=UTC)
    return PublicRiskSnapshot(
        source="CNINFO",
        fetched_at=checked_at,
        window_start=date(2026, 8, 16),
        window_end=date(2026, 9, 14),
        status="FRESH",
        notice_zh="巨潮公告风险检查完成。",
        assessments=(
            PublicRiskAssessment(
                symbol="600519",
                name="贵州茅台",
                level=EventRiskLevel.BLOCKED,
                reason_codes=("REGULATORY_INVESTIGATION",),
                events=(_event(),),
                checked_at=checked_at,
            ),
        ),
    )


def test_cninfo_fetch_uses_company_org_id_and_classifies_title() -> None:
    calls: list[tuple[str, str, bytes | None]] = []

    def transport(method, url, data, headers, timeout):
        del headers, timeout
        calls.append((method, url, data))
        if method == "GET":
            return json.dumps(
                {"stockList": [{"code": "600519", "orgId": "gssh0600519", "zwjc": "贵州茅台"}]},
                ensure_ascii=False,
            ).encode()
        return json.dumps(
            {
                "announcements": [
                    {
                        "announcementId": "121234",
                        "secCode": "600519",
                        "secName": "贵州茅台",
                        "announcementTitle": "关于收到中国证监会立案告知书的公告",
                        "announcementTime": 1_788_944_400_000,
                        "adjunctUrl": "finalpage/2026-09-10/121234.PDF",
                    }
                ],
                "hasMore": False,
            },
            ensure_ascii=False,
        ).encode()

    provider = CNInfoAnnouncementProvider(transport=transport, delay_seconds=0)
    now = datetime(2026, 9, 14, 2, tzinfo=UTC)
    snapshot = provider.fetch(["600519.SH"], window_end=date(2026, 9, 14), now=now)

    assert snapshot.status == "FRESH"
    assert snapshot.assessments[0].level is EventRiskLevel.BLOCKED
    assert snapshot.assessments[0].reason_codes == ("REGULATORY_INVESTIGATION",)
    assert snapshot.assessments[0].events[0].source_url.startswith(
        "https://static.cninfo.com.cn/"
    )
    query = parse_qs(calls[1][2].decode())
    assert query["stock"] == ["600519,gssh0600519"]
    assert query["seDate"] == ["2026-08-16~2026-09-14"]


def test_cninfo_symbol_failure_is_partial_and_fails_closed() -> None:
    def transport(method, url, data, headers, timeout):
        del url, data, headers, timeout
        if method == "GET":
            return json.dumps(
                {"stockList": [{"code": "600519", "orgId": "org", "zwjc": "贵州茅台"}]},
                ensure_ascii=False,
            ).encode()
        raise OSError("network unavailable")

    snapshot = CNInfoAnnouncementProvider(transport=transport, delay_seconds=0).fetch(
        ["600519", "000001"],
        window_end=date(2026, 9, 14),
        now=datetime(2026, 9, 14, 2, tzinfo=UTC),
    )

    assert snapshot.status == "PARTIAL"
    assert {item.level for item in snapshot.assessments} == {EventRiskLevel.UNKNOWN}
    assert all(item.reason_codes for item in snapshot.assessments)


def test_later_relief_announcement_resolves_matching_old_risk() -> None:
    suspended = _event(
        event_id="suspend",
        title="关于公司股票停牌的公告",
        announced_at=datetime(2026, 9, 9, 8, tzinfo=UTC),
        reasons=("TRADING_SUSPENSION",),
    )
    resumed = _event(
        event_id="resume",
        title="关于公司股票复牌的公告",
        announced_at=datetime(2026, 9, 10, 8, tzinfo=UTC),
        level=EventRiskLevel.CLEAR,
        reasons=(),
    )

    result = _assessment(
        "600519",
        "贵州茅台",
        events=(resumed, suspended),
        checked_at=datetime(2026, 9, 14, 2, tzinfo=UTC),
    )

    assert result.level is EventRiskLevel.CLEAR
    assert result.reason_codes == ()
    assert classify_announcement_title("关于撤销退市风险警示的公告")[0] is EventRiskLevel.CLEAR


def test_relief_words_do_not_hide_remaining_or_unapproved_risk() -> None:
    level, reasons = classify_announcement_title("关于申请撤销退市风险警示的公告")
    assert level is EventRiskLevel.BLOCKED
    assert reasons == ("DELISTING_RISK",)

    level, reasons = classify_announcement_title(
        "关于撤销退市风险警示并继续实施其他风险警示的公告"
    )
    assert level is EventRiskLevel.REVIEW
    assert reasons == ("OTHER_RISK_WARNING",)

    level, reasons = classify_announcement_title("公司股票复牌暨被实施退市风险警示的公告")
    assert level is EventRiskLevel.BLOCKED
    assert reasons == ("DELISTING_RISK",)


def test_public_risk_store_round_trip_and_rejects_unknown_fields(tmp_path) -> None:
    path = tmp_path / "risk.json"
    store = PublicRiskStore(path)
    snapshot = _snapshot()
    store.save(snapshot)

    loaded = PublicRiskStore(path).latest()
    assert loaded == snapshot

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact is invalid"):
        PublicRiskStore(path)


def test_public_risk_event_rejects_non_official_or_active_content_url() -> None:
    with pytest.raises(ValueError, match="official static host"):
        PublicRiskEvent(
            **{
                **_event().__dict__,
                "source_url": "https://example.com/announcement.pdf",
            }
        )
    with pytest.raises(ValueError, match="HTTPS URL"):
        PublicRiskEvent(
            **{
                **_event().__dict__,
                "source_url": "javascript:alert(1)",
            }
        )


def test_public_risk_store_rejects_older_replacement(tmp_path) -> None:
    store = PublicRiskStore(tmp_path / "risk.json")
    newer = _snapshot(fetched_at=datetime(2026, 9, 14, 2, tzinfo=UTC))
    older = _snapshot(fetched_at=datetime(2026, 9, 14, 1, tzinfo=UTC))
    store.save(newer)
    with pytest.raises(ValueError, match="older"):
        store.save(older)


def test_invalid_runtime_cache_can_be_replaced_only_after_good_snapshot(tmp_path) -> None:
    path = tmp_path / "risk.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError):
        PublicRiskStore(path)

    recovering = PublicRiskStore(path, load_existing=False)
    assert recovering.latest() is None
    recovering.save(_snapshot())

    assert PublicRiskStore(path).latest() == _snapshot()


def test_coordinator_uses_china_calendar_date(tmp_path) -> None:
    captured: dict[str, object] = {}

    class Provider:
        def fetch(self, symbols, *, window_end, window_days, now):
            captured.update(
                symbols=tuple(symbols), window_end=window_end, window_days=window_days, now=now
            )
            return PublicRiskSnapshot(
                source="CNINFO",
                fetched_at=now,
                window_start=window_end,
                window_end=window_end,
                status="FRESH",
                notice_zh="检查完成。",
                assessments=(),
            )

    published: list[tuple[object, str, str]] = []
    coordinator = PublicRiskCoordinator(
        store=PublicRiskStore(tmp_path / "risk.json"),
        symbols=("600519",),
        publish=lambda snapshot, status, notice: published.append((snapshot, status, notice)),
        provider=Provider(),
        interval_seconds=60,
        clock=lambda: datetime(2026, 9, 13, 16, 30, tzinfo=UTC),
    )

    coordinator.refresh_once()

    assert captured["window_end"] == date(2026, 9, 14)
    assert published[0][1] == "FRESH"


def test_empty_startup_coordinator_wakes_when_first_candidates_arrive(tmp_path) -> None:
    symbols: list[str] = []
    called = threading.Event()

    class Provider:
        def fetch(self, requested, *, window_end, window_days, now):
            del window_days
            assert tuple(requested) == ("600519",)
            called.set()
            return PublicRiskSnapshot(
                source="CNINFO",
                fetched_at=now,
                window_start=window_end,
                window_end=window_end,
                status="FRESH",
                notice_zh="检查完成。",
                assessments=(),
            )

    coordinator = PublicRiskCoordinator(
        store=PublicRiskStore(tmp_path / "risk.json"),
        symbols=lambda: symbols,
        publish=lambda snapshot, status, notice: None,
        provider=Provider(),
        interval_seconds=60,
        clock=lambda: datetime(2026, 9, 14, 2, tzinfo=UTC),
    )
    coordinator.start()
    symbols.append("600519")
    coordinator.request_refresh()
    try:
        assert called.wait(timeout=2)
    finally:
        coordinator.stop()


def test_major_announcement_removes_actionable_daily_price_guidance() -> None:
    signal = OfficialModelSignal(
        signal_date=date(2026, 9, 13),
        symbol="600519",
        name="贵州茅台",
        normalized_score=80,
        strategy_version="test-v1",
        source="baostock",
    )
    plan = PriceGuidancePlan(
        plan_id="daily-600519",
        symbol="600519",
        plan_type=PricePlanType.DAILY_CANDIDATE,
        guidance_level=GuidanceLevel.RESEARCH_REFERENCE,
        state=GuidanceState.RESEARCH_REFERENCE,
        calculation_date=date(2026, 9, 13),
        valid_for=date(2026, 9, 14),
        entry_lower="9.80",
        entry_upper="10.00",
        maximum_acceptable_price="10.10",
        invalidation_price="9.00",
        protection_price=None,
        reduce_lower=None,
        reduce_upper=None,
        suggested_quantity=0,
        evidence_cutoff=datetime(2026, 9, 13, 7, tzinfo=UTC),
        model_version="test-model",
        feature_version="test-features",
        config_version="test-config",
        data_version="test-data",
        reason_codes=("TEST",),
    )
    assessment = _snapshot().assessments[0]

    payload = _official_signal_payload(
        signal,
        now=datetime(2026, 9, 14, 2, tzinfo=UTC),
        price_guidance=plan,
        event_risk=assessment,
    )

    guidance = payload["price_guidance"]
    assert payload["event_risk"]["level"] == "BLOCKED"
    assert guidance["state"] == "NO_RELIABLE_GUIDANCE"
    assert guidance["entry_lower"] is None
    assert guidance["maximum_acceptable_price"] is None
    assert "PUBLIC_EVENT_RISK" in guidance["reason_codes"]


def test_required_unchecked_risk_fails_closed_and_daily_refresh_wakes(tmp_path) -> None:
    class Provider:
        name = "injected"

    signal = OfficialModelSignal(
        signal_date=date(2026, 9, 13),
        symbol="600519",
        name="贵州茅台",
        normalized_score=80,
        strategy_version="test-v1",
        source="baostock",
    )
    official_store = OfficialSignalStore()
    official_store.put_signals((signal,))
    guidance_store = PriceGuidanceStore(tmp_path / "guidance.json")
    guidance_store.replace_plans(
        (
            PriceGuidancePlan(
                plan_id="daily-600519",
                symbol="600519",
                plan_type=PricePlanType.DAILY_CANDIDATE,
                guidance_level=GuidanceLevel.RESEARCH_REFERENCE,
                state=GuidanceState.RESEARCH_REFERENCE,
                calculation_date=date(2026, 9, 13),
                valid_for=date(2026, 9, 14),
                entry_lower="9.80",
                entry_upper="10.00",
                maximum_acceptable_price="10.10",
                invalidation_price="9.00",
                protection_price=None,
                reduce_lower=None,
                reduce_upper=None,
                suggested_quantity=0,
                evidence_cutoff=datetime(2026, 9, 13, 7, tzinfo=UTC),
                model_version="test-model",
                feature_version="test-features",
                config_version="test-config",
                data_version="test-data",
                reason_codes=("TEST",),
            ),
        )
    )
    service = WorkbenchService(
        provider=Provider(),
        official_signal_store=official_store,
        price_guidance_store=guidance_store,
        public_risk_required=True,
        clock=lambda: datetime(2026, 9, 14, 2, tzinfo=UTC),
    )

    row = service.snapshot()["official_daily_candidates"][0]
    assert row["event_risk"]["level"] == "UNKNOWN"
    assert row["price_guidance"]["state"] == "NO_RELIABLE_GUIDANCE"
    assert row["price_guidance"]["entry_lower"] is None

    wake_count = 0

    def wake() -> None:
        nonlocal wake_count
        wake_count += 1

    service.set_public_risk_refresh_request(wake)
    replacement = OfficialModelSignal(
        signal_date=date(2026, 9, 14),
        symbol="000001",
        name="平安银行",
        normalized_score=75,
        strategy_version="test-v2",
        source="baostock",
    )
    service.publish_official_daily((replacement,), status="FRESH", notice_zh="更新完成。")

    assert service.public_risk_symbols() == ("000001",)
    assert wake_count == 1
