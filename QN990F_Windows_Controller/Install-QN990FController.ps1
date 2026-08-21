param(
    [string]$TvIp = "",
    [double]$IdleMinutes = -1,
    [string]$Hotkey = "Ctrl+Alt+P"
)

$ErrorActionPreference = "Stop"
$AppDir = Join-Path $env:LOCALAPPDATA "QN990FController"
$VenvDir = Join-Path $AppDir "venv"
$ControllerPath = Join-Path $AppDir "QN990FController.py"
$ConfigPath = Join-Path $AppDir "config.json"
$PidPath = Join-Path $AppDir "controller.pid"
$StatusPath = Join-Path $AppDir "status.json"
$StartupDir = [Environment]::GetFolderPath("Startup")
$StartupShortcut = Join-Path $StartupDir "QN990F Picture Controller.lnk"
$ProgramsDir = [Environment]::GetFolderPath("Programs")
$StartMenuDir = Join-Path $ProgramsDir "QN990F Controller"

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Stop-ExistingController {
    if (Test-Path $PidPath) {
        try {
            $ControllerPid = [int](Get-Content $PidPath -ErrorAction Stop)
            $Process = Get-Process -Id $ControllerPid -ErrorAction SilentlyContinue
            if ($Process) {
                Write-Host "Stopping existing QN990F Controller (PID $ControllerPid)..."
                Stop-Process -Id $ControllerPid -Force -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds 300
            }
        } catch {
            # Stale PID file; ignore.
        }
    }
    Remove-Item $PidPath -Force -ErrorAction SilentlyContinue
}

function Test-PythonInvocation {
    param(
        [string]$Exe,
        [string[]]$PrefixArgs = @()
    )
    try {
        $AllArgs = @($PrefixArgs) + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)")
        & $Exe @AllArgs *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Find-Python {
    $PyLauncherCandidates = @()
    $PyCmd = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($PyCmd) { $PyLauncherCandidates += $PyCmd.Source }
    $LocalPy = Join-Path $env:LOCALAPPDATA "Programs\Python\Launcher\py.exe"
    if (Test-Path $LocalPy) { $PyLauncherCandidates += $LocalPy }

    foreach ($Launcher in ($PyLauncherCandidates | Select-Object -Unique)) {
        foreach ($Selector in @("-3.13", "-3.12", "-3.11", "-3.10", "-3.9", "-3")) {
            if (Test-PythonInvocation -Exe $Launcher -PrefixArgs @($Selector)) {
                return [PSCustomObject]@{
                    Exe = $Launcher
                    PrefixArgs = @($Selector)
                    Description = "$Launcher $Selector"
                }
            }
        }
    }

    $Candidates = @()
    $PythonCmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($PythonCmd -and $PythonCmd.Source -notlike "*\WindowsApps\*") {
        $Candidates += $PythonCmd.Source
    }

    $PythonRoot = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path $PythonRoot) {
        $Candidates += @(Get-ChildItem $PythonRoot -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName "python.exe" } |
            Where-Object { Test-Path $_ })
    }

    foreach ($Candidate in ($Candidates | Select-Object -Unique)) {
        if (Test-PythonInvocation -Exe $Candidate) {
            return [PSCustomObject]@{
                Exe = $Candidate
                PrefixArgs = @()
                Description = $Candidate
            }
        }
    }

    return $null
}

function Install-PythonWithWinget {
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $Winget) {
        throw "Python 3.9+ was not found and winget is unavailable. Install Microsoft's 'App Installer' (which provides winget), then run this installer again."
    }

    Write-Step "Installing Python 3.12 for the current user"
    & $Winget.Source install `
        --id Python.Python.3.12 `
        --exact `
        --source winget `
        --scope user `
        --silent `
        --accept-source-agreements `
        --accept-package-agreements `
        --disable-interactivity

    if ($LASTEXITCODE -ne 0) {
        throw "winget could not install Python 3.12 (exit code $LASTEXITCODE)."
    }

    Start-Sleep -Seconds 1
}

function New-Shortcut {
    param(
        [string]$Path,
        [string]$Target,
        [string]$Arguments = "",
        [string]$WorkingDirectory = ""
    )
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($Path)
    $Shortcut.TargetPath = $Target
    $Shortcut.Arguments = $Arguments
    if ($WorkingDirectory) { $Shortcut.WorkingDirectory = $WorkingDirectory }
    $Shortcut.Save()
}

Write-Host "QN990F Windows Picture Controller installer" -ForegroundColor Green
Write-Host "Hotkey: $Hotkey -> Picture Off"
Write-Host "Mouse/keyboard input after blanking -> wake"

if (-not (Test-Path (Join-Path $PSScriptRoot "QN990FController.py"))) {
    throw "QN990FController.py is missing. Keep the files from the ZIP in the same folder."
}

if ([string]::IsNullOrWhiteSpace($TvIp)) {
    $TvIp = Read-Host "Enter the QN990F LAN IP address (example: 192.168.1.50)"
}
if ([string]::IsNullOrWhiteSpace($TvIp)) {
    throw "TV IP address cannot be empty."
}

if ($IdleMinutes -lt 0) {
    $RawIdle = Read-Host "Idle minutes before automatic Picture Off [10; enter 0 to disable]"
    if ([string]::IsNullOrWhiteSpace($RawIdle)) {
        $IdleMinutes = 10
    } else {
        $Parsed = 0.0
        if ((-not [double]::TryParse($RawIdle, [ref]$Parsed)) -or $Parsed -lt 0) {
            throw "Idle minutes must be a number >= 0."
        }
        $IdleMinutes = $Parsed
    }
}

New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
Stop-ExistingController

