$ErrorActionPreference = "SilentlyContinue"

$AppDir = Join-Path $env:LOCALAPPDATA "QN990FController"
$PidPath = Join-Path $AppDir "controller.pid"
$TrayPidPath = Join-Path $AppDir "status-tray.pid"
$TrayScriptPath = Join-Path $AppDir "StatusTray.ps1"
$StartupDir = [Environment]::GetFolderPath("Startup")
$ProgramsDir = [Environment]::GetFolderPath("Programs")
$StartupShortcuts = @(
    (Join-Path $StartupDir "Samsung TV Picture Controller.lnk"),
    (Join-Path $StartupDir "QN990F Picture Controller.lnk")
)
$StartMenuDirs = @(
    (Join-Path $ProgramsDir "Samsung TV Picture Controller"),
    (Join-Path $ProgramsDir "QN990F Controller")
)

Write-Host "Uninstalling Samsung TV Picture Controller..."

if (Test-Path $TrayPidPath) {
    try {
        $TrayPid = [int](Get-Content $TrayPidPath)
        $TrayProcess = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $TrayPid" -ErrorAction SilentlyContinue
        if ($TrayProcess -and $TrayProcess.CommandLine -and
            ([string]$TrayProcess.CommandLine).IndexOf(
                $TrayScriptPath,
                [StringComparison]::OrdinalIgnoreCase
            ) -ge 0) {
            Stop-Process -Id $TrayPid -Force
        }
    } catch {}
}

if (Test-Path $PidPath) {
    try {
        $ControllerPid = [int](Get-Content $PidPath)
        Stop-Process -Id $ControllerPid -Force
        Start-Sleep -Milliseconds 300
    } catch {}
}

Remove-Item $StartupShortcuts -Force
Remove-Item $StartMenuDirs -Recurse -Force

# This script may itself be running from AppDir. Launch a tiny delayed cleanup
# in a second PowerShell process so AppDir can be removed after this process exits.
$Cleanup = "Start-Sleep -Seconds 1; Remove-Item -LiteralPath '$($AppDir.Replace("'","''"))' -Recurse -Force -ErrorAction SilentlyContinue"
Start-Process powershell.exe -ArgumentList "-NoProfile", "-WindowStyle", "Hidden", "-Command", $Cleanup -WindowStyle Hidden

Write-Host "Uninstalled."
Start-Sleep -Seconds 1
