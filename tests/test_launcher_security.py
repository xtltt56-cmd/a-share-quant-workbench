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
