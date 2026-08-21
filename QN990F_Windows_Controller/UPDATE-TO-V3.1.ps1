$ErrorActionPreference = "Stop"

$AppDir = Join-Path $env:LOCALAPPDATA "QN990FController"
$InstalledController = Join-Path $AppDir "QN990FController.py"
$ConfigPath = Join-Path $AppDir "config.json"
$PidPath = Join-Path $AppDir "controller.pid"
$StatusPath = Join-Path $AppDir "status.json"
$VenvPython = Join-Path $AppDir "venv\Scripts\python.exe"
$VenvPythonW = Join-Path $AppDir "venv\Scripts\pythonw.exe"
$SourceController = Join-Path $PSScriptRoot "QN990FController.py"

if (-not (Test-Path $InstalledController)) {
    throw "QN990F Controller is not installed. Run INSTALL-ME.cmd instead."
}
if (-not (Test-Path $VenvPython)) {
    throw "Installed Python environment is missing. Run INSTALL-ME.cmd to repair it."
}
if (-not (Test-Path $SourceController)) {
    throw "QN990FController.py is missing from this update package."
}

Write-Host "Updating QN990F Controller to v3.1..." -ForegroundColor Cyan
Write-Host "This preserves TV IP, Samsung token, idle timeout, hotkey, and device filters."

if (Test-Path $PidPath) {
    try {
        $ControllerPid = [int](Get-Content $PidPath -ErrorAction Stop)
        Stop-Process -Id $ControllerPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 400
    } catch {}
}
Remove-Item $PidPath -Force -ErrorAction SilentlyContinue

Copy-Item $SourceController $InstalledController -Force

# Keep v3 defaults available even when updating directly from v1/v2.
if (Test-Path $ConfigPath) {
    $Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json

    function Set-DefaultProperty($Name, $Value) {
        if (-not ($Config.PSObject.Properties.Name -contains $Name)) {
            $Config | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
        }
    }

    Set-DefaultProperty "connection_refresh_seconds" 8.0
    Set-DefaultProperty "input_wake_debounce_ms" 180
    Set-DefaultProperty "mouse_wake_threshold_counts" 24
    Set-DefaultProperty "mouse_motion_window_ms" 500
    Set-DefaultProperty "ignored_input_device_substrings" @()

    $Config.wake_guard_ms = 800
    $Config.poll_interval_ms = 50
    $Config | ConvertTo-Json -Depth 5 | Set-Content $ConfigPath -Encoding UTF8
}

Write-Host ""
Write-Host "Running Win32 RAWMOUSE self-test..."
& $VenvPython $InstalledController --self-test
if ($LASTEXITCODE -ne 0) {
    throw "RAWMOUSE self-test failed. The background controller was NOT restarted."
}

Remove-Item $StatusPath -Force -ErrorAction SilentlyContinue
Start-Process -FilePath $VenvPythonW -ArgumentList "`"$InstalledController`"" -WorkingDirectory $AppDir
Start-Sleep -Seconds 1

Write-Host ""
Write-Host "Updated to v3.1." -ForegroundColor Green
Write-Host "Fixed: v3 could not parse ANY mouse Raw Input because the anonymous RAWMOUSE"
Write-Host "button struct was not exposed correctly through ctypes."
Write-Host ""
Write-Host "Expected self-test result:"
Write-Host "  RAWMOUSE ctypes self-test passed (size=24)"
Write-Host ""
Write-Host "Log: $AppDir\controller.log"
