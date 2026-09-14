@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_quant_workbench.ps1"
if errorlevel 1 pause
