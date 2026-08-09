from datetime import datetime, timedelta, timezone
from pathlib import Path

from a_share_quant.contracts.identifiers import AssetType, SecurityIdentifier
from a_share_quant.contracts.realtime import MarketSnapshot, MinuteBar, RealTimeQuote

NOW = datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc)


def _quote(symbol: str, *, last: float) -> RealTimeQuote:
    return RealTimeQuote(
        symbol=symbol,
        market="A",
        timestamp_exchange=NOW,
        timestamp_received=NOW,
        last=last,
        name=f"sample-{symbol}",
        open=last,
        high=last + 0.1,
        low=last - 0.1,
        previous_close=last - 0.05,
        volume=100,
        amount=last * 100,
        change_pct=0.5,
        source="akshare",
    )


def test_offline_or_unavailable_provider_can_never_claim_real_market(tmp_path: Path) -> None:
    from scripts.run_real_market_validation import finalize_validation, write_validation_report

    unavailable = finalize_validation(
        {
            "evidence_mode": "OFFLINE",
            "provider_available": False,
            "gates": {},
            "errors": ["token=super-secret"],
        }
    )
    offline = finalize_validation(
        {
            "evidence_mode": "OFFLINE",
            "provider_available": True,
            "gates": {"dashboard": True},
            "errors": ["password=not-for-report"],
        }
    )
    output = tmp_path / "validation.md"
    write_validation_report(
        {
            **offline,
            "started_at": NOW.isoformat(),
            "completed_at": NOW.isoformat(),
            "market_session": "NON_TRADING",
            "provider": "akshare",
            "source": "AKShare / Eastmoney",
            "capabilities": [],
            "security_review_status": "PASS_CODE_REVIEW_PENDING_REAL_MARKET",
        },
        output,
    )

    text = output.read_text(encoding="utf-8")

    assert unavailable["stage_status"] == "NO_REALTIME_PROVIDER_AVAILABLE"
    assert offline["stage_status"] == "STAGE_3RT_OFFLINE_VALIDATED"
    assert "OFFLINE" in text
    assert "REPLAY" in text
    assert "REAL MARKET" in text
    assert "PASS_CODE_REVIEW_PENDING_REAL_MARKET" in text
    assert "STAGE_3RT_REAL_MARKET_VALIDATED" not in text
    assert "password=not-for-report" not in text
    assert "super-secret" not in text


def test_only_full_real_evidence_with_index_identity_and_all_gates_can_promote() -> None:
    from scripts.run_real_market_validation import (
        REQUIRED_REAL_GATES,
        finalize_validation,
        validate_snapshot,
    )

    snapshot = MarketSnapshot(
        timestamp_exchange=NOW,
        timestamp_received=NOW,
        quotes=(_quote("000001", last=10.0), _quote("000002", last=20.0)),
        source="akshare",
    )
    csi300 = SecurityIdentifier(
        symbol="000300",
        exchange="SSE",
        asset_type=AssetType.INDEX,
    )
    snapshot_result = validate_snapshot(
        snapshot,
        csi300=csi300,
        now=NOW,
        minimum_full_market_quotes=2,
    )
    promoted = finalize_validation(
        {
            "evidence_mode": "REAL_MARKET",
            "provider_available": True,
            "gates": {name: True for name in REQUIRED_REAL_GATES},
            "snapshot": snapshot_result,
        }
    )
    incomplete = finalize_validation(
        {
            "evidence_mode": "REAL_MARKET",
            "provider_available": True,
            "gates": {
                **{name: True for name in REQUIRED_REAL_GATES},
                "eod_finalization": False,
            },
            "snapshot": snapshot_result,
        }
    )

    assert snapshot_result["schema_pass"] is True
    assert snapshot_result["required_symbols_pass"] is True
    assert snapshot_result["full_market_snapshot"] is True
    assert snapshot_result["csi300_index_identity"] is True
    assert promoted["stage_status"] == "STAGE_3RT_REAL_MARKET_VALIDATED"
    assert incomplete["stage_status"] == "STAGE_3RT_OFFLINE_VALIDATED"


