from __future__ import annotations

import json
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from a_share_quant.advisory.price_contracts import PricePlanType
from a_share_quant.intelligence.contracts import EventRiskLevel
from a_share_quant.market.trading_calendar import AShareTradingCalendar, CalendarUnavailableError
from a_share_quant.research.prospective_competition import _future_weekday
from a_share_quant.runtime import daily_bundle
from a_share_quant.runtime.daily_bundle import DailyBundleTransaction
from a_share_quant.runtime.daily_refresh import DailyRefreshSummary, refresh_daily_data_if_due
from a_share_quant.runtime.daily_refresh_worker import run_bounded_daily_refresh
from a_share_quant.runtime.eod_coordinator import EODCoordinator, EODRefreshResult
from a_share_quant.runtime.price_guidance import PriceGuidanceRuntime
from a_share_quant.storage.atomic_json import read_checked_json, write_checked_json
from a_share_quant.storage.official_signal_store import OfficialSignalStore
from a_share_quant.storage.price_guidance_store import PriceGuidanceStore
from a_share_quant.storage.public_risk_store import PublicRiskStore
from a_share_quant.workbench import app
from a_share_quant.workbench.service import WorkbenchService
from tests.test_daily_refresh import FakeProvider
from tests.test_official_daily_bootstrap import _signal
from tests.test_workflow_reliability_upgrade import Provider, risk_snapshot

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 4, 10, tzinfo=TZ)
DAY = date(2026, 9, 30)


def artifacts(root, day=DAY, holdings=()):
    signals = OfficialSignalStore(root / "signals.json")
    signals.put_signals((_signal(signal_date=day),))
    guidance = PriceGuidanceStore(root / "guidance.json")
    PriceGuidanceRuntime(
        bars_by_symbol={},
        store=guidance,
        candidate_symbols=("000001",),
        holding_positions=tuple({"symbol": symbol} for symbol in holdings),
    ).generate(day, AShareTradingCalendar().next_session(day))
    return signals, guidance


def test_daily_bundle_recovers_interruption_between_file_replacements(tmp_path, monkeypatch):
    old, old_guidance = artifacts(tmp_path, date(2026, 9, 29))
    new, new_guidance = artifacts(tmp_path / "staged")
    transaction = DailyBundleTransaction(tmp_path, old.path, old_guidance.path)
    original_write = daily_bundle.atomic_write_bytes

    def interrupted_write(path, raw):
        if path == old_guidance.path.resolve():
            raise OSError("simulated interruption after first file")
        original_write(path, raw)

    monkeypatch.setattr(daily_bundle, "atomic_write_bytes", interrupted_write)
    with pytest.raises(OSError):
        transaction.publish(DAY, {"signals": new.path, "guidance": new_guidance.path})
    assert transaction.journal.exists()
    assert OfficialSignalStore(old.path).latest()[0].data_cutoff == DAY
    assert PriceGuidanceStore(old_guidance.path).plans()[0].calculation_date != DAY
    monkeypatch.setattr(daily_bundle, "atomic_write_bytes", original_write)
    restarted = DailyBundleTransaction(tmp_path, old.path, old_guidance.path)
    assert restarted.recover() is True
    assert restarted.recover() is False
    assert OfficialSignalStore(old.path).latest() == new.latest()
    assert PriceGuidanceStore(old_guidance.path).plans() == new_guidance.plans()


@pytest.mark.parametrize("bad", ["coverage", "date", "holding", "validity"])
def test_invalid_daily_batch_never_overwrites_existing_artifacts(tmp_path, bad):
    old, prices = artifacts(tmp_path, date(2026, 9, 29))
    new, new_prices = artifacts(tmp_path / "staged")
    plans = new_prices.plans()
    if bad == "coverage":
        new_prices.replace_plans(())
    elif bad == "date":
        new_prices.replace_plans((replace(plans[0], calculation_date=date(2026, 9, 29)),))
    elif bad == "validity":
        new_prices.replace_plans((replace(plans[0], valid_for=date(2026, 10, 9)),))
    previous = (old.path.read_bytes(), prices.path.read_bytes())
    transaction = DailyBundleTransaction(tmp_path, old.path, prices.path)
    with pytest.raises(ValueError):
        transaction.publish(
            DAY,
            {"signals": new.path, "guidance": new_prices.path},
            holding_symbols=("000002",) if bad == "holding" else (),
        )
    assert previous == (old.path.read_bytes(), prices.path.read_bytes())
    assert not transaction.journal.exists()