Write-Step "Copying controller files"
Copy-Item (Join-Path $PSScriptRoot "QN990FController.py") $ControllerPath -Force
Copy-Item (Join-Path $PSScriptRoot "Configure-QN990FController.ps1") (Join-Path $AppDir "Configure-QN990FController.ps1") -Force
Copy-Item (Join-Path $PSScriptRoot "Uninstall-QN990FController.ps1") (Join-Path $AppDir "Uninstall-QN990FController.ps1") -Force
Copy-Item (Join-Path $PSScriptRoot "README.txt") (Join-Path $AppDir "README.txt") -Force

$Python = Find-Python
if (-not $Python) {
    Install-PythonWithWinget
    $Python = Find-Python
}
if (-not $Python) {
    throw "Python was installed but could not be located. Close this PowerShell window, open a new one, and rerun the installer."
}
Write-Host "Using Python: $($Python.Description)"

Write-Step "Creating an isolated Python environment"
if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
    $VenvArgs = @($Python.PrefixArgs) + @("-m", "venv", $VenvDir)
    & $Python.Exe @VenvArgs
    if ($LASTEXITCODE -ne 0) { throw "Failed to create Python virtual environment." }
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPythonW = Join-Path $VenvDir "Scripts\pythonw.exe"

Write-Step "Installing samsungtvws 3.0.5"
& $VenvPython -m pip install --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to update pip inside the virtual environment." }

& $VenvPython -m pip install --disable-pip-version-check "samsungtvws==3.0.5"
if ($LASTEXITCODE -ne 0) { throw "Failed to install samsungtvws." }

$Config = [ordered]@{
    tv_ip = $TvIp.Trim()
    port = 8002
    idle_minutes = [double]$IdleMinutes
    enable_idle_off = ($IdleMinutes -gt 0)
    respect_display_required = $true
    hotkey = $Hotkey
    picture_off_key = "KEY_PICTURE_OFF"
    wake_key = "KEY_RETURN"
    wake_guard_ms = 700
    poll_interval_ms = 100
    socket_timeout_seconds = 5.0
    key_press_delay_seconds = 0.05
}
$Config | ConvertTo-Json | Set-Content -Path $ConfigPath -Encoding UTF8

Write-Step "Checking the global hotkey"
& $VenvPython $ControllerPath --check-hotkey
if ($LASTEXITCODE -ne 0) {
    throw "The hotkey '$Hotkey' is already in use or invalid. Rerun the installer with another hotkey, for example: -Hotkey 'Ctrl+Alt+O'."
}

Write-Step "Pairing with the TV"
Write-Host "Turn the QN990F on and keep it on the same LAN/subnet as this PC."
Write-Host "When the TV asks whether to allow QN990F-PC-Controller, choose Allow." -ForegroundColor Yellow
& $VenvPython $ControllerPath --pair
if ($LASTEXITCODE -ne 0) {
    throw "Pairing failed. Check the TV IP, LAN connectivity, and Samsung Device Connection Manager permissions."
}

Write-Step "Testing Picture Off + wake"
Write-Host "The TV should go black for about 2 seconds and then return."
& $VenvPython $ControllerPath --test
if ($LASTEXITCODE -ne 0) {
    Write-Warning "The test command reported an error. See $AppDir\controller.log."
}
$Answer = Read-Host "Did the TV actually go black and then come back? [Y/n]"
if ($Answer -match '^[Nn]') {
    Write-Warning "KEY_PICTURE_OFF may be ignored by this QN990F firmware. The controller is installed, but automatic startup will NOT be enabled."
    Write-Host "You can retest later with:"
    Write-Host "`"$VenvPython`" `"$ControllerPath`" --test"
    exit 2
}

Write-Step "Adding automatic startup"
New-Shortcut `
    -Path $StartupShortcut `
    -Target $VenvPythonW `
    -Arguments "`"$ControllerPath`"" `
    -WorkingDirectory $AppDir

New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null
$PowerShellExe = (Get-Command powershell.exe).Source
New-Shortcut `
    -Path (Join-Path $StartMenuDir "Configure QN990F Controller.lnk") `
    -Target $PowerShellExe `
    -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$AppDir\Configure-QN990FController.ps1`"" `
    -WorkingDirectory $AppDir
New-Shortcut `
    -Path (Join-Path $StartMenuDir "Uninstall QN990F Controller.lnk") `
    -Target $PowerShellExe `
    -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$AppDir\Uninstall-QN990FController.ps1`"" `
    -WorkingDirectory $AppDir

Write-Step "Starting the controller"
Remove-Item $StatusPath -Force -ErrorAction SilentlyContinue
Start-Process -FilePath $VenvPythonW -ArgumentList "`"$ControllerPath`"" -WorkingDirectory $AppDir
Start-Sleep -Seconds 1

$Started = $false
if (Test-Path $StatusPath) {
    try {
        $Status = Get-Content $StatusPath -Raw | ConvertFrom-Json
        $Started = [bool]$Status.running
    } catch {}
}
if (-not $Started) {
    Write-Warning "The background process did not report a running state. Check $AppDir\controller.log."
}

Write-Host ""
Write-Host "Installed." -ForegroundColor Green
Write-Host "  Hotkey:           $Hotkey"
if ($IdleMinutes -gt 0) {
    Write-Host "  Automatic blank:  after $IdleMinutes minute(s) of keyboard/mouse inactivity"
} else {
    Write-Host "  Automatic blank:  disabled"
}
Write-Host "  Wake:             next keyboard/mouse input after the controller blanked the TV"
Write-Host "  Config/logs:      $AppDir"
Write-Host ""
Write-Host "For reliable use, reserve the TV's IP in your router/DHCP settings."
Write-Host "Also set Windows' own 'turn off my screen' timeout longer than this controller's timeout (or Never)."