def test_capability_discovery_reports_rights_without_exposing_credential_values(
    monkeypatch,
) -> None:
    from scripts.run_real_market_validation import discover_provider_capabilities

    monkeypatch.setenv("TUSHARE_TOKEN", "credential-that-must-not-leak")
    monkeypatch.delenv("RQDATA_USERNAME", raising=False)
    monkeypatch.delenv("RQDATA_PASSWORD", raising=False)
    monkeypatch.delenv("RQDATA_CONFIG_PATH", raising=False)

    records = {
        item["provider"]: item
        for item in discover_provider_capabilities(allow_network=False)
    }

    assert records["tushare"]["configured"] is True
    assert records["tushare"]["status"] == "NETWORK_NOT_REQUESTED"
    assert records["tushare"]["live_minute"] == "NOT_PROBED"
    assert records["rqdata"]["status"] == "NOT_CONFIGURED"
    assert records["rqdata"]["historical_daily"] == "NOT_CONFIGURED"
    assert "credential-that-must-not-leak" not in str(records)


def test_validation_report_includes_only_sanitized_smoke_summary(tmp_path: Path) -> None:
    from scripts.run_real_market_validation import (
        _load_realtime_smoke_evidence,
        finalize_validation,
        write_validation_report,
    )

    smoke_report = tmp_path / "reports" / "realtime_data_smoke_test.md"
    smoke_report.parent.mkdir()
    smoke_report.write_text(
        "- Status: **FAILED**\n"
        "- Active provider: `akshare`\n"
        "- Latency: `123.4` ms\n"
        "- Data quality: `FAILED`\n"
        "- Error: `password=must-not-appear`\n",
        encoding="utf-8",
    )

    evidence = _load_realtime_smoke_evidence(tmp_path)
    payload = finalize_validation(
        {
            "evidence_mode": "OFFLINE",
            "provider_available": True,
            "gates": {},
            "realtime_smoke": evidence,
        }
    )
    output = tmp_path / "validation.md"
    write_validation_report(payload, output)
    text = output.read_text(encoding="utf-8")

    assert evidence == {
        "status": "FAILED",
        "active_provider": "akshare",
        "latency_ms": 123.4,
        "data_quality": "FAILED",
        "error": "SanitizedError",
    }
    assert "Explicit provider smoke attempt" in text
    assert "must-not-appear" not in text


def test_failed_public_smoke_with_no_professional_rights_reports_no_provider(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from scripts.run_real_market_validation import run_validation

    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("RQDATA_USERNAME", raising=False)
    monkeypatch.delenv("RQDATA_PASSWORD", raising=False)
    monkeypatch.delenv("RQDATA_CONFIG_PATH", raising=False)
    smoke_report = tmp_path / "reports" / "realtime_data_smoke_test.md"
    smoke_report.parent.mkdir()
    smoke_report.write_text(
        "- Status: **FAILED**\n"
        "- Active provider: `akshare`\n"
        "- Latency: `n/a` ms\n"
        "- Data quality: `FAILED`\n"
        "- Error: `ProviderRequestError`\n",
        encoding="utf-8",
    )

    result = run_validation(
        allow_network=True,
        repo_root=tmp_path,
        now=datetime(2026, 8, 9, 2, 0, tzinfo=timezone.utc),
    )

    assert result["evidence_mode"] == "OFFLINE"
    assert result["provider_available"] is False
    assert result["stage_status"] == "NO_REALTIME_PROVIDER_AVAILABLE"


def test_intraday_feature_validation_requires_causal_stock_and_csi300_bars() -> None:
    from scripts.run_real_market_validation import validate_intraday_features

    stock_bars = (
        _bar("000001", minute=0, close=10.0),
        _bar("000001", minute=1, close=10.2),
        _bar("000001", minute=2, close=10.3),
        _bar("000002", minute=0, close=20.0),
        _bar("000002", minute=1, close=20.2),
        _bar("000002", minute=2, close=20.3),
    )
    index_bars = (
        _bar("000300", minute=0, close=100.0),
        _bar("000300", minute=1, close=101.0),
        _bar("000300", minute=2, close=101.5),
    )
    quotes = {
        "000001": _quote("000001", last=10.2),
        "000002": _quote("000002", last=20.2),
    }

    result = validate_intraday_features(
        stock_bars=stock_bars,
        index_bars=index_bars,
        quotes_by_symbol=quotes,
        now=NOW + timedelta(minutes=3),
    )

    assert result["status"] == "PASS"
    assert result["symbols"] == ["000001", "000002"]
    assert result["feature_rows"] == 6
    assert result["missing_required_features"] == []


def _bar(symbol: str, *, minute: int, close: float) -> MinuteBar:
    timestamp = NOW.replace(minute=NOW.minute + minute)
    return MinuteBar(
        symbol=symbol,
        timestamp=timestamp,
        frequency="1m",
        open=close - 0.1,
        high=close + 0.1,
        low=close - 0.2,
        close=close,
        volume=100 + minute * 10,
        amount=close * (100 + minute * 10),
        source="akshare",
        is_final=minute == 0,
    )
