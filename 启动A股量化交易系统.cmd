@echo off
chcp 65001 >nul
title A股量化交易系统
if not exist "%~dp0.venv\Scripts\python.exe" if not exist "%~dp0runtime\python.exe" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_release.ps1"
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_quant_workbench.ps1"
)
if errorlevel 1 (
  echo.
  echo 启动失败，请查看 logs 文件夹中的日志。
  pause
  exit /b 1
)
exit /b 0
