@echo off
title Samsung TV Picture Controller Installer
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-QN990FController.ps1"
set "InstallerExitCode=%ERRORLEVEL%"
echo.
if not "%InstallerExitCode%"=="0" (
  echo Installer exited with an error. Read the message above.
) else (
  echo Installation finished.
)
echo.
pause
exit /b %InstallerExitCode%
