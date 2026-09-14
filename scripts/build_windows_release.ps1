[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [ValidateSet('Online', 'Portable', 'Both')][string]$Flavor = 'Both',
    [string]$PythonInstallerPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$payloadItems = @(
    '.env.example',
    'LICENSE',
    'NOTICE',
    'README.md',
    'README_RELEASE_ZH.md',
    'THIRD_PARTY_NOTICES.md',
    'VERSION.json',
    'pyproject.toml',
    'config',
    'constraints',
    'scripts',
    'src'
)

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)

    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function New-PayloadStage {
    param([Parameter(Mandatory = $true)][string]$Channel)

    $stage = Join-Path $outputRoot ("stage-{0}-{1}" -f $Channel.ToLowerInvariant(), [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    foreach ($relative in $payloadItems) {
        $source = Join-Path $repoRoot $relative
        if (-not (Test-Path -LiteralPath $source)) {
            throw "Required release item not found: $relative"
        }
        Copy-Item -LiteralPath $source -Destination $stage -Recurse
    }
    Get-ChildItem -LiteralPath $repoRoot -File -Filter '*.cmd' | Copy-Item -Destination $stage
    Get-ChildItem -LiteralPath $stage -Directory -Recurse -Filter '__pycache__' | Sort-Object FullName -Descending | Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $stage -File -Recurse -Include '*.pyc', '*.pyo' | Remove-Item -Force
    $revision = (& git -C $repoRoot rev-parse --verify HEAD 2>$null | Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($revision)) {
        $revision = [string]$env:GITHUB_SHA
    }
    if ([string]::IsNullOrWhiteSpace($revision)) {
        $revision = 'unknown'
    }
    $metadata = [ordered]@{
        version = $Version
        channel = $Channel.ToLowerInvariant()
        product_name = 'A-Share Quant Workbench'
        distribution = 'windows-x64'
        git_commit = $revision.Trim()
        built_at = [DateTimeOffset]::UtcNow.ToString('o')
    }
    [IO.File]::WriteAllText(
        (Join-Path $stage 'VERSION.json'),
        ($metadata | ConvertTo-Json),
        [Text.UTF8Encoding]::new($false)
    )
    return $stage
}

function Compress-Payload {
    param(
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $archive = Join-Path $outputRoot $Name
    if (Test-Path -LiteralPath $archive) {
        Remove-Item -LiteralPath $archive -Force
    }
    Compress-Archive -Path (Join-Path $Stage '*') -DestinationPath $archive -CompressionLevel Optimal
    return $archive
}

$artifacts = @()
if ($Flavor -in @('Online', 'Both')) {
    $onlineStage = New-PayloadStage -Channel 'Online'
    try {
        $artifacts += Compress-Payload -Stage $onlineStage -Name "A-Share-Quant-Workbench-v$Version-Windows-x64-Online.zip"
    } finally {
        if (([IO.Path]::GetFullPath($onlineStage)).StartsWith($outputRoot, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $onlineStage -Recurse -Force
        }
    }
}

if ($Flavor -in @('Portable', 'Both')) {
    $portableStage = New-PayloadStage -Channel 'Portable'
    try {
        $installArguments = @('-NonInteractive', '-AllowSystemDrive', '-SkipShortcut', '-SkipLaunch')
        if (-not [string]::IsNullOrWhiteSpace($PythonInstallerPath)) {
            $installArguments += @('-PythonInstallerPath', $PythonInstallerPath)
        }
        & (Join-Path $portableStage 'scripts\install_release.ps1') @installArguments
        if ($LASTEXITCODE -ne 0) {
            throw 'Portable runtime installation failed.'
        }
        $cache = Join-Path $portableStage '.runtime\install-cache'
        if (Test-Path -LiteralPath $cache) {
            Remove-Item -LiteralPath $cache -Recurse -Force
        }
        $env:PYTHONPATH = Join-Path $portableStage 'src'
        & (Join-Path $portableStage 'runtime\python.exe') -X utf8 -c 'import a_share_quant; from a_share_quant.workbench.app import create_server; print("portable smoke passed")'
        if ($LASTEXITCODE -ne 0) {
            throw 'Portable import smoke test failed.'
        }
        $artifacts += Compress-Payload -Stage $portableStage -Name "A-Share-Quant-Workbench-v$Version-Windows-x64-Portable.zip"
    } finally {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        if (([IO.Path]::GetFullPath($portableStage)).StartsWith($outputRoot, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $portableStage -Recurse -Force
        }
    }
}

$checksumPath = Join-Path $outputRoot 'SHA256SUMS.txt'
$checksumLines = foreach ($artifact in $artifacts) {
    "{0}  {1}" -f (Get-Sha256Hex -Path $artifact), (Split-Path $artifact -Leaf)
}
[IO.File]::WriteAllLines($checksumPath, $checksumLines, [Text.UTF8Encoding]::new($false))
$artifacts | ForEach-Object { Get-Item -LiteralPath $_ }
Get-Item -LiteralPath $checksumPath
