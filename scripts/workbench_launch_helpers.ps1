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

    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $false
    }
    $expectedCli = Join-Path $RepoRoot 'scripts\quant_cli.py'
    return (
        $CommandLine.IndexOf($expectedCli, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $CommandLine -match '(?i)(^|\s)workbench(\s|$)'
    )
}

function Read-QuantLaunchMetadata {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
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

    if ($null -eq $Metadata) {
        return 'RESTART_MISSING_METADATA'
    }
    if ([int]$Metadata.pid -ne $ActualPid) {
        return 'RESTART_PID_MISMATCH'
    }
    if ([string]$Metadata.mode -ne $RequestedMode) {
        return 'RESTART_MODE_MISMATCH'
    }
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
        [IO.File]::WriteAllText(
            $temporary,
            $payload,
            [Text.UTF8Encoding]::new($false)
        )
        if (Test-Path -LiteralPath $Path) {
            [IO.File]::Replace($temporary, $Path, $null)
        } else {
            [IO.File]::Move($temporary, $Path)
        }
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}
