[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskRepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'workbench_launch_helpers.ps1')
$taskPython = Get-QuantPythonPath -RepoRoot $taskRepoRoot
$taskPrevious = @{}
$taskVariables = @{
    PIP_CACHE_DIR = Join-Path $taskRepoRoot '.runtime\cache\pip'
    TEMP = Join-Path $taskRepoRoot '.runtime\tmp'
    TMP = Join-Path $taskRepoRoot '.runtime\tmp'
}
try {
    foreach ($taskName in $taskVariables.Keys) {
        $taskEntry = Get-Item -LiteralPath "Env:$taskName" -ErrorAction SilentlyContinue
        $taskPrevious[$taskName] = if ($taskEntry) { $taskEntry.Value } else { $null }
        New-Item -ItemType Directory -Force -Path $taskVariables[$taskName] | Out-Null
        Set-Item -LiteralPath "Env:$taskName" -Value $taskVariables[$taskName]
    }
    & $taskPython -m pip install --disable-pip-version-check --no-input `
        --requirement (Join-Path $taskRepoRoot 'constraints\agent-py312.txt') `
        --constraint (Join-Path $taskRepoRoot 'constraints\release-py312.txt')
    if ($LASTEXITCODE -ne 0) { throw '可选 Agent SDK 安装失败；标准工作流仍可使用。' }
    & $taskPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw '依赖兼容性检查未通过。' }
    Write-Host '可选 SDK 已安装到项目运行时。未下载模型、未调用云端 API、未创建自启动。'
} finally {
    foreach ($taskName in $taskPrevious.Keys) {
        if ($null -eq $taskPrevious[$taskName]) {
            Remove-Item -LiteralPath "Env:$taskName" -ErrorAction SilentlyContinue
        } else {
            Set-Item -LiteralPath "Env:$taskName" -Value $taskPrevious[$taskName]
        }
    }
}
