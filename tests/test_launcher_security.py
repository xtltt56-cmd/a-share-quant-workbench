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
    assert "--account-import-dir" in content
    assert "--account-snapshot-path" in content
    assert "safe-exit" in content


def test_start_launcher_opens_dashboard_and_advisory_for_both_success_paths() -> None:
    launcher = (ROOT / "scripts" / "start_quant_workbench.ps1").read_text(
        encoding="utf-8"
    )

    assert "function Open-QuantWorkbenchPages" in launcher
    assert '$dashboardUrl = "http://127.0.0.1:$Port/"' in launcher
    assert '$advisoryUrl = "http://127.0.0.1:$Port/advisory"' in launcher
    assert "Start-Process $dashboardUrl" in launcher
    assert "Start-Process $advisoryUrl" in launcher
    assert "api/system/safe-exit" not in launcher  # stop script owns the guarded shutdown request
    assert launcher.count("Open-QuantWorkbenchPages -Port $Port") == 2
    assert ".runtime\\advisory\\import-inbox" in launcher


def test_launcher_validates_runtime_before_reuse_or_stop() -> None:
    launcher = (ROOT / "scripts" / "start_quant_workbench.ps1").read_text(
        encoding="utf-8"
    )
    stopper = (ROOT / "scripts" / "stop_quant_workbench.ps1").read_text(
        encoding="utf-8"
    )

    for required in (
        "workbench_launch_helpers.ps1",
        "quant_workbench.launch.json",
        "Test-QuantWorkbenchCommandLine",
        "Get-QuantLaunchDecision",
        "Write-QuantLaunchMetadata",
        "Get-CimInstance Win32_Process",
        "REUSE",
        "research-checkpoint",
        "research\\research-checkpoint.json",
    ):
        assert required in launcher

    for required in (
        "workbench_launch_helpers.ps1",
        "quant_workbench.launch.json",
        "Test-QuantWorkbenchCommandLine",
        "Remove-Item -LiteralPath $launchMetadataPath",
        "api/system/safe-exit",
    ):
        assert required in stopper


def test_launch_metadata_code_has_no_account_or_secret_fields() -> None:
    helper = (ROOT / "scripts" / "workbench_launch_helpers.ps1").read_text(
        encoding="utf-8"
    ).lower()
    for forbidden in (
        "password",
        "token",
        "account_id",
        "shareholder",
        ".env",
        "import-inbox",
        "account-ledger",
    ):
        assert forbidden not in helper


def test_start_launcher_uses_only_process_scoped_project_runtime_environment() -> None:
    launcher = (ROOT / "scripts" / "start_quant_workbench.ps1").read_text(
        encoding="utf-8"
    )
    lower = launcher.lower()
    start_process_index = launcher.index("$workbenchProcess = Start-Process")

    assert "$runtimeTempDir = Join-Path $runtimeDir 'tmp'" in launcher
    assert "$runtimeCacheDir = Join-Path $runtimeDir 'cache'" in launcher
    expected_assignments = (
        "$env:TEMP = $runtimeTempDir",
        "$env:TMP = $runtimeTempDir",
        "$env:PIP_CACHE_DIR = Join-Path $runtimeCacheDir 'pip'",
        "$env:JOBLIB_TEMP_FOLDER = Join-Path $runtimeCacheDir 'joblib'",
        "$env:XDG_CACHE_HOME = Join-Path $runtimeCacheDir 'xdg'",
        "$env:MPLCONFIGDIR = Join-Path $runtimeCacheDir 'matplotlib'",
    )
    for assignment in expected_assignments:
        assert assignment in launcher
        assert launcher.index(assignment) < start_process_index

    for forbidden in (
        "setx ",
        "setenvironmentvariable",
        "registry::",
        "$env:home",
        "$env:codex_home",
    ):
        assert forbidden not in lower
