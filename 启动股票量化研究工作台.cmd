@echo off
chcp 65001 >nul
title 股票量化研究工作台
if exist "%~dp0.venv\Scripts\python.exe" goto launch
if exist "%~dp0runtime\python.exe" goto launch
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_release.ps1"
goto result

:launch
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_quant_workbench.ps1"

:result
if errorlevel 1 (
  echo.
  echo 启动失败，请查看 logs 文件夹中的日志。
  pause
  exit /b 1
)
exit /b 0
