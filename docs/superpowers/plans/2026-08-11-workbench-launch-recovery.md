# 工作台启动恢复与收盘状态实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 让桌面快捷方式自动替换离线、旧版本或状态不明的本项目后台进程，并让非交易时段在中文界面明确显示为“市场已收盘”。

**Architecture:** 新增一个无副作用的 PowerShell 启动辅助模块，集中负责进程身份校验、启动元数据解析、代码内容指纹和复用/重启决策；启动与停止脚本只执行该模块给出的决定。实时调度器继续在非开盘时段禁止网络请求，但改用稳定状态码向工作台传递原因，页面只做中文映射，不改变行情质量判定。

**Tech Stack:** Windows PowerShell 5.1、Python 3.12、pytest、标准库 HTTP/JSON、现有纯 HTML/JavaScript 工作台。

---

## 文件结构与职责

- Create: scripts/workbench_launch_helpers.ps1 — 启动元数据、生产源码指纹、进程身份和复用决策。
- Create: tests/test_workbench_launch_helpers.py — 通过真实 Windows PowerShell 验证辅助模块。
- Modify: scripts/start_quant_workbench.ps1 — 根据决策复用或重启，成功就绪后写元数据。
- Modify: scripts/stop_quant_workbench.ps1 — 只停止已验证属于本仓库的工作台进程。
- Modify: tests/test_launcher_security.py — 锁定本地地址、安全身份校验和元数据边界。
- Modify: src/a_share_quant/runtime/scheduler.py — 输出稳定的非开盘状态码。
- Modify: tests/test_realtime_scheduler.py — 验证非开盘状态不请求供应商。
- Modify: src/a_share_quant/workbench/service.py — 传播非开盘原因且不升级数据质量。
- Modify: tests/test_workbench_service.py — 验证收盘、缓存和零调用边界。
- Modify: src/a_share_quant/workbench/app.py — 显示准确的简体中文状态。
- Modify: tests/test_workbench_app.py — 锁定中文页面文字。
- Modify: README.md — 增加启动恢复和收盘状态说明。

### Task 1: 建立可测试的启动决策模块

**Files:**
- Create: scripts/workbench_launch_helpers.ps1
- Create: tests/test_workbench_launch_helpers.py

- [ ] **Step 1: 写入模式、版本、指纹和身份的失败测试**

创建 tests/test_workbench_launch_helpers.py：

~~~python
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "scripts" / "workbench_launch_helpers.ps1"


def _powershell(expression: str) -> str:
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f". '{HELPERS}'; {expression}",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _decision(**overrides: object) -> str:
    values = {
        "metadata_pid": 42,
        "actual_pid": 42,
        "metadata_mode": "network",
        "requested_mode": "network",
        "metadata_revision": "abc",
        "requested_revision": "abc",
        "metadata_fingerprint": "sha256:one",
        "requested_fingerprint": "sha256:one",
    }
    values.update(overrides)
    metadata = {
        "format_version": 1,
        "pid": values["metadata_pid"],
        "mode": values["metadata_mode"],
        "git_revision": values["metadata_revision"],
        "code_fingerprint": values["metadata_fingerprint"],
        "started_at": "2026-08-11T16:00:00+08:00",
    }
    payload = json.dumps(metadata, separators=(",", ":")).replace("'", "''")
    return _powershell(
        f"$metadata = '{payload}' | ConvertFrom-Json; "
        "Get-QuantLaunchDecision "
        f"-Metadata $metadata -ActualPid {values['actual_pid']} "
        f"-RequestedMode '{values['requested_mode']}' "
        f"-RequestedGitRevision '{values['requested_revision']}' "
        f"-RequestedCodeFingerprint '{values['requested_fingerprint']}'"
    )


