@echo off
title QN990F Windows Picture Controller Installer
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-QN990FController.ps1"
echo.
if errorlevel 1 (
  echo Installer exited with an error. Read the message above.
) else (
  echo Installation finished.
)
echo.
pause
