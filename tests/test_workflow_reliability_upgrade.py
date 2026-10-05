import threading
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from a_share_quant.intelligence.contracts import (
    EventRiskLevel,
    PublicRiskAssessment,
    PublicRiskSnapshot,
)
from a_share_quant.runtime.daily_refresh import DailyRefreshSummary
from a_share_quant.runtime.eod_coordinator import EODCoordinator
from a_share_quant.runtime.scheduler import MarketSession, SessionResolver
from a_share_quant.storage.official_signal_store import OfficialSignalStore
from a_share_quant.workbench import app
from a_share_quant.workbench.advisory_service import AdvisoryWorkbenchService
from a_share_quant.workbench.service import WorkbenchService

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 4, 10, tzinfo=TZ)


class Provider:
    name = "test-provider"

    def get_priority_quotes(self, symbols):
        return ()


def risk_snapshot(checked_at, level=EventRiskLevel.CLEAR):
    return PublicRiskSnapshot(
        source="CNINFO",
        fetched_at=checked_at,
        window_start=checked_at.date(),
        window_end=checked_at.date(),
        status="FRESH",
        notice_zh="检查完成",
        assessments=(
            PublicRiskAssessment(
                symbol="000001",
                name="平安银行",
                level=level,
                reason_codes=(),
                events=(),
                checked_at=checked_at,
            ),
        ),
    )


@pytest.mark.parametrize("failure", [False, True])
def test_old_clear_risk_cannot_authorize_guidance(failure):
    snapshot = risk_snapshot(NOW - timedelta(days=20))
    service = WorkbenchService(
        provider=Provider(),
        public_risk_snapshot=snapshot,
        public_risk_required=True,
        clock=lambda: NOW,
    )
    if failure:
        service.publish_public_risk(snapshot, "UPDATE_FAILED", "刷新失败")
    risk = service.public_risk_for_symbol("000001")
    assert risk["level"] == "UNKNOWN"
    assert "PUBLIC_RISK_EXPIRED" in risk["reason_codes"]
    assert service.snapshot()["public_risk_health"]["unknown_count"] == 1


def test_risk_expiry_is_checked_at_read_time():
    current = [NOW]
    service = WorkbenchService(
        provider=Provider(),
        public_risk_snapshot=risk_snapshot(NOW),
        public_risk_required=True,
        clock=lambda: current[0],
    )
    assert service.public_risk_for_symbol("000001")["level"] == "CLEAR"
    current[0] += timedelta(hours=6, seconds=1)
    assert service.public_risk_for_symbol("000001")["level"] == "UNKNOWN"
    assert service.snapshot()["public_risk_health"]["status"] == "STALE"


@pytest.mark.parametrize("level", [EventRiskLevel.REVIEW, EventRiskLevel.BLOCKED])
def test_old_adverse_risk_is_not_silently_cleared(level):
    snapshot = risk_snapshot(NOW - timedelta(days=20), level=level)
    service = WorkbenchService(
        provider=Provider(),
        public_risk_snapshot=snapshot,
        clock=lambda: NOW,
    )
    risk = service.public_risk_for_symbol("000001")
    assert risk["level"] == level.value
    assert "PUBLIC_RISK_EXPIRED" in risk["reason_codes"]


def test_failed_fresh_clear_check_fails_closed():
    snapshot = risk_snapshot(NOW)
    service = WorkbenchService(
        provider=Provider(),
        public_risk_snapshot=snapshot,
        clock=lambda: NOW,
    )
    service.publish_public_risk(snapshot, "UPDATE_FAILED", "刷新失败")
    assert service.public_risk_for_symbol("000001")["level"] == "UNKNOWN"


def test_future_risk_timestamp_fails_closed():
    service = WorkbenchService(
        provider=Provider(),
        public_risk_snapshot=risk_snapshot(NOW + timedelta(minutes=1)),
        clock=lambda: NOW,
    )
    assert service.public_risk_for_symbol("000001")["level"] == "UNKNOWN"


def test_holiday_initial_failure_is_retried_by_periodic_tick():
    calls = []

    def refresh(day):
        calls.append(day)
        if len(calls) == 1:
            raise OSError("transient")

    coordinator = EODCoordinator(refresh=refresh, clock=lambda: NOW)
    assert coordinator.run_initial() is False
    assert coordinator.run_due() is True
    assert calls == [date(2026, 9, 30)] * 2
    assert coordinator.run_due() is False


