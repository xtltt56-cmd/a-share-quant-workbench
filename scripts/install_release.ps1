[CmdletBinding()]
param(
    [switch]$NonInteractive,
    [switch]$AllowSystemDrive,
    [switch]$SkipShortcut,
    [switch]$SkipLaunch,
    [string]$PythonInstallerPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeRoot = Join-Path $repoRoot 'runtime'
$runtimePython = Join-Path $runtimeRoot 'python.exe'
$runtimeState = Join-Path $repoRoot '.runtime\install-state.json'
$requirements = Join-Path $repoRoot 'constraints\release-py312.txt'
$pythonVersion = '3.12.10'
$pythonInstallerUrl = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-amd64.exe"
$pythonInstallerSha256 = '67B5635E80EA51072B87941312D00EC8927C4DB9BA18938F7AD2D27B328B95FB'

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

function Get-VerifiedPythonInstaller {
    if ([string]::IsNullOrWhiteSpace($PythonInstallerPath)) {
        $cache = Join-Path $repoRoot '.runtime\install-cache'
        New-Item -ItemType Directory -Path $cache -Force | Out-Null
        $candidate = Join-Path $cache "python-$pythonVersion-amd64.exe"
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            Write-Host 'Downloading the signed installer from python.org...'
            Invoke-WebRequest -Uri $pythonInstallerUrl -OutFile $candidate -UseBasicParsing
        }
    } else {
        $candidate = (Resolve-Path -LiteralPath $PythonInstallerPath).Path
    }
    $actualHash = Get-Sha256Hex -Path $candidate
    if ($actualHash -ne $pythonInstallerSha256) {
        throw "Python installer checksum failed. Actual SHA-256: $actualHash"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $candidate
    if (
        $signature.Status -ne 'Valid' -or
        $null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notlike '*Python Software Foundation*'
    ) {
        throw 'Python installer signature is invalid or has an unexpected signer.'
    }
    return $candidate
}

function Install-PrivatePython {
    $installer = Get-VerifiedPythonInstaller
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    $arguments = @(
        '/quiet',
        'InstallAllUsers=0',
        "TargetDir=`"$runtimeRoot`"",
        'Include_pip=1',
        'Include_launcher=0',
        'Include_test=0',
        'Include_doc=0',
        'Include_tcltk=0',
        'Include_tools=0',
        'Include_dev=0',
        'Include_symbols=0',
        'Include_debug=0',
        'AssociateFiles=0',
        'Shortcuts=0',
        'PrependPath=0'
    )
    Write-Host 'Installing the application-private Python runtime...'
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) {
        throw "Python installation failed with exit code $($process.ExitCode)."
    }
    if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
        throw 'Python installer completed but the private runtime is missing.'
    }
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

Write-Host 'A-Share Quant Workbench installation completed.'
if (-not $SkipLaunch) {
    & (Join-Path $repoRoot 'scripts\start_quant_workbench.ps1')
}
