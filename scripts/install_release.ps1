[CmdletBinding()]
param(
    [switch]$NonInteractive,
    [switch]$AllowSystemDrive,
    [switch]$SkipShortcut,
    [switch]$SkipLaunch,
    [string]$PythonPackagePath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeRoot = Join-Path $repoRoot 'runtime'
$runtimePython = Join-Path $runtimeRoot 'python.exe'
$runtimeState = Join-Path $repoRoot '.runtime\install-state.json'
$requirements = Join-Path $repoRoot 'constraints\release-py312.txt'
$pythonVersion = '3.12.10'
$pythonPackageName = "python.$pythonVersion.nupkg"
$pythonPackageUrl = "https://api.nuget.org/v3-flatcontainer/python/$pythonVersion/$pythonPackageName"
$pythonPackageSha256 = '0EB85C2DFCCCCF1B17352DE4C397F69194035B7D37149EACC16F1147D93DE3B8'

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)

    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '')
    } finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Confirm-SystemDriveInstall {
    $rootDrive = [IO.Path]::GetPathRoot($repoRoot).TrimEnd('\')
    $systemDrive = [string]$env:SystemDrive
    if ($AllowSystemDrive -or $rootDrive -ine $systemDrive) {
        return
    }
    $message = 'The application is on the system drive. Market data, models, and caches stay under this folder and may consume substantial space. A data drive is recommended.'
    if ($NonInteractive) {
        throw "$message Re-run with -AllowSystemDrive to confirm this location."
    }
    Write-Warning $message
    $answer = Read-Host 'Continue on the system drive? Type YES'
    if ($answer -cne 'YES') {
        throw 'Installation cancelled without changing system configuration.'
    }
}

function Get-VerifiedPythonPackage {
    if ([string]::IsNullOrWhiteSpace($PythonPackagePath)) {
        $cache = Join-Path $repoRoot '.runtime\install-cache'
        New-Item -ItemType Directory -Path $cache -Force | Out-Null
        $candidate = Join-Path $cache $pythonPackageName
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            Write-Host 'Downloading the official CPython runtime package from NuGet...'
            $download = "$candidate.$([Guid]::NewGuid().ToString('N')).download"
            try {
                Invoke-WebRequest -Uri $pythonPackageUrl -OutFile $download -UseBasicParsing
                if ((Get-Sha256Hex -Path $download) -ne $pythonPackageSha256) {
                    throw 'Downloaded CPython package failed its pinned SHA-256 check.'
                }
                Move-Item -LiteralPath $download -Destination $candidate
            } finally {
                if (Test-Path -LiteralPath $download -PathType Leaf) {
                    Remove-Item -LiteralPath $download
                }
            }
        }
    } else {
        $candidate = (Resolve-Path -LiteralPath $PythonPackagePath).Path
    }
    if ((Get-Sha256Hex -Path $candidate) -ne $pythonPackageSha256) {
        throw 'CPython package failed its pinned SHA-256 check.'
    }
    return $candidate
}

function Install-PrivatePython {
    $package = Get-VerifiedPythonPackage
    $cache = Join-Path $repoRoot '.runtime\install-cache'
    New-Item -ItemType Directory -Path $cache -Force | Out-Null
    $stage = Join-Path $cache ('python-' + [Guid]::NewGuid().ToString('N'))
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    Write-Host 'Extracting application-private Python...'
    [IO.Compression.ZipFile]::ExtractToDirectory($package, $stage)
    $tools = Join-Path $stage 'tools'
    $candidate = Join-Path $tools 'python.exe'
    $identity = & $candidate -I -c 'import platform; print(platform.python_version())'
    if ($LASTEXITCODE -ne 0 -or $identity -ne $pythonVersion) {
        throw 'The extracted interpreter failed its version check.'
    }
    if (Test-Path -LiteralPath $runtimeRoot) {
        if (@(Get-ChildItem -LiteralPath $runtimeRoot -Force).Count -gt 0) {
            throw 'An existing private runtime directory is incomplete. Back it up before retrying.'
        }
        Remove-Item -LiteralPath $runtimeRoot
    }
    Move-Item -LiteralPath $tools -Destination $runtimeRoot
}

function Invoke-RuntimePython {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & $runtimePython @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE."
    }
}

Confirm-SystemDriveInstall
if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
    throw "Release requirements file not found: $requirements"
}
if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
    Install-PrivatePython
}

$env:PYTHONNOUSERSITE = '1'
$runtimeCache = Join-Path $repoRoot '.runtime\cache\pip'
$runtimeTemp = Join-Path $repoRoot '.runtime\tmp'
New-Item -ItemType Directory -Path $runtimeCache, $runtimeTemp, (Split-Path $runtimeState), (Join-Path $repoRoot 'data'), (Join-Path $repoRoot 'logs') -Force | Out-Null
$env:PIP_CACHE_DIR = $runtimeCache
$env:TEMP = $runtimeTemp
$env:TMP = $runtimeTemp
Write-Host 'Installing constrained runtime dependencies. The first run may take several minutes...'
Invoke-RuntimePython -Arguments @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '--requirement', $requirements)
Invoke-RuntimePython -Arguments @('-m', 'pip', 'check')

$previousPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
try {
    $env:PYTHONPATH = Join-Path $repoRoot 'src'
    Invoke-RuntimePython -Arguments @('-X', 'utf8', '-c', 'import a_share_quant, akshare, baostock, pandas, pyarrow; print("runtime validation passed")')
    Invoke-RuntimePython -Arguments @('-X', 'utf8', (Join-Path $repoRoot 'scripts\quant_cli.py'), '--help')
} finally {
    if ($null -eq $previousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $previousPythonPath
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $repoRoot '.env')) -and (Test-Path -LiteralPath (Join-Path $repoRoot '.env.example'))) {
    Copy-Item -LiteralPath (Join-Path $repoRoot '.env.example') -Destination (Join-Path $repoRoot '.env')
}
$installedVersion = (& $runtimePython -c 'import platform; print(platform.python_version())').Trim()
$state = [ordered]@{
    format_version = 1
    installed_at = [DateTimeOffset]::Now.ToString('o')
    application_root = $repoRoot
    python_version = $installedVersion
    requirements_sha256 = Get-Sha256Hex -Path $requirements
    system_drive_install = ([IO.Path]::GetPathRoot($repoRoot).TrimEnd('\') -ieq [string]$env:SystemDrive)
}
[IO.File]::WriteAllText($runtimeState, ($state | ConvertTo-Json), [Text.UTF8Encoding]::new($false))

if (-not $SkipShortcut) {
    & (Join-Path $repoRoot 'scripts\create_desktop_shortcut.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw 'Desktop shortcut creation failed.'
    }
}

$productName = (Get-Content -LiteralPath (Join-Path $repoRoot 'VERSION.json') -Raw -Encoding UTF8 | ConvertFrom-Json).product_name
Write-Host "$productName installation completed."
if (-not $SkipLaunch) {
    & (Join-Path $repoRoot 'scripts\start_quant_workbench.ps1')
}
