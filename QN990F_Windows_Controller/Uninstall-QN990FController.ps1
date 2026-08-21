$ErrorActionPreference = "SilentlyContinue"

$AppDir = Join-Path $env:LOCALAPPDATA "QN990FController"
$PidPath = Join-Path $AppDir "controller.pid"
$StartupShortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "QN990F Picture Controller.lnk"
$StartMenuDir = Join-Path ([Environment]::GetFolderPath("Programs")) "QN990F Controller"

Write-Host "Uninstalling QN990F Controller..."

if (Test-Path $PidPath) {
    try {
        $ControllerPid = [int](Get-Content $PidPath)
        Stop-Process -Id $ControllerPid -Force
        Start-Sleep -Milliseconds 300
    } catch {}
}

Remove-Item $StartupShortcut -Force
Remove-Item $StartMenuDir -Recurse -Force

# This script may itself be running from AppDir. Launch a tiny delayed cleanup
# in a second PowerShell process so AppDir can be removed after this process exits.
$Cleanup = "Start-Sleep -Seconds 1; Remove-Item -LiteralPath '$($AppDir.Replace("'","''"))' -Recurse -Force -ErrorAction SilentlyContinue"
Start-Process powershell.exe -ArgumentList "-NoProfile", "-WindowStyle", "Hidden", "-Command", $Cleanup -WindowStyle Hidden

Write-Host "Uninstalled."
Start-Sleep -Seconds 1
