from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_launcher_scripts_use_local_dashboard_and_no_broker_path() -> None:
    scripts = [
        ROOT / "scripts" / "start_quant_workbench.ps1",
        ROOT / "scripts" / "stop_quant_workbench.ps1",
        ROOT / "scripts" / "create_desktop_shortcut.ps1",
        ROOT / "scripts" / "quant_cli.py",
    ]
    content = "\n".join(path.read_text(encoding="utf-8") for path in scripts)

    assert "127.0.0.1" in content
    assert "0.0.0.0" not in content
    assert "broker" not in content.lower()
    assert "place_order" not in content.lower()
    assert "live_trading_enabled" in content
    assert "[string]$AdvisoryInitialCash = '100000'" in content
    assert "Open-QuantWorkbenchPages -Port $Port" in content


def test_start_launcher_opens_dashboard_and_advisory_for_both_success_paths() -> None:
    launcher = (ROOT / "scripts" / "start_quant_workbench.ps1").read_text(
        encoding="utf-8"
    )

    assert "function Open-QuantWorkbenchPages" in launcher
    assert '$dashboardUrl = "http://127.0.0.1:$Port/"' in launcher
    assert '$advisoryUrl = "http://127.0.0.1:$Port/advisory"' in launcher
    assert "Start-Process $dashboardUrl" in launcher
    assert "Start-Process $advisoryUrl" in launcher
    assert launcher.count("Open-QuantWorkbenchPages -Port $Port") == 2