def test_launch_decision_reuses_only_identical_runtime() -> None:
    assert _decision() == "REUSE"
    assert _decision(requested_mode="offline") == "RESTART_MODE_MISMATCH"
    assert _decision(requested_revision="def") == "RESTART_REVISION_MISMATCH"
    assert _decision(requested_fingerprint="sha256:two") == "RESTART_CODE_MISMATCH"
    assert _decision(actual_pid=43) == "RESTART_PID_MISMATCH"


def test_launch_decision_restarts_without_valid_metadata() -> None:
    assert (
        _powershell(
            "Get-QuantLaunchDecision -Metadata $null -ActualPid 42 "
            "-RequestedMode 'network' -RequestedGitRevision 'abc' "
            "-RequestedCodeFingerprint 'sha256:one'"
        )
        == "RESTART_MISSING_METADATA"
    )


def test_identity_requires_this_repository_workbench() -> None:
    expected = (
        f'"{ROOT / ".venv" / "Scripts" / "python.exe"}" -X utf8 '
        f'"{ROOT / "scripts" / "quant_cli.py"}" workbench --network'
    ).replace("'", "''")
    unrelated = '"C:\\Python312\\python.exe" other.py workbench --network'
    root = str(ROOT).replace("'", "''")
    assert (
        _powershell(
            f"Test-QuantWorkbenchCommandLine -CommandLine '{expected}' "
            f"-RepoRoot '{root}'"
        )
        == "True"
    )
    assert (
        _powershell(
            f"Test-QuantWorkbenchCommandLine -CommandLine '{unrelated}' "
            f"-RepoRoot '{root}'"
        )
        == "False"
    )
~~~

- [ ] **Step 2: 运行测试并确认 RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workbench_launch_helpers.py -q
~~~

Expected: FAIL，错误说明 workbench_launch_helpers.ps1 不存在。

- [ ] **Step 3: 实现最小辅助模块**

创建 scripts/workbench_launch_helpers.ps1：

~~~powershell
Set-StrictMode -Version Latest

function Get-QuantGitRevision {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    try {
        $revision = (& git -C $RepoRoot rev-parse --verify HEAD 2>$null | Select-Object -First 1)
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($revision)) {
            return $revision.Trim()
        }
    } catch {
        # The content fingerprint remains authoritative without Git.
    }
    return 'unknown'
}

function Get-QuantCodeFingerprint {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    $sourceRoots = @((Join-Path $RepoRoot 'src'), (Join-Path $RepoRoot 'scripts'))
    $files = Get-ChildItem -LiteralPath $sourceRoots -Recurse -File -ErrorAction Stop |
        Where-Object { $_.Extension -in @('.py', '.ps1') } |
        Sort-Object FullName
    $lines = foreach ($file in $files) {
        $relative = $file.FullName.Substring($RepoRoot.Length).TrimStart('\', '/')
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        $relative + ':' + $hash
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($lines -join [Environment]::NewLine))
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }) -join ''
        return 'sha256:' + $digest
    } finally {
        $sha.Dispose()
    }
}

