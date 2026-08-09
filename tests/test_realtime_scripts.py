from pathlib import Path

from scripts.run_historical_dry_run import choose_production_symbols
from scripts.run_realtime_smoke_test import write_smoke_report


def test_production_symbol_selection_does_not_reuse_fixture_only_symbols() -> None:
    result = choose_production_symbols(
        [
            {"symbol": "000001", "is_st": False, "is_delisting_risk": False, "is_suspended": False},
            {"symbol": "000003", "is_st": True, "is_delisting_risk": False, "is_suspended": False},
            {"symbol": "600000", "is_st": False, "is_delisting_risk": False, "is_suspended": False},
        ],
        fixture_symbols={"000001", "000002", "000003", "000004"},
        limit=10,
    )

    assert result == ("600000",)


def test_smoke_report_writes_sanitized_provider_failure(tmp_path: Path) -> None:
    output = tmp_path / "smoke.md"

    write_smoke_report(
        {
            "status": "FAILED",
            "error": "provider request failed",
            "providers": [{"provider": "tushare", "status": "PERMISSION_DENIED"}],
            "sample_symbols": [],
            "started_at": "2026-08-09T03:00:00+00:00",
            "completed_at": "2026-08-09T03:00:01+00:00",
        },
        output,
    )

    text = output.read_text(encoding="utf-8")
    assert "FAILED" in text
    assert "PERMISSION_DENIED" in text
    assert "TUSHARE_TOKEN" not in text
    assert "password" not in text.lower()
