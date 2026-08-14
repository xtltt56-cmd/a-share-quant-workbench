import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANAGED_ENVIRONMENT = {
    "TEMP": ".runtime/tmp",
    "TMP": ".runtime/tmp",
    "PIP_CACHE_DIR": ".runtime/cache/pip",
    "JOBLIB_TEMP_FOLDER": ".runtime/cache/joblib",
    "XDG_CACHE_HOME": ".runtime/cache/xdg",
    "MPLCONFIGDIR": ".runtime/cache/matplotlib",
}


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
    launch_call_index = launcher.index("$workbenchProcess = Start-QuantWorkbenchProcess")

    assert "$runtimeTempDir = Join-Path $runtimeDir 'tmp'" in launcher
    assert "$runtimeCacheDir = Join-Path $runtimeDir 'cache'" in launcher
    expected_directories = (
        "$pipCacheDir = Join-Path $runtimeCacheDir 'pip'",
        "$joblibTempDir = Join-Path $runtimeCacheDir 'joblib'",
        "$xdgCacheDir = Join-Path $runtimeCacheDir 'xdg'",
        "$matplotlibConfigDir = Join-Path $runtimeCacheDir 'matplotlib'",
    )
    for assignment in expected_directories:
        assert assignment in launcher
        assert launcher.index(assignment) < launch_call_index

    assert "function Start-QuantWorkbenchProcess" in launcher
    assert 'Set-Item -LiteralPath "Env:$name"' in launcher
    assert 'Remove-Item -LiteralPath "Env:$name"' in launcher
    assert "finally {" in launcher
    assert "$env:" not in launcher.lower()

    for forbidden in (
        "setx ",
        "setenvironmentvariable",
        "registry::",
        "$env:home",
        "$env:codex_home",
    ):
        assert forbidden not in lower


@pytest.mark.skipif(os.name != "nt", reason="PowerShell process-scope test")
@pytest.mark.parametrize(
    ("mode", "expected_invoked", "expected_threw"),
    [
        ("reuse_or_early", False, False),
        ("success", True, False),
        ("failure", True, True),
    ],
)
def test_launcher_managed_environment_is_scoped_to_start_process(
    mode: str,
    expected_invoked: bool,
    expected_threw: bool,
) -> None:
    payload = _run_start_process_scope_probe(mode)

    assert payload["Invoked"] is expected_invoked
    assert payload["Threw"] is expected_threw
    original_temp = str(Path(payload["RepoRoot"]) / ".runtime" / "original-temp")
    original_tmp = str(Path(payload["RepoRoot"]) / ".runtime" / "original-tmp")
    assert payload["After"] == {
        "TEMP": original_temp,
        "TMP": original_tmp,
        "PIP_CACHE_DIR": None,
        "JOBLIB_TEMP_FOLDER": None,
        "XDG_CACHE_HOME": None,
        "MPLCONFIGDIR": None,
        "HOME": "preserved-home",
        "CODEX_HOME": "preserved-codex-home",
    }
    if expected_invoked:
        inherited_root = Path(payload["RepoRoot"])
        for name, relative in MANAGED_ENVIRONMENT.items():
            assert Path(payload["Observed"][name]) == inherited_root / relative
        assert payload["Observed"]["HOME"] == "preserved-home"
        assert payload["Observed"]["CODEX_HOME"] == "preserved-codex-home"
    else:
        assert payload["Observed"] is None


def _run_start_process_scope_probe(mode: str) -> dict[str, object]:
    launcher_literal = str(ROOT / "scripts" / "start_quant_workbench.ps1").replace(
        "'", "''"
    )
    mode_literal = mode.replace("'", "''")
    script = r"""
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    '__LAUNCHER__',
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) { throw 'launcher parse failed' }
$functionAst = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Start-QuantWorkbenchProcess'
}, $true)
if ($null -eq $functionAst) { throw 'scoped launcher function missing' }
Invoke-Expression $functionAst.Extent.Text

$repoRoot = (Resolve-Path '__ROOT__').Path
$runtimeDir = Join-Path $repoRoot '.runtime'
$managed = [ordered]@{
    TEMP = Join-Path $runtimeDir 'tmp'
    TMP = Join-Path $runtimeDir 'tmp'
    PIP_CACHE_DIR = Join-Path $runtimeDir 'cache\pip'
    JOBLIB_TEMP_FOLDER = Join-Path $runtimeDir 'cache\joblib'
    XDG_CACHE_HOME = Join-Path $runtimeDir 'cache\xdg'
    MPLCONFIGDIR = Join-Path $runtimeDir 'cache\matplotlib'
}
$names = @(
    'TEMP', 'TMP', 'PIP_CACHE_DIR', 'JOBLIB_TEMP_FOLDER',
    'XDG_CACHE_HOME', 'MPLCONFIGDIR', 'HOME', 'CODEX_HOME'
)
Set-Item -LiteralPath 'Env:TEMP' -Value (Join-Path $runtimeDir 'original-temp')
Set-Item -LiteralPath 'Env:TMP' -Value (Join-Path $runtimeDir 'original-tmp')
foreach ($name in @('PIP_CACHE_DIR', 'JOBLIB_TEMP_FOLDER', 'XDG_CACHE_HOME', 'MPLCONFIGDIR')) {
    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
}
Set-Item -LiteralPath 'Env:HOME' -Value 'preserved-home'
Set-Item -LiteralPath 'Env:CODEX_HOME' -Value 'preserved-codex-home'

function Get-ProbeEnvironment {
    $snapshot = [ordered]@{}
    foreach ($name in $names) {
        $entry = Get-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        $snapshot[$name] = if ($null -eq $entry) { $null } else { $entry.Value }
    }
    return $snapshot
}

$script:Invoked = $false
$script:Observed = $null
$script:FailLaunch = '__MODE__' -eq 'failure'
function Start-Process {
    param(
        [string]$FilePath,
        [object[]]$ArgumentList,
        [string]$WorkingDirectory,
        [string]$WindowStyle,
        [string]$RedirectStandardOutput,
        [string]$RedirectStandardError,
        [switch]$PassThru
    )
    $script:Invoked = $true
    $script:Observed = Get-ProbeEnvironment
    if ($script:FailLaunch) { throw 'simulated Start-Process failure' }
    [pscustomobject]@{ Id = 12345 }
}

$threw = $false
if ('__MODE__' -ne 'reuse_or_early') {
    try {
        $null = Start-QuantWorkbenchProcess `
            -FilePath 'python.exe' `
            -ArgumentList @('-V') `
            -WorkingDirectory $repoRoot `
            -StandardOutputPath (Join-Path $runtimeDir 'probe.out') `
            -StandardErrorPath (Join-Path $runtimeDir 'probe.err') `
            -Environment $managed
    } catch {
        $threw = $true
    }
}
[ordered]@{
    RepoRoot = $repoRoot
    Invoked = $script:Invoked
    Threw = $threw
    Observed = $script:Observed
    After = Get-ProbeEnvironment
} | ConvertTo-Json -Depth 5 -Compress
"""
    script = script.replace("__LAUNCHER__", launcher_literal)
    script = script.replace("__ROOT__", str(ROOT).replace("'", "''"))
    script = script.replace("__MODE__", mode_literal)
    base = dict(os.environ)
    assert Path(base["TEMP"]).drive.casefold() == "d:"
    assert Path(base["TMP"]).drive.casefold() == "d:"
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=True,
        env=base,
    )
    return json.loads(result.stdout.strip())
