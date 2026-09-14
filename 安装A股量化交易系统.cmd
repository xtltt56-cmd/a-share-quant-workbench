@echo off
chcp 65001 >nul
title 安装A股量化交易系统
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_release.ps1"
if errorlevel 1 (
  echo.
  echo 安装未完成，请查看上方错误信息。
  pause
  exit /b 1
)
exit /b 0