def test_journal_target_tampering_is_rejected_before_any_write(tmp_path, monkeypatch):
    old, prices = artifacts(tmp_path, date(2026, 9, 29))
    new, new_prices = artifacts(tmp_path / "staged")
    transaction = DailyBundleTransaction(tmp_path, old.path, prices.path)
    monkeypatch.setattr(transaction, "_apply", lambda body: "staged-only")
    transaction.publish(DAY, {"signals": new.path, "guidance": new_prices.path})
    payload = read_checked_json(transaction.journal, maximum_bytes=transaction.MAX_BYTES)
    payload["paths"]["signals"] = "../outside.json"
    write_checked_json(transaction.journal, payload, maximum_bytes=transaction.MAX_BYTES)
    previous = old.path.read_bytes()
    with pytest.raises(ValueError, match="targets"):
        DailyBundleTransaction(tmp_path, old.path, prices.path).recover()
    assert old.path.read_bytes() == previous


def test_checkpoint_restores_backoff_and_budget_across_restart(tmp_path):
    path = tmp_path / "eod.json"
    calls = []
    current = [NOW]

    def fail(day):
        calls.append(day)
        return EODRefreshResult(False, "UPDATE_FAILED", "刷新失败")

    first = EODCoordinator(
        refresh=fail,
        clock=lambda: current[0],
        checkpoint_path=path,
        retry_base_seconds=60,
        maximum_attempts=2,
    )
    assert first.run_initial() is False
    restarted = EODCoordinator(
        refresh=fail,
        clock=lambda: current[0],
        checkpoint_path=path,
        retry_base_seconds=60,
        maximum_attempts=2,
    )
    assert restarted.snapshot()["attempts"] == 1
    assert restarted.run_initial() is False
    current[0] += timedelta(seconds=60)
    assert restarted.run_due() is False
    assert len(calls) == 2
    exhausted = EODCoordinator(
        refresh=fail,
        clock=lambda: current[0],
        checkpoint_path=path,
        retry_base_seconds=60,
        maximum_attempts=2,
    )
    assert exhausted.run_initial() is False
    assert exhausted.snapshot()["status"] == "RETRY_EXHAUSTED"
    assert exhausted.retry_after_review()["status"] == "PENDING"
    assert exhausted.run_due() is False
    assert len(calls) == 3


def test_completed_checkpoint_is_idempotent_but_artifacts_are_revalidated(tmp_path):
    path = tmp_path / "eod.json"
    calls = []
    EODCoordinator(
        refresh=lambda day: calls.append(day), clock=lambda: NOW, checkpoint_path=path
    ).run_initial()
    restarted = EODCoordinator(
        refresh=lambda day: calls.append(day),
        clock=lambda: NOW,
        checkpoint_path=path,
        completion_is_valid=lambda day: True,
    )
    assert restarted.run_initial() is False
    assert len(calls) == 1
    invalidated = EODCoordinator(
        refresh=lambda day: calls.append(day),
        clock=lambda: NOW,
        checkpoint_path=path,
        completion_is_valid=lambda day: False,
    )
    assert invalidated.run_initial() is True
    assert len(calls) == 2


def test_interrupted_running_checkpoint_resumes_without_resetting_attempts(tmp_path):
    path = tmp_path / "eod.json"

    def interrupted(day):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        EODCoordinator(refresh=interrupted, clock=lambda: NOW, checkpoint_path=path).run_initial()
    restored = EODCoordinator(refresh=lambda day: None, clock=lambda: NOW, checkpoint_path=path)
    assert restored.snapshot()["status"] == "PENDING"
    assert restored.snapshot()["attempts"] == 1
    assert restored.run_due() is True
    assert restored.snapshot()["attempts"] == 2


def test_rejected_checkpoint_stops_without_overwriting_evidence(tmp_path):
    path = tmp_path / "eod.json"
    path.write_text("not-json", encoding="utf-8")
    calls = []
    coordinator = EODCoordinator(
        refresh=lambda day: calls.append(day), clock=lambda: NOW, checkpoint_path=path
    )
    coordinator.start(interval_seconds=0.1)
    assert coordinator.run_initial() is False
    with pytest.raises(ValueError):
        coordinator.retry_after_review()
    assert calls == []
    assert path.read_text(encoding="utf-8") == "not-json"