def test_failed_daily_summary_is_not_marked_completed(monkeypatch, tmp_path):
    store = OfficialSignalStore()
    service = WorkbenchService(provider=Provider(), official_signal_store=store)
    monkeypatch.setattr(
        app,
        "refresh_daily_data_if_due",
        lambda *a, **k: DailyRefreshSummary(skipped=False, symbols_failed=1),
    )
    calls = []

    def refresh(day):
        calls.append(day)
        return app.refresh_eod_state(
            day=day,
            repo_root=tmp_path,
            official_store=store,
            guidance_store=None,
            service=service,
        )

    coordinator = EODCoordinator(
        refresh=refresh,
        clock=lambda: datetime(2026, 10, 9, 16, tzinfo=TZ),
    )
    assert coordinator.run_due() is False
    assert coordinator.run_due() is False
    assert len(calls) == 2
    assert coordinator.last_error is not None
    assert service.snapshot()["daily_data_status"] == "UPDATE_FAILED"


def test_candidate_generation_failure_is_not_marked_completed(monkeypatch, tmp_path):
    store = OfficialSignalStore()
    refreshed = OfficialSignalStore()
    refreshed.set_refresh_status("STALE_DATA", "日线过期")
    service = WorkbenchService(provider=Provider(), official_signal_store=store)
    monkeypatch.setattr(
        app, "refresh_daily_data_if_due", lambda *a, **k: DailyRefreshSummary(skipped=True)
    )
    monkeypatch.setattr(app, "load_or_generate_official_store", lambda *a, **k: refreshed)
    result = app.refresh_eod_state(
        day=date(2026, 10, 9),
        repo_root=tmp_path,
        official_store=store,
        guidance_store=None,
        service=service,
    )
    assert result.success is False
    assert result.status == "STALE_DATA"


def test_production_session_resolver_observes_exchange_holiday():
    assert (
        SessionResolver().resolve(datetime(2026, 10, 5, 10, tzinfo=TZ)) is MarketSession.NON_TRADING
    )


def test_unknown_calendar_year_does_not_crash_eod_worker():
    coordinator = EODCoordinator(
        refresh=lambda day: None,
        clock=lambda: datetime(2027, 1, 4, 16, tzinfo=TZ),
    )
    assert coordinator.run_initial() is False
    assert coordinator.run_due() is False
    assert coordinator.last_error == "CALENDAR_UNAVAILABLE"


def test_new_manual_holding_updates_priority_without_restart(tmp_path):
    advisory = AdvisoryWorkbenchService(
        initial_cash=100000,
        ledger_path=tmp_path / "ledger.jsonl",
        known_instruments={"000001": "平安银行"},
        today=lambda: NOW.date(),
    )
    service = WorkbenchService(provider=Provider(), clock=lambda: NOW)
    wake = []
    service.set_public_risk_refresh_request(lambda: wake.append(True))
    advisory.set_holdings_changed_callback(service.update_account_symbols)
    preview = advisory.preview_manual_buy(
        name="平安银行",
        code="000001",
        quantity=100,
        price=10,
    )
    advisory.confirm_manual_buy(preview["confirmation_token"])
    assert service.public_risk_symbols() == ("000001",)
    assert service.scheduler.priority_symbols == ("000001",)
    assert wake == [True]


def test_removed_account_symbols_do_not_remove_daily_candidates():
    from a_share_quant.signals.realtime import OfficialModelSignal

    store = OfficialSignalStore()
    store.put_signals(
        (
            OfficialModelSignal(
                signal_date=NOW.date(),
                symbol="600519",
                name="贵州茅台",
                normalized_score=80,
                strategy_version="test",
                source="baostock",
            ),
        )
    )
    service = WorkbenchService(provider=Provider(), official_signal_store=store)
    service.update_account_symbols(("000001",))
    service.update_account_symbols(())
    assert service.public_risk_symbols() == ("600519",)


def test_refresh_failure_notice_does_not_expose_provider_credentials():
    def refresh(day):
        raise RuntimeError("secret-token=https://private.example/account")

    coordinator = EODCoordinator(refresh=refresh, clock=lambda: NOW)
    coordinator.run_initial()
    assert "secret-token" not in str(coordinator.snapshot())


def test_holiday_daily_generation_and_refresh_share_last_session():
    from a_share_quant.research.daily_candidates import (
        latest_complete_signal_date,
        validate_daily_data_freshness,
    )
    from a_share_quant.runtime.daily_refresh import _latest_complete_weekday

    assert latest_complete_signal_date(NOW) == date(2026, 9, 30)
    assert _latest_complete_weekday(NOW) == date(2026, 9, 30)
    validate_daily_data_freshness(date(2026, 9, 30), now=NOW, max_business_day_lag=0)


def test_eod_retry_backoff_and_attempt_limit():
    current = [NOW]
    calls = []

    def refresh(day):
        calls.append(day)
        raise OSError("temporary")

    coordinator = EODCoordinator(
        refresh=refresh, clock=lambda: current[0], retry_base_seconds=60,
        maximum_attempts=2,
    )
    coordinator.run_initial()
    coordinator.run_due()
    assert len(calls) == 1
    current[0] += timedelta(seconds=60)
    coordinator.run_due()
    assert len(calls) == 2
    assert coordinator.snapshot()["status"] == "RETRY_EXHAUSTED"
    assert coordinator.snapshot()["next_retry_at"] is None
    current[0] += timedelta(hours=2)
    coordinator.run_due()
    assert len(calls) == 2


