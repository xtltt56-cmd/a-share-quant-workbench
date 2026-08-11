[CmdletBinding()]
param(
    [int]$Port = 8765,
    [switch]$Offline,
    [string]$AdvisoryInitialCash = '100000',
    [string]$AdvisoryLedger = '',
    [string]$AdvisoryContext = '',
    [string]$AdvisoryInstrumentMap = '',
    [string]$OfficialSignalPath = '',
    [string]$AccountImportDirectory = ''
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
$runtimeDir = Join-Path $repoRoot '.runtime'
$pidPath = Join-Path $runtimeDir 'quant_workbench.pid'
$stdoutPath = Join-Path $repoRoot 'logs\quant_workbench.stdout.log'
$stderrPath = Join-Path $repoRoot 'logs\quant_workbench.stderr.log'

function Open-QuantWorkbenchPages {
    param([int]$Port)

    $dashboardUrl = "http://127.0.0.1:$Port/"
    $advisoryUrl = "http://127.0.0.1:$Port/advisory"
    Start-Process $dashboardUrl
    Start-Process $advisoryUrl
}

if ([string]::IsNullOrWhiteSpace($AdvisoryLedger)) {
    $AdvisoryLedger = Join-Path $runtimeDir 'advisory\account-ledger.jsonl'
}
if ([string]::IsNullOrWhiteSpace($OfficialSignalPath)) {
    $OfficialSignalPath = Join-Path $runtimeDir 'signals\official-daily.json'
}
if ([string]::IsNullOrWhiteSpace($AccountImportDirectory)) {
    # 默认收件箱：.runtime\advisory\import-inbox
    $AccountImportDirectory = Join-Path $runtimeDir 'advisory\import-inbox'
}
$AccountSnapshotPath = Join-Path $runtimeDir 'advisory\imported-account-snapshot.json'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python environment not found: $pythonPath"
}
New-Item -ItemType Directory -Force -Path $runtimeDir, (Split-Path $stdoutPath), $AccountImportDirectory | Out-Null

if (Test-Path -LiteralPath $pidPath) {
    $oldPidText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    [int]$oldPid = 0
    if ([int]::TryParse($oldPidText, [ref]$oldPid)) {
        $oldProcess = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($null -ne $oldProcess) {
            Write-Output "Quant Workbench is already running (PID $oldPid)."
            Open-QuantWorkbenchPages -Port $Port
            exit 0
        }
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}

$cliPath = Join-Path $repoRoot 'scripts\quant_cli.py'
$arguments = @('-X', 'utf8', $cliPath, 'workbench', '--port', $Port, '--advisory-ledger', $AdvisoryLedger, '--advisory-initial-cash', $AdvisoryInitialCash, '--official-signal-path', $OfficialSignalPath, '--account-import-dir', $AccountImportDirectory, '--account-snapshot-path', $AccountSnapshotPath)
if (-not [string]::IsNullOrWhiteSpace($AdvisoryContext)) {
    $arguments += @('--advisory-context', $AdvisoryContext)
}
if (-not [string]::IsNullOrWhiteSpace($AdvisoryInstrumentMap)) {
    $arguments += @('--advisory-instrument-map', $AdvisoryInstrumentMap)
}
$arguments += '--network'
if ($Offline) {
    $arguments = @('-X', 'utf8', $cliPath, 'workbench', '--port', $Port, '--advisory-ledger', $AdvisoryLedger, '--advisory-initial-cash', $AdvisoryInitialCash, '--official-signal-path', $OfficialSignalPath, '--account-import-dir', $AccountImportDirectory, '--account-snapshot-path', $AccountSnapshotPath)
    if (-not [string]::IsNullOrWhiteSpace($AdvisoryContext)) {
        $arguments += @('--advisory-context', $AdvisoryContext)
    }
    if (-not [string]::IsNullOrWhiteSpace($AdvisoryInstrumentMap)) {
        $arguments += @('--advisory-instrument-map', $AdvisoryInstrumentMap)
    }
    $arguments += '--offline'
}
$workbenchProcess = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
Set-Content -LiteralPath $pidPath -Value $workbenchProcess.Id -Encoding ascii

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        # The process is still starting; the bounded loop is the retry policy.
    }
}
if (-not $ready) {
    Write-Error "Quant Workbench did not become ready. See $stderrPath"
    exit 1
}
Write-Output "Quant Workbench started at http://127.0.0.1:$Port/ (PID $($workbenchProcess.Id))."
Open-QuantWorkbenchPages -Port $Port
