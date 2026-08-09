[CmdletBinding()]
param()

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pidPath = Join-Path $repoRoot '.runtime\quant_workbench.pid'
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Output 'Quant Workbench is not running.'
    exit 0
}

$pidText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
[int]$quantWorkbenchPid = 0
if ([int]::TryParse($pidText, [ref]$quantWorkbenchPid)) {
    $quantWorkbenchProcess = Get-Process -Id $quantWorkbenchPid -ErrorAction SilentlyContinue
    if ($null -ne $quantWorkbenchProcess) {
        Stop-Process -Id $quantWorkbenchPid -ErrorAction SilentlyContinue
        Write-Output "Quant Workbench stopped (PID $quantWorkbenchPid)."
    }
}
Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
