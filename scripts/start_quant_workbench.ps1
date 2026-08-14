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
$runtimeTempDir = Join-Path $runtimeDir 'tmp'
$runtimeCacheDir = Join-Path $runtimeDir 'cache'
$pipCacheDir = Join-Path $runtimeCacheDir 'pip'
$joblibTempDir = Join-Path $runtimeCacheDir 'joblib'
$xdgCacheDir = Join-Path $runtimeCacheDir 'xdg'
$matplotlibConfigDir = Join-Path $runtimeCacheDir 'matplotlib'
$pidPath = Join-Path $runtimeDir 'quant_workbench.pid'
$launchMetadataPath = Join-Path $runtimeDir 'quant_workbench.launch.json'
$launchHelpersPath = Join-Path $PSScriptRoot 'workbench_launch_helpers.ps1'
$stdoutPath = Join-Path $repoRoot 'logs\quant_workbench.stdout.log'
$stderrPath = Join-Path $repoRoot 'logs\quant_workbench.stderr.log'

. $launchHelpersPath
$requestedMode = if ($Offline) { 'offline' } else { 'network' }
$requestedGitRevision = Get-QuantGitRevision -RepoRoot $repoRoot
$requestedCodeFingerprint = Get-QuantCodeFingerprint -RepoRoot $repoRoot

function Open-QuantWorkbenchPages {
    param([int]$Port)

    $dashboardUrl = "http://127.0.0.1:$Port/"
    $advisoryUrl = "http://127.0.0.1:$Port/advisory"
    Start-Process $dashboardUrl
    Start-Process $advisoryUrl
}

function Start-QuantWorkbenchProcess {
    [CmdletBinding()]
    param(
        [string]$FilePath,
        [object[]]$ArgumentList,
        [string]$WorkingDirectory,
        [string]$StandardOutputPath,
        [string]$StandardErrorPath,
        [System.Collections.IDictionary]$Environment
    )

    $previousEnvironment = @{}
    foreach ($name in $Environment.Keys) {
        $entry = Get-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        $previousEnvironment[$name] = @{
            Exists = $null -ne $entry
            Value = if ($null -eq $entry) { $null } else { $entry.Value }
        }
    }
    try {
        foreach ($name in $Environment.Keys) {
            Set-Item -LiteralPath "Env:$name" -Value $Environment[$name]
        }
        Start-Process `
            -FilePath $FilePath `
            -ArgumentList $ArgumentList `
            -WorkingDirectory $WorkingDirectory `
            -WindowStyle Hidden `
            -RedirectStandardOutput $StandardOutputPath `
            -RedirectStandardError $StandardErrorPath `
            -PassThru
    } finally {
        foreach ($name in $Environment.Keys) {
            if ($previousEnvironment[$name].Exists) {
                Set-Item -LiteralPath "Env:$name" -Value $previousEnvironment[$name].Value
            } else {
                Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
            }
        }
    }
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
$ResearchCheckpointPath = Join-Path $runtimeDir 'research\research-checkpoint.json'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python environment not found: $pythonPath"
}
New-Item -ItemType Directory -Force -Path $runtimeDir, $runtimeTempDir, $pipCacheDir, $joblibTempDir, $xdgCacheDir, $matplotlibConfigDir, (Split-Path $stdoutPath), $AccountImportDirectory, (Split-Path $ResearchCheckpointPath) | Out-Null

if (Test-Path -LiteralPath $pidPath) {
    $oldPidText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    [int]$oldPid = 0
    if ([int]::TryParse($oldPidText, [ref]$oldPid)) {
        $oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid" -ErrorAction SilentlyContinue
        if ($null -ne $oldProcess) {
            $owned = Test-QuantWorkbenchCommandLine `
                -CommandLine $oldProcess.CommandLine `
                -RepoRoot $repoRoot
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

$cliPath = Join-Path $repoRoot 'scripts\quant_cli.py'
$arguments = @('-X', 'utf8', $cliPath, 'workbench', '--port', $Port, '--advisory-ledger', $AdvisoryLedger, '--advisory-initial-cash', $AdvisoryInitialCash, '--official-signal-path', $OfficialSignalPath, '--account-import-dir', $AccountImportDirectory, '--account-snapshot-path', $AccountSnapshotPath, '--research-checkpoint', $ResearchCheckpointPath)
if (-not [string]::IsNullOrWhiteSpace($AdvisoryContext)) {
    $arguments += @('--advisory-context', $AdvisoryContext)
}
if (-not [string]::IsNullOrWhiteSpace($AdvisoryInstrumentMap)) {
    $arguments += @('--advisory-instrument-map', $AdvisoryInstrumentMap)
}
$arguments += '--network'
if ($Offline) {
    $arguments = @('-X', 'utf8', $cliPath, 'workbench', '--port', $Port, '--advisory-ledger', $AdvisoryLedger, '--advisory-initial-cash', $AdvisoryInitialCash, '--official-signal-path', $OfficialSignalPath, '--account-import-dir', $AccountImportDirectory, '--account-snapshot-path', $AccountSnapshotPath, '--research-checkpoint', $ResearchCheckpointPath)
    if (-not [string]::IsNullOrWhiteSpace($AdvisoryContext)) {
        $arguments += @('--advisory-context', $AdvisoryContext)
    }
    if (-not [string]::IsNullOrWhiteSpace($AdvisoryInstrumentMap)) {
        $arguments += @('--advisory-instrument-map', $AdvisoryInstrumentMap)
    }
    $arguments += '--offline'
}
$workbenchEnvironment = [ordered]@{
    TEMP = $runtimeTempDir
    TMP = $runtimeTempDir
    PIP_CACHE_DIR = $pipCacheDir
    JOBLIB_TEMP_FOLDER = $joblibTempDir
    XDG_CACHE_HOME = $xdgCacheDir
    MPLCONFIGDIR = $matplotlibConfigDir
}
$workbenchProcess = Start-QuantWorkbenchProcess `
    -FilePath $pythonPath `
    -ArgumentList $arguments `
    -WorkingDirectory $repoRoot `
    -StandardOutputPath $stdoutPath `
    -StandardErrorPath $stderrPath `
    -Environment $workbenchEnvironment
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
    Stop-Process -Id $workbenchProcess.Id -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $launchMetadataPath -Force -ErrorAction SilentlyContinue
    Write-Error "Quant Workbench did not become ready. See $stderrPath"
    exit 1
}
$metadataArgs = @{
    Path = $launchMetadataPath
    ProcessId = $workbenchProcess.Id
    Mode = $requestedMode
    GitRevision = $requestedGitRevision
    CodeFingerprint = $requestedCodeFingerprint
}
Write-QuantLaunchMetadata @metadataArgs
Write-Output "Quant Workbench started at http://127.0.0.1:$Port/ (PID $($workbenchProcess.Id))."
Open-QuantWorkbenchPages -Port $Port