def test_permission_block_survives_restart(tmp_path):
    path = tmp_path / "eod.json"

    def denied(day):
        raise PermissionError("private path should not be exposed")

    first = EODCoordinator(refresh=denied, clock=lambda: NOW, checkpoint_path=path)
    assert first.run_initial() is False
    restored = EODCoordinator(refresh=lambda day: None, clock=lambda: NOW, checkpoint_path=path)
    assert restored.run_initial() is False
    assert restored.snapshot()["error_code"] == "PERMISSION_DENIED"
    restored.retry_after_review()
    assert restored.run_due() is True
    assert "private path" not in path.read_text(encoding="utf-8")


def test_checkpoint_write_failure_prevents_provider_call(tmp_path, monkeypatch):
    calls = []
    coordinator = EODCoordinator(
        refresh=lambda day: calls.append(day),
        clock=lambda: NOW,
        checkpoint_path=tmp_path / "eod.json",
    )
    monkeypatch.setattr(coordinator, "_persist", lambda: (_ for _ in ()).throw(PermissionError()))
    assert coordinator.run_initial() is False
    assert calls == []
    assert coordinator.snapshot()["status"] == "BLOCKED"


@pytest.mark.parametrize("failed_write", [False, True])
def test_account_notification_cannot_unblock_permissions_on_new_session(
    tmp_path, monkeypatch, failed_write,
):
    now = [NOW]
    calls = []
    denied = [True]

    def refresh(day):
        calls.append(day)
        if denied[0]:
            raise PermissionError("test only")

    coordinator = EODCoordinator(
        refresh=refresh, clock=lambda: now[0], checkpoint_path=tmp_path / "eod.json",
    )
    if failed_write:
        original = coordinator._persist
        monkeypatch.setattr(
            coordinator, "_persist", lambda: (_ for _ in ()).throw(PermissionError()),
        )
        assert coordinator.run_initial() is False
        monkeypatch.setattr(coordinator, "_persist", original)
        expected_calls = 0
    else:
        assert coordinator.run_initial() is False
        expected_calls = 1
    error_code = coordinator.snapshot()["error_code"]
    now[0] = NOW.replace(year=2026, month=10, day=8, hour=16)
    denied[0] = False  # Fixing the cause alone is not approval to resume.
    coordinator.request_refresh()
    assert coordinator.snapshot()["status"] == "BLOCKED"
    assert coordinator.snapshot()["error_code"] == error_code
    assert coordinator.run_due() is False
    assert len(calls) == expected_calls
    restored = EODCoordinator(
        refresh=refresh, clock=lambda: now[0], checkpoint_path=tmp_path / "eod.json",
    )
    assert restored.run_initial() is False
    assert len(calls) == expected_calls
    restored.retry_after_review()
    assert restored.run_due() is True
    assert len(calls) == expected_calls + 1


def test_holding_change_during_run_is_not_lost(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def refresh(day):
        calls.append(day)
        if len(calls) == 1:
            entered.set()
            assert release.wait(2)

    coordinator = EODCoordinator(
        refresh=refresh, clock=lambda: NOW, checkpoint_path=tmp_path / "eod.json"
    )
    thread = threading.Thread(target=coordinator.run_initial)
    thread.start()
    try:
        assert entered.wait(2)
        coordinator.request_refresh()
    finally:
        release.set()
        thread.join(3)
    assert coordinator.snapshot()["status"] == "PENDING"
    assert coordinator.run_due() is True
    assert len(calls) == 2


@pytest.mark.parametrize("level", [EventRiskLevel.REVIEW, EventRiskLevel.BLOCKED])
def test_empty_new_announcement_window_does_not_clear_old_warning(tmp_path, level):
    store = PublicRiskStore(tmp_path / "risk.json")
    old = risk_snapshot(NOW - timedelta(days=40), level)
    store.save(old)
    store.save(risk_snapshot(NOW))
    restored = PublicRiskStore(store.path).latest().assessments[0]
    assert restored.level is level
    assert restored.checked_at == old.assessments[0].checked_at
    assert "PUBLIC_RISK_CLEARANCE_REQUIRED" in restored.reason_codes


def test_absent_symbol_warning_survives_new_universe(tmp_path):
    store = PublicRiskStore(tmp_path / "risk.json")
    old = risk_snapshot(NOW - timedelta(days=40), EventRiskLevel.BLOCKED)
    store.save(old)
    store.save(replace(risk_snapshot(NOW), assessments=()))
    assert (
        PublicRiskStore(store.path).latest().by_symbol()["000001"].level is EventRiskLevel.BLOCKED
    )


def test_maturity_calendar_skips_national_day_and_unknown_year_fails_closed():
    assert _future_weekday(DAY, 5) == date(2026, 10, 14)
    with pytest.raises(CalendarUnavailableError):
        _future_weekday(date(2026, 12, 31), 5)
    assert AShareTradingCalendar().metadata()["version"] == "ashare-exchange-2026-v1"


def test_stale_holding_bars_do_not_generate_current_price_guidance(tmp_path):
    # The implementation must reject stale input BEFORE trying to derive prices.
    result = PriceGuidanceRuntime(
        bars_by_symbol={"000002": pd.DataFrame({"date": [date(2026, 9, 29)]})},
        store=PriceGuidanceStore(tmp_path / "prices.json"),
        holding_positions=({"symbol": "000002"},),
        require_current_session=True,
    ).generate(DAY, date(2026, 10, 8))
    assert result.plans[0].reason_codes == ("STALE_DAILY_INPUT",)
    assert result.plans[0].protection_price is None


def test_closed_system_never_launches_daily_fetch_child(tmp_path, monkeypatch):
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("unexpected child"))
    with pytest.raises(RuntimeError, match="CANCELLED"):
        run_bounded_daily_refresh(tmp_path, DAY, (), stop_event=stop)


