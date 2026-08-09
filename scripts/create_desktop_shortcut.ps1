[CmdletBinding()]
param()

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$startScript = Join-Path $repoRoot 'scripts\start_quant_workbench.ps1'
$desktopPath = [Environment]::GetFolderPath('Desktop')
$shortcutName = 'A' + [char]0x80A1 + [char]0x91CF + [char]0x5316 + [char]0x4EA4 + [char]0x6613 + [char]0x7CFB + [char]0x7EDF + '.lnk'
$shortcutPath = Join-Path $desktopPath $shortcutName
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = (Get-Command powershell.exe).Source
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`""
$shortcut.WorkingDirectory = $repoRoot
$shortcut.Description = 'A-Share Quant Workbench - local paper monitor'
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
$shortcut.Save()
Write-Output "Created local desktop shortcut: $shortcutPath"