def test_new_imported_positions_update_monitor_universe(tmp_path):
    from a_share_quant.account.import_inbox import AccountImportInbox
    from a_share_quant.account.snapshot_store import AccountSnapshotStore

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "positions.csv").write_text(
        "证券代码,证券名称,证券数量,可用数量,冻结数量,成本价,可用资金,日期\n"
        "000001,平安银行,300,200,100,10.1234,88000.50,2026-08-11\n",
        encoding="utf-8",
    )
    advisory = AdvisoryWorkbenchService(
        initial_cash=100000, ledger_path=tmp_path / "ledger.jsonl",
        account_import_inbox=AccountImportInbox(inbox),
        account_snapshot_store=AccountSnapshotStore(tmp_path / "snapshot.json"),
    )
    service = WorkbenchService(provider=Provider())
    advisory.set_holdings_changed_callback(service.update_account_symbols)
    file_id = advisory.list_account_imports()["files"][0]["file_id"]
    preview = advisory.preview_account_import(file_id)
    result = advisory.confirm_account_import(preview["confirmation_token"])
    assert result["holdings_sync_status"] == "SUCCESS"
    assert service.public_risk_symbols() == ("000001",)
    assert not (tmp_path / "ledger.jsonl").exists()


@pytest.mark.parametrize("error_type", [OSError, AttributeError, KeyError])
def test_notification_failure_does_not_undo_or_duplicate_committed_fill(tmp_path, error_type):
    advisory = AdvisoryWorkbenchService(
        initial_cash=100000, ledger_path=tmp_path / "ledger.jsonl",
        today=lambda: NOW.date(),
    )

    def failed_callback(symbols):
        raise error_type("monitor unavailable")

    advisory.set_holdings_changed_callback(failed_callback)
    preview = advisory.preview_manual_buy(
        name="平安银行", code="000001", quantity=100, price=10,
    )
    result = advisory.confirm_manual_buy(preview["confirmation_token"])
    assert result["recorded"] is True
    assert result["holdings_sync_status"] == "UPDATE_FAILED"
    assert advisory.holdings()["positions"][0]["total_quantity"] == 100
    assert advisory.holdings()["holdings_sync_error_code"] == error_type.__name__
    with pytest.raises(ValueError):
        advisory.confirm_manual_buy(preview["confirmation_token"])


def test_eod_permission_error_stops_automatic_retries():
    attempts = []

    def denied(day):
        attempts.append(day)
        raise PermissionError("private-path")

    coordinator = EODCoordinator(
        refresh=denied, clock=lambda: datetime(2026, 10, 4, 12, tzinfo=timezone.utc),
    )
    assert coordinator.run_initial() is False
    assert coordinator.run_due() is False
    assert len(attempts) == 1
    state = coordinator.snapshot()
    assert state["status"] == "BLOCKED"
    assert state["error_code"] == "PERMISSION_DENIED"
    assert state["next_retry_at"] is None


def test_concurrent_notifications_cannot_restore_an_old_holdings_universe(tmp_path, monkeypatch):
    advisory = AdvisoryWorkbenchService(
        initial_cash=100000, ledger_path=tmp_path / "ledger.jsonl",
        today=lambda: NOW.date(),
    )
    service = WorkbenchService(provider=Provider())
    first_started = threading.Event()
    release_first = threading.Event()
    second_committed = threading.Event()
    notifications = []
    errors = []
    original_confirm = advisory._confirm_manual_buy_locked

    def recorded(token):
        result = original_confirm(token)
        if result["code"] == "600519":
            second_committed.set()
        return result

    monkeypatch.setattr(advisory, "_confirm_manual_buy_locked", recorded)

    def notify(symbols):
        notifications.append(symbols)
        if symbols == ("000001",):
            first_started.set()
            assert release_first.wait(3)
        service.update_account_symbols(symbols)

    advisory.set_holdings_changed_callback(notify)
    first = advisory.preview_manual_buy(name="平安银行", code="000001", quantity=100, price=10)
    second = advisory.preview_manual_buy(name="贵州茅台", code="600519", quantity=100, price=10)

    def confirm(token):
        try:
            advisory.confirm_manual_buy(token)
        except Exception as exc:
            errors.append(type(exc).__name__)

    first_thread = threading.Thread(target=confirm, args=(first["confirmation_token"],))
    second_thread = threading.Thread(target=confirm, args=(second["confirmation_token"],))
    first_thread.start()
    try:
        assert first_started.wait(3)
        second_thread.start()
        assert second_committed.wait(3)
        assert notifications == [("000001",)]
    finally:
        release_first.set()
        first_thread.join(timeout=3)
        if second_thread.ident is not None:
            second_thread.join(timeout=3)
    assert not errors
    assert service.public_risk_symbols() == ("000001", "600519")