def test_daily_worker_returns_cached_results_in_a_real_child_without_network(tmp_path):
    daily = tmp_path / "data" / "lake" / "daily_bars"
    daily.mkdir(parents=True)
    pd.DataFrame(
        {
            "date": pd.bdate_range(end=DAY, periods=252).date,
            "data_version": ["baostock-unadjusted-v1"] * 252,
        }
    ).to_parquet(
        daily / "000001.parquet",
        index=False,
    )
    result = run_bounded_daily_refresh(
        tmp_path, DAY, (), stop_event=threading.Event(), timeout_seconds=15
    )
    assert result.skipped is True
    assert result.symbol_results[0].status == "FRESH"


def test_eod_generates_holding_and_daily_plans_in_one_real_file_batch(tmp_path, monkeypatch):
    signals, prices = artifacts(tmp_path, date(2026, 9, 29))
    service = WorkbenchService(
        provider=Provider(),
        official_signal_store=signals,
        price_guidance_store=prices,
        clock=lambda: NOW,
    )
    service.update_account_symbols(("000002",))
    generator = app.load_or_generate_official_store
    received = []

    def refreshed_data(*a, **kw):
        received.append(kw["required_symbols"])
        return DailyRefreshSummary(skipped=True)

    monkeypatch.setattr(app, "refresh_daily_data_if_due", refreshed_data)
    monkeypatch.setattr(
        app,
        "load_or_generate_official_store",
        lambda path, **kw: generator(
            path,
            generator=lambda: (_signal(signal_date=DAY),),
        ),
    )
    result = app.refresh_eod_state(
        day=DAY, repo_root=tmp_path, official_store=signals, guidance_store=prices, service=service
    )
    assert result.success is True
    assert received == [("000002",)]
    assert {(item.symbol, item.plan_type) for item in PriceGuidanceStore(prices.path).plans()} == {
        ("000001", PricePlanType.DAILY_CANDIDATE),
        ("000002", PricePlanType.HOLDING),
    }
    assert app.completed_daily_bundle_is_valid(DAY, service) is True
    service.update_account_symbols(("000003",))
    assert app.completed_daily_bundle_is_valid(DAY, service) is False


def test_failed_price_generation_does_not_publish_new_candidates(tmp_path, monkeypatch):
    signals, prices = artifacts(tmp_path, date(2026, 9, 29))
    service = WorkbenchService(
        provider=Provider(),
        official_signal_store=signals,
        price_guidance_store=prices,
        clock=lambda: NOW,
    )
    old_bytes = signals.path.read_bytes(), prices.path.read_bytes()
    generator = app.load_or_generate_official_store
    monkeypatch.setattr(
        app, "refresh_daily_data_if_due", lambda *a, **kw: DailyRefreshSummary(skipped=True)
    )
    monkeypatch.setattr(
        app,
        "load_or_generate_official_store",
        lambda path, **kw: generator(
            path,
            generator=lambda: (_signal(signal_date=DAY),),
        ),
    )
    monkeypatch.setattr(app, "load_or_generate_price_guidance_store", lambda *a, **kw: None)
    with pytest.raises(ValueError):
        app.refresh_eod_state(
            day=DAY,
            repo_root=tmp_path,
            official_store=signals,
            guidance_store=prices,
            service=service,
        )
    assert old_bytes == (signals.path.read_bytes(), prices.path.read_bytes())
    assert service.snapshot()["official_daily_candidates"][0]["data_cutoff"] == "2026-09-29"


