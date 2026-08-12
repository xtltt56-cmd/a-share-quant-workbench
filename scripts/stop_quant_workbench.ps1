[CmdletBinding()]
param()

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pidPath = Join-Path $repoRoot '.runtime\quant_workbench.pid'
$launchMetadataPath = Join-Path $repoRoot '.runtime\quant_workbench.launch.json'
$launchHelpersPath = Join-Path $PSScriptRoot 'workbench_launch_helpers.ps1'
. $launchHelpersPath
if (-not (Test-Path -LiteralPath $pidPath)) {
    Remove-Item -LiteralPath $launchMetadataPath -Force -ErrorAction SilentlyContinue
    Write-Output 'Quant Workbench is not running.'
    exit 0
}

$pidText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
[int]$quantWorkbenchPid = 0
if ([int]::TryParse($pidText, [ref]$quantWorkbenchPid)) {
    $quantWorkbenchProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$quantWorkbenchPid" -ErrorAction SilentlyContinue
    if ($null -ne $quantWorkbenchProcess) {
        $owned = Test-QuantWorkbenchCommandLine `
            -CommandLine $quantWorkbenchProcess.CommandLine `
            -RepoRoot $repoRoot
        if ($owned) {
            try {
                $safeExitHeaders = @{ 'X-Quant-Workbench-Request' = 'safe-exit' }
                Invoke-WebRequest `
                    -Uri 'http://127.0.0.1:8765/api/system/safe-exit' `
                    -Method Post `
                    -Headers $safeExitHeaders `
                    -UseBasicParsing `
                    -TimeoutSec 3 | Out-Null
            } catch {
                Write-Warning 'Research safe-exit did not respond; continuing with owned-process stop.'
            }
            Stop-Process -Id $quantWorkbenchPid -ErrorAction SilentlyContinue
            Wait-Process -Id $quantWorkbenchPid -Timeout 5 -ErrorAction SilentlyContinue
            Write-Output "Quant Workbench stopped (PID $quantWorkbenchPid)."
        } else {
            Write-Warning "PID $quantWorkbenchPid is not this repository's Quant Workbench; it was not stopped."
        }
    }
}
Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $launchMetadataPath -Force -ErrorAction SilentlyContinue
