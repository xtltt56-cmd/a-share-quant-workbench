[CmdletBinding()]
param()

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$startScript = Join-Path $repoRoot 'scripts\start_quant_workbench.ps1'
$desktopPath = [Environment]::GetFolderPath('Desktop')
$metadata = Get-Content -LiteralPath (Join-Path $repoRoot 'VERSION.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$productName = [string]$metadata.product_name
if ([string]::IsNullOrWhiteSpace($productName) -or $productName.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0) {
    throw 'Invalid product name in VERSION.json.'
}
$shortcutName = $productName + '.lnk'
$shortcutPath = Join-Path $desktopPath $shortcutName
$shell = New-Object -ComObject WScript.Shell
$launchArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`""
$legacyName = 'A' + [char]0x80A1 + [char]0x91CF + [char]0x5316 + [char]0x4EA4 + [char]0x6613 + [char]0x7CFB + [char]0x7EDF + '.lnk'
$legacyPath = Join-Path $desktopPath $legacyName
if ((Test-Path -LiteralPath $legacyPath -PathType Leaf) -and -not (Test-Path -LiteralPath $shortcutPath)) {
    $legacy = $shell.CreateShortcut($legacyPath)
    if ($legacy.WorkingDirectory -ieq $repoRoot -and $legacy.Arguments -ceq $launchArguments) {
        Move-Item -LiteralPath $legacyPath -Destination $shortcutPath -ErrorAction Stop
    }
}
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = (Get-Command powershell.exe).Source
$shortcut.Arguments = $launchArguments
$shortcut.WorkingDirectory = $repoRoot
$shortcut.Description = $productName
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
$shortcut.Save()
Write-Output "Created local desktop shortcut: $shortcutPath"