function Test-QuantWorkbenchCommandLine {
    param(
        [AllowNull()][string]$CommandLine,
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    $expectedCli = Join-Path $RepoRoot 'scripts\quant_cli.py'
    return (
        $CommandLine.IndexOf($expectedCli, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $CommandLine -match '(?i)(^|\s)workbench(\s|$)'
    )
}

function Read-QuantLaunchMetadata {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try {
        $metadata = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
        if (
            $metadata.format_version -ne 1 -or
            $null -eq $metadata.pid -or
            $metadata.mode -notin @('network', 'offline') -or
            [string]::IsNullOrWhiteSpace([string]$metadata.git_revision) -or
            -not ([string]$metadata.code_fingerprint).StartsWith('sha256:')
        ) {
            return $null
        }
        return $metadata
    } catch {
        return $null
    }
}

function Get-QuantLaunchDecision {
    param(
        [AllowNull()][pscustomobject]$Metadata,
        [Parameter(Mandatory = $true)][int]$ActualPid,
        [Parameter(Mandatory = $true)][ValidateSet('network', 'offline')][string]$RequestedMode,
        [Parameter(Mandatory = $true)][string]$RequestedGitRevision,
        [Parameter(Mandatory = $true)][string]$RequestedCodeFingerprint
    )
    if ($null -eq $Metadata) { return 'RESTART_MISSING_METADATA' }
    if ([int]$Metadata.pid -ne $ActualPid) { return 'RESTART_PID_MISMATCH' }
    if ([string]$Metadata.mode -ne $RequestedMode) { return 'RESTART_MODE_MISMATCH' }
    if ([string]$Metadata.git_revision -ne $RequestedGitRevision) {
        return 'RESTART_REVISION_MISMATCH'
    }
    if ([string]$Metadata.code_fingerprint -ne $RequestedCodeFingerprint) {
        return 'RESTART_CODE_MISMATCH'
    }
    return 'REUSE'
}

function Write-QuantLaunchMetadata {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][ValidateSet('network', 'offline')][string]$Mode,
        [Parameter(Mandatory = $true)][string]$GitRevision,
        [Parameter(Mandatory = $true)][string]$CodeFingerprint
    )
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temporary = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    $payload = [ordered]@{
        format_version = 1
        pid = $ProcessId
        mode = $Mode
        git_revision = $GitRevision
        code_fingerprint = $CodeFingerprint
        started_at = [DateTimeOffset]::Now.ToString('o')
    } | ConvertTo-Json
    try {
        [IO.File]::WriteAllText($temporary, $payload, [Text.UTF8Encoding]::new($false))
        if (Test-Path -LiteralPath $Path) {
            [IO.File]::Replace($temporary, $Path, $null)
        } else {
            [IO.File]::Move($temporary, $Path)
        }
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}
~~~

- [ ] **Step 4: 运行测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workbench_launch_helpers.py -q
~~~

Expected: 3 passed。

- [ ] **Step 5: 增加内容指纹和元数据往返测试**

追加：

~~~python
def test_fingerprint_is_stable_and_metadata_round_trips(tmp_path: Path) -> None:
    root = str(ROOT).replace("'", "''")
    first = _powershell(f"Get-QuantCodeFingerprint -RepoRoot '{root}'")
    second = _powershell(f"Get-QuantCodeFingerprint -RepoRoot '{root}'")
    assert first == second
    assert first.startswith("sha256:")
    assert len(first) == 71

    metadata_path = str(tmp_path / "launch.json").replace("'", "''")
    expression = (
        f"Write-QuantLaunchMetadata -Path '{metadata_path}' -ProcessId 42 "
        "-Mode 'network' -GitRevision 'abc' -CodeFingerprint 'sha256:one'; "
        f"Read-QuantLaunchMetadata -Path '{metadata_path}' | ConvertTo-Json -Compress"
    )
    payload = json.loads(_powershell(expression))
    assert payload["format_version"] == 1
    assert payload["pid"] == 42
    assert payload["mode"] == "network"
    assert payload["git_revision"] == "abc"
    assert payload["code_fingerprint"] == "sha256:one"
~~~

- [ ] **Step 6: 运行并提交**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workbench_launch_helpers.py -q
git add scripts/workbench_launch_helpers.ps1 tests/test_workbench_launch_helpers.py
git commit -m "feat: add workbench launch state guard"
~~~

Expected: 4 passed，提交成功。

### Task 2: 接入启动与停止脚本

**Files:**
- Modify: scripts/start_quant_workbench.ps1
- Modify: scripts/stop_quant_workbench.ps1
- Modify: tests/test_launcher_security.py

- [ ] **Step 1: 写入失败测试**

追加：

~~~python
def test_launcher_validates_runtime_before_reuse_or_stop() -> None:
    launcher = (ROOT / "scripts" / "start_quant_workbench.ps1").read_text(encoding="utf-8")
    stopper = (ROOT / "scripts" / "stop_quant_workbench.ps1").read_text(encoding="utf-8")

    for required in (
        "workbench_launch_helpers.ps1",
        "quant_workbench.launch.json",
        "Test-QuantWorkbenchCommandLine",
        "Get-QuantLaunchDecision",
        "Write-QuantLaunchMetadata",
        "Get-CimInstance Win32_Process",
        "REUSE",
    ):
        assert required in launcher

    for required in (
        "workbench_launch_helpers.ps1",
        "quant_workbench.launch.json",
        "Test-QuantWorkbenchCommandLine",
        "Remove-Item -LiteralPath $launchMetadataPath",
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
~~~

- [ ] **Step 2: 运行并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_launcher_security.py -q
~~~

Expected: FAIL，启动/停止脚本尚未接入辅助模块。

- [ ] **Step 3: 替换启动器旧进程复用逻辑**

在路径初始化后计算：

~~~powershell
$launchMetadataPath = Join-Path $runtimeDir 'quant_workbench.launch.json'
$launchHelpersPath = Join-Path $PSScriptRoot 'workbench_launch_helpers.ps1'
. $launchHelpersPath
$requestedMode = if ($Offline) { 'offline' } else { 'network' }
$requestedGitRevision = Get-QuantGitRevision -RepoRoot $repoRoot
$requestedCodeFingerprint = Get-QuantCodeFingerprint -RepoRoot $repoRoot
~~~

用以下分支替换原有“发现 PID 就复用”：

~~~powershell
if (Test-Path -LiteralPath $pidPath) {
    $oldPidText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    [int]$oldPid = 0
    if ([int]::TryParse($oldPidText, [ref]$oldPid)) {
        $oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid" -ErrorAction SilentlyContinue
        if ($null -ne $oldProcess) {
            $owned = Test-QuantWorkbenchCommandLine -CommandLine $oldProcess.CommandLine -RepoRoot $repoRoot
            if ($owned) {
                $metadata = Read-QuantLaunchMetadata -Path $launchMetadataPath
                $decisionArgs = @{
                    Metadata = $metadata
                    ActualPid = $oldPid
                    RequestedMode = $requestedMode
                    RequestedGitRevision = $requestedGitRevision
                    RequestedCodeFingerprint = $requestedCodeFingerprint
                }
                $decision = Get-QuantLaunchDecision @decisionArgs
                if ($decision -eq 'REUSE') {
                    Write-Output "Quant Workbench is already running (PID $oldPid)."
                    Open-QuantWorkbenchPages -Port $Port
                    exit 0
                }
                Stop-Process -Id $oldPid -ErrorAction Stop
                Wait-Process -Id $oldPid -Timeout 5 -ErrorAction SilentlyContinue
                Write-Output "Restarting Quant Workbench: $decision."
            } else {
                Write-Warning "PID $oldPid is not this repository's Quant Workbench; it was not stopped."
            }
        }
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $launchMetadataPath -Force -ErrorAction SilentlyContinue
}
~~~

健康检查失败时停止本次新进程并清状态；成功后执行：

~~~powershell
$metadataArgs = @{
    Path = $launchMetadataPath
    ProcessId = $workbenchProcess.Id
    Mode = $requestedMode
    GitRevision = $requestedGitRevision
    CodeFingerprint = $requestedCodeFingerprint
}
Write-QuantLaunchMetadata @metadataArgs
~~~

- [ ] **Step 4: 加固停止脚本**

加载辅助模块；取得 CIM 命令行并调用 Test-QuantWorkbenchCommandLine。只有返回 True 才执行 Stop-Process。无论进程是否存在，最后只删除以下两个明确文件：

~~~powershell
Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $launchMetadataPath -Force -ErrorAction SilentlyContinue
~~~

未知进程输出 warning 并保留进程。

- [ ] **Step 5: 运行并提交**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workbench_launch_helpers.py tests/test_launcher_security.py -q
git add scripts/start_quant_workbench.ps1 scripts/stop_quant_workbench.ps1 tests/test_launcher_security.py
git commit -m "fix: restart stale workbench runtimes"
~~~

Expected: 全部 PASS，提交成功。

### Task 3: 区分收盘与网络故障

**Files:**
- Modify: src/a_share_quant/runtime/scheduler.py
- Modify: src/a_share_quant/workbench/service.py
- Modify: tests/test_realtime_scheduler.py
- Modify: tests/test_workbench_service.py

- [ ] **Step 1: 写入调度器失败测试**

复用 tests/test_realtime_scheduler.py 已有 provider/store fixture，分别对 09:10、12:00、15:05、周末 10:00 调用 run_once，断言：

~~~python
expected = (
    (MarketSession.PRE_MARKET, "MARKET_NOT_OPEN"),
    (MarketSession.LUNCH_BREAK, "MARKET_LUNCH_BREAK"),
    (MarketSession.CLOSED, "MARKET_CLOSED"),
    (MarketSession.NON_TRADING, "MARKET_CLOSED"),
)
assert tick.requested is False
assert tick.updated is False
assert tick.skip_reason == expected_reason
assert provider.snapshot_requests == 0
~~~

- [ ] **Step 2: 写入工作台失败测试**

在 tests/test_workbench_service.py 新增 15:05 场景，使用计数包装器证明 provider 调用次数为 0，并断言：

~~~python
assert state["session"] == "CLOSED"
assert state["last_error"] == "MARKET_CLOSED"
assert state["evidence_mode"] == "MARKET_CLOSED"
assert state["data_quality"] == "FAILED"
assert state["continuous_updates"] is False
~~~

将现有非交易日测试改为同样的 MARKET_CLOSED，但 session 保持 NON_TRADING。

- [ ] **Step 3: 运行并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_realtime_scheduler.py tests/test_workbench_service.py -q
~~~

Expected: FAIL，旧实现仍返回 market session is CLOSED 和 OFFLINE。

- [ ] **Step 4: 实现稳定状态码**

在 scheduler.py 中加入：

~~~python
_NON_OPEN_REASON = {
    MarketSession.PRE_MARKET: "MARKET_NOT_OPEN",
    MarketSession.LUNCH_BREAK: "MARKET_LUNCH_BREAK",
    MarketSession.CLOSED: "MARKET_CLOSED",
    MarketSession.NON_TRADING: "MARKET_CLOSED",
}
~~~

非开盘 tick 使用：

~~~python
skip_reason=_NON_OPEN_REASON[session]
~~~

在 service.py 无缓存、未更新分支中使用：

~~~python
self.state.evidence_mode = (
    tick.skip_reason if not tick.requested and tick.skip_reason else "OFFLINE"
)
~~~

保留 FAILED、schema_pass=False、continuous_updates=False。

- [ ] **Step 5: 运行缓存和工作台回归**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_realtime_scheduler.py tests/test_workbench_service.py tests/test_realtime_cache.py -q
~~~

Expected: 全部 PASS；缓存仍只能是 CACHED/STALE。

- [ ] **Step 6: 提交**

~~~powershell
git add src/a_share_quant/runtime/scheduler.py src/a_share_quant/workbench/service.py tests/test_realtime_scheduler.py tests/test_workbench_service.py
git commit -m "fix: distinguish closed market state"
~~~

### Task 4: 中文化状态并更新操作说明

**Files:**
- Modify: src/a_share_quant/workbench/app.py
- Modify: tests/test_workbench_app.py
- Modify: README.md
- Modify: docs/superpowers/specs/2026-08-11-workbench-launch-recovery-design.md
- Modify: docs/superpowers/plans/2026-08-11-workbench-launch-recovery.md

- [ ] **Step 1: 写入页面失败测试**

追加：

~~~python
assert "'MARKET_CLOSED':'市场已收盘'" in html
assert "'MARKET_NOT_OPEN':'尚未开盘'" in html
assert "'MARKET_LUNCH_BREAK':'午间休市'" in html
assert "'CLOSED':'已收盘'" in html
assert "'NON_TRADING':'非交易日'" in html
assert "'LUNCH_BREAK':'午间休市'" in html
assert "'PRE_MARKET':'盘前时段'" in html
assert "'CLOSED':'非交易时段'" not in html
~~~

- [ ] **Step 2: 运行并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workbench_app.py -q
~~~

Expected: FAIL。

- [ ] **Step 3: 修改中文映射**

在 app.py 的 labels 对象中写入：

~~~javascript
'OPEN':'交易时段','CLOSED':'已收盘','NON_TRADING':'非交易日',
'PRE_MARKET':'盘前时段','LUNCH_BREAK':'午间休市',
'MARKET_CLOSED':'市场已收盘','MARKET_NOT_OPEN':'尚未开盘',
'MARKET_LUNCH_BREAK':'午间休市'
~~~

保留 FAILED:'失败'，因为它表示没有可用行情；状态行同时显示“市场已收盘”。

- [ ] **Step 4: 更新 README**

加入：

~~~markdown
### 桌面启动与盘中监控状态

双击桌面“A股量化交易系统”后，启动器会核对后台运行模式和代码指纹：一致时复用，不一致、元数据损坏或代码更新时自动重启。系统始终只打开 127.0.0.1 的两个本地页面。

“市场已收盘”“尚未开盘”“午间休市”和“非交易日”表示调度器按A股时段停止请求，不是网络故障。只有交易时段内连续取得至少两次不同且通过校验的行情快照，数据质量才会显示“良好”；收盘缓存不能作为实时买卖依据。
~~~

- [ ] **Step 5: 扫描占位符与安全词**

~~~powershell
$forbiddenPlaceholders = @('T' + 'BD', 'T' + 'ODO', 'implement' + ' later', 'fill in' + ' details')
Select-String -Path 'docs\superpowers\specs\2026-08-11-workbench-launch-recovery-design.md','docs\superpowers\plans\2026-08-11-workbench-launch-recovery.md' -Pattern $forbiddenPlaceholders
git grep -n -I -E '0\.0\.0\.0|place_order|order_stock|password|account_id' -- scripts/workbench_launch_helpers.ps1 scripts/start_quant_workbench.ps1 scripts/stop_quant_workbench.ps1
~~~

Expected: 无输出。

- [ ] **Step 6: 运行并提交**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workbench_app.py tests/test_workbench_service.py tests/test_launcher_security.py -q
git add src/a_share_quant/workbench/app.py tests/test_workbench_app.py README.md docs/superpowers/specs/2026-08-11-workbench-launch-recovery-design.md docs/superpowers/plans/2026-08-11-workbench-launch-recovery.md
git commit -m "docs: explain workbench launch recovery"
~~~

Expected: 全部 PASS，提交成功。

### Task 5: 完整验证与本机运行验收

**Files:**
- Verify only; 新缺陷必须先增加失败测试再修改生产代码。

- [ ] **Step 1: 聚焦联合回归**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workbench_launch_helpers.py tests/test_launcher_security.py tests/test_realtime_scheduler.py tests/test_workbench_service.py tests/test_realtime_cache.py tests/test_workbench_app.py tests/test_account_import_http.py -q
~~~

Expected: 全部 PASS；只允许 Windows 无法创建真实符号链接时的能力条件 skip。

- [ ] **Step 2: 全量验证**

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
~~~

Expected: pytest 无失败；Ruff 输出 All checks passed!；差异检查无输出。

- [ ] **Step 3: 验证相同模式复用**

~~~powershell
& 'D:\量化交易\scripts\start_quant_workbench.ps1'
$firstProcessId = [int](Get-Content -LiteralPath 'D:\量化交易\.runtime\quant_workbench.pid' -Raw)
& 'D:\量化交易\scripts\start_quant_workbench.ps1'
$secondProcessId = [int](Get-Content -LiteralPath 'D:\量化交易\.runtime\quant_workbench.pid' -Raw)
if ($firstProcessId -ne $secondProcessId) { throw 'matching runtime was not reused' }
~~~

Expected: 第二次输出 already running，PID 相同。

- [ ] **Step 4: 验证模式不一致重启并恢复联网**

~~~powershell
$networkProcessId = [int](Get-Content -LiteralPath 'D:\量化交易\.runtime\quant_workbench.pid' -Raw)
& 'D:\量化交易\scripts\start_quant_workbench.ps1' -Offline
$offlineProcessId = [int](Get-Content -LiteralPath 'D:\量化交易\.runtime\quant_workbench.pid' -Raw)
if ($networkProcessId -eq $offlineProcessId) { throw 'network runtime was reused' }
& 'D:\量化交易\scripts\start_quant_workbench.ps1'
$restoredProcessId = [int](Get-Content -LiteralPath 'D:\量化交易\.runtime\quant_workbench.pid' -Raw)
if ($offlineProcessId -eq $restoredProcessId) { throw 'offline runtime was reused' }
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$restoredProcessId"
if ($process.CommandLine -notmatch '--network' -or $process.CommandLine -match '--offline') {
    throw 'final runtime is not in network mode'
}
~~~

Expected: 两次切换都产生新 PID，最终仅含 --network。

- [ ] **Step 5: 验证页面、导入入口和收盘状态**

~~~powershell
$advisoryHtml = (Invoke-WebRequest -Uri 'http://127.0.0.1:8765/advisory' -UseBasicParsing -TimeoutSec 15).Content
foreach ($text in '券商导出文件导入','扫描导出文件','生成预览','确认导入') {
    if (-not $advisoryHtml.Contains($text)) { throw "missing UI: $text" }
}
$imports = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/advisory/imports' -TimeoutSec 15
if ('table.xls' -notin $imports.files.file_name) { throw 'table.xls is not visible' }
$state = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/state' -TimeoutSec 15
if ($state.session -in @('CLOSED','NON_TRADING') -and $state.last_error -ne 'MARKET_CLOSED') {
    throw 'closed session is mislabeled'
}
if ($state.session -in @('CLOSED','NON_TRADING') -and $state.data_quality -eq 'GOOD') {
    throw 'closed session was upgraded to GOOD'
}
~~~

Expected: 页面为当前版本，文件可扫描，收盘状态明确且不为 GOOD。

- [ ] **Step 6: 记录交易时段验证边界**

若执行时间位于 09:30–11:30 或 13:00–15:00，等待最多 120 秒并轮询 /api/state，要求 active_source 为实际端点、evidence_mode=REAL_MARKET、quotes 非空、日选至少一只有 current_price；continuous_updates=true 前 distinct_update_count 必须至少为 2。

若执行时间位于收盘、非交易日、盘前或午休，只记录“非交易时段安全门通过”，不声称连续盘中行情已验收；下一交易时段执行同一轮询，不降低质量门。

- [ ] **Step 7: 检查提交与工作树**

~~~powershell
git log -6 --oneline
git status --short --branch
~~~

Expected: 启动守卫、启动器集成、收盘状态、中文界面和文档提交均存在；工作树干净。

## 方案审查结论

1. **可行性通过。** 快捷方式固定调用 start_quant_workbench.ps1，无需重建桌面快捷方式。
2. **根因覆盖通过。** 模式、PID、进程身份、Git提交号和生产源码内容指纹共同覆盖“旧离线进程 + 旧页面”。
3. **误杀风险已控制。** 停止前必须确认命令行指向本仓库 scripts\quant_cli.py workbench；PID 被复用时不会终止未知进程。
4. **行情诚实性通过。** 收盘只改变原因码和中文显示，不放宽 FAILED/STALE、时间戳、连续两次更新或断路器门槛。
5. **隐私边界通过。** 元数据不读取 .env、账户导出、账本、令牌或行情。
6. **验证限制明确。** 收盘后只能验证关闭状态和数据源连通性；连续盘中更新必须在真实交易时段完成。