def test_bundle_checksum_failure_is_not_hidden(tmp_path):
    path = tmp_path / "journal.json"
    write_checked_json(path, {"format_version": 1}, maximum_bytes=1024)
    payload = json.loads(path.read_bytes())
    payload["body"]["format_version"] = 2
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        read_checked_json(path, maximum_bytes=1024)


def test_new_holding_backfill_does_not_expand_to_whole_exchange(tmp_path):
    fetched = []

    class BoundedProvider(FakeProvider):
        def list_instruments(self, as_of=None):
            return pd.DataFrame(
                {
                    "symbol": ["000001", "000002", "000003"],
                    "name": ["甲", "乙", "丙"],
                    "as_of": [as_of] * 3,
                    "source": ["baostock"] * 3,
                }
            )

        def get_daily_bars(self, symbol, start_date, end_date):
            fetched.append(symbol)
            return super().get_daily_bars(symbol, start_date, end_date)

        def get_index_daily_bars(self, symbol, start_date, end_date):
            fetched.append(symbol)
            return super().get_index_daily_bars(symbol, start_date, end_date)

    summary = refresh_daily_data_if_due(
        tmp_path,
        end_date=DAY,
        provider=BoundedProvider(),
        required_symbols=("000002",),
        minimum_history_rows=1,
    )
    assert fetched == ["000002", "000300"]
    assert summary.symbols_failed == 0
    assert {row.symbol for row in summary.symbol_results} == {"000002", "000300"}
    assert all(row.status == "FRESH" for row in summary.symbol_results)
    assert not (tmp_path / "lake/daily_bars/000003.parquet").exists()


def test_unavailable_holding_is_reported_without_fake_bars(tmp_path):
    summary = refresh_daily_data_if_due(
        tmp_path,
        end_date=DAY,
        provider=FakeProvider(),
        required_symbols=("000002",),
        minimum_history_rows=1,
    )
    assert summary.symbols_failed == 1
    assert next(row for row in summary.symbol_results if row.symbol == "000002").status == "FAILED"
    assert not (tmp_path / "lake/daily_bars/000002.parquet").exists()


@pytest.mark.parametrize("cancel", [False, True])
def test_daily_worker_deadline_and_shutdown_terminate_the_exact_real_child(
    tmp_path,
    monkeypatch,
    cancel,
):
    original = subprocess.Popen
    children = []
    stop = threading.Event()

    def sleeping_child(command, **kwargs):
        child = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", sleeping_child)
    timer = threading.Timer(0.3, stop.set) if cancel else None
    if timer is not None:
        timer.start()
    try:
        with pytest.raises(RuntimeError if cancel else TimeoutError):
            run_bounded_daily_refresh(tmp_path, DAY, (), stop_event=stop, timeout_seconds=0.7)
    finally:
        if timer is not None:
            timer.cancel()
            timer.join(1)
    assert len(children) == 1
    assert children[0].poll() is not None
    assert not tuple((tmp_path / ".runtime/temp/daily-refresh").glob("fetch-*"))


def test_retry_http_requires_local_header_and_returns_real_pending_state(tmp_path):
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    service = WorkbenchService(provider=Provider(), clock=lambda: NOW)
    coordinator = EODCoordinator(
        refresh=lambda day: EODRefreshResult(False, "FAILED", "失败"),
        clock=lambda: NOW,
        checkpoint_path=tmp_path / "eod.json",
    )
    coordinator.run_initial()
    service.set_eod_retry_request(coordinator.retry_after_review)
    server = app.create_server(service=service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/api/workflow/retry"
    try:
        with pytest.raises(HTTPError) as failed:
            urlopen(Request(url, data=b"{}"), timeout=3)
        assert failed.value.code == 403
        request = Request(url, data=b"{}", headers={"X-Quant-Workbench-Request": "workflow-retry"})
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read())
        assert payload["refresh"]["status"] == "PENDING"
        assert coordinator.snapshot()["attempts"] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
