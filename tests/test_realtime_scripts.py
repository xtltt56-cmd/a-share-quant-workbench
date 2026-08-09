import subprocess
import sys
from datetime import date
from pathlib import Path

from scripts.run_historical_dry_run import choose_production_symbols
from scripts.run_realtime_smoke_test import write_smoke_report


def test_production_symbol_selection_does_not_reuse_fixture_only_symbols() -> None:
    result = choose_production_symbols(
        [
            {
                "symbol": "000001",
                "listed_date": "2020-01-01",
                "is_st": False,
                "is_delisting_risk": False,
                "is_suspended": False,
            },
            {
                "symbol": "000003",
                "listed_date": "2020-01-01",
                "is_st": True,
                "is_delisting_risk": False,
                "is_suspended": False,
            },
            {
                "symbol": "600000",
                "listed_date": "2020-01-01",
                "is_st": False,
                "is_delisting_risk": False,
                "is_suspended": False,
            },
        ],
        fixture_symbols={"000001", "000002", "000003", "000004"},
        limit=10,
    )

    assert result == ("600000",)


def test_production_symbol_selection_requires_old_tradable_equities_and_excludes_csi300() -> None:
    from scripts.run_historical_dry_run import MIN_REAL_TRADABLE_SYMBOLS

    eligible = [
        {
            "symbol": f"6000{index:02d}",
            "listed_date": "2020-01-01",
            "is_st": False,
            "is_delisting_risk": False,
            "is_suspended": False,
        }
        for index in range(MIN_REAL_TRADABLE_SYMBOLS)
    ]
    result = choose_production_symbols(
        [
            *eligible,
            {
                "symbol": "000300",
                "listed_date": "2020-01-01",
                "is_st": False,
                "is_delisting_risk": False,
                "is_suspended": False,
            },
            {
                "symbol": "600099",
                "listed_date": "2026-08-01",
                "is_st": False,
                "is_delisting_risk": False,
                "is_suspended": False,
            },
        ],
        fixture_symbols=set(),
        limit=20,
        as_of=date(2026, 8, 10),
    )

    assert len(result) == MIN_REAL_TRADABLE_SYMBOLS
    assert "000300" not in result
    assert "600099" not in result


def test_smoke_report_writes_sanitized_provider_failure(tmp_path: Path) -> None:
    output = tmp_path / "smoke.md"

    write_smoke_report(
        {
            "status": "FAILED",
            "error": "password=must-not-appear",
            "providers": [
                {
                    "provider": "tushare",
                    "status": "PERMISSION_DENIED",
                    "permissions": ["snapshot", "1m"],
                }
            ],
            "sample_symbols": [],
            "started_at": "2026-08-09T03:00:00+00:00",
            "completed_at": "2026-08-09T03:00:01+00:00",
        },
        output,
    )

    text = output.read_text(encoding="utf-8")
    assert "FAILED" in text
    assert "PERMISSION_DENIED" in text
    assert "snapshot, 1m" in text
    assert "TUSHARE_TOKEN" not in text
    assert "password" not in text.lower()
    assert "must-not-appear" not in text


def test_network_diagnostics_wrapper_requires_no_network_by_default(tmp_path: Path) -> None:
    from scripts.run_network_diagnostics import run_diagnostics

    output = tmp_path / "network.md"
    report = run_diagnostics(
        allow_network=False,
        repo_root=tmp_path,
        output=output,
    )

    text = output.read_text(encoding="utf-8")
    assert report.assessment == "NETWORK_PROBES_NOT_REQUESTED"
    assert "NOT_REQUESTED" in text
    assert "password=" not in text.lower()


def test_realtime_smoke_script_supports_direct_python_execution() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/run_realtime_smoke_test.py", "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "--network" in result.stdout
