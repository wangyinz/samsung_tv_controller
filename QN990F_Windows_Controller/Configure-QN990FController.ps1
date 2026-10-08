$ErrorActionPreference = "Stop"

$AppDir = Join-Path $env:LOCALAPPDATA "QN990FController"
$ConfigPath = Join-Path $AppDir "config.json"
$ControllerPath = Join-Path $AppDir "QN990FController.py"
$VenvPython = Join-Path $AppDir "venv\Scripts\python.exe"
$VenvPythonW = Join-Path $AppDir "venv\Scripts\pythonw.exe"
$PidPath = Join-Path $AppDir "controller.pid"

if (-not (Test-Path $ConfigPath)) {
    throw "Samsung TV Picture Controller is not installed."
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$ControlMethod = if ($Config.control_method) { [string]$Config.control_method } else { "lan" }

Write-Host "Samsung TV Picture Controller configuration" -ForegroundColor Green
Write-Host "Press Enter to keep the current value."

if ($ControlMethod -eq "smartthings") {
    Write-Host "Connection: SmartThings cloud"
    Write-Host "SmartThings TV ID: $($Config.smartthings_device_id)"
    Write-Host "Rerun the installer to change the connection or TV."
} else {
    $NewIp = Read-Host "TV IP [$($Config.tv_ip)]"
    if (-not [string]::IsNullOrWhiteSpace($NewIp)) {
        $Config.tv_ip = $NewIp.Trim()
    }
}

$NewIdle = Read-Host "Idle minutes; 0 disables auto blank [$($Config.idle_minutes)]"
if (-not [string]::IsNullOrWhiteSpace($NewIdle)) {
    $Parsed = 0.0
    if ((-not [double]::TryParse($NewIdle, [ref]$Parsed)) -or $Parsed -lt 0) {
        throw "Idle minutes must be a number >= 0."
    }
    $Config.idle_minutes = $Parsed
    $Config.enable_idle_off = ($Parsed -gt 0)
}

$NewHotkey = Read-Host "Global hotkey [$($Config.hotkey)]"
if (-not [string]::IsNullOrWhiteSpace($NewHotkey)) {
    $Config.hotkey = $NewHotkey.Trim()
}

$RespectDefault = if ($Config.respect_display_required) { "Y" } else { "N" }
$Respect = Read-Host "Respect Windows media/display-required requests? [${RespectDefault}]"
if (-not [string]::IsNullOrWhiteSpace($Respect)) {
    $Config.respect_display_required = ($Respect -notmatch '^[Nn]')
}

Write-Host ""
Write-Host "Checking locally connected HDMI displays..."
$HdmiJson = & $VenvPython $ControllerPath --list-hdmi-targets
if ($LASTEXITCODE -ne 0) {
    throw "Windows could not inspect HDMI displays. No configuration was saved."
}
$HdmiTargets = @($HdmiJson | ConvertFrom-Json)
if ($HdmiTargets.Count -eq 0) {
    throw "No identifiable active HDMI display was found. Connect this PC to the target TV and rerun Configure."
}
for ($Index = 0; $Index -lt $HdmiTargets.Count; $Index++) {
    $Target = $HdmiTargets[$Index]
    Write-Host "  $($Index + 1). $($Target.name) [EDID $($Target.manufacturer):$($Target.product)] $($Target.path)"
}
$CurrentBinding = $Config.hdmi_target
$ExistingChoice = 0
for ($Index = 0; $Index -lt $HdmiTargets.Count; $Index++) {
    if ($CurrentBinding -and $HdmiTargets[$Index].path -eq $CurrentBinding.path -and
        $HdmiTargets[$Index].manufacturer -eq $CurrentBinding.manufacturer -and
        $HdmiTargets[$Index].product -eq $CurrentBinding.product) {
        $ExistingChoice = $Index + 1
        break
    }
}
$Prompt = if ($ExistingChoice) { "Choose the TV HDMI display [$ExistingChoice]" } else { "Choose the TV HDMI display (1-$($HdmiTargets.Count))" }
$ChoiceText = Read-Host $Prompt
if ([string]::IsNullOrWhiteSpace($ChoiceText) -and $ExistingChoice) { $ChoiceText = [string]$ExistingChoice }
$Choice = 0
if ((-not [int]::TryParse($ChoiceText, [ref]$Choice)) -or
    $Choice -lt 1 -or $Choice -gt $HdmiTargets.Count) {
    throw "An explicit HDMI display selection is required. No configuration was saved."
}
$Config | Add-Member -NotePropertyName hdmi_target -NotePropertyValue $HdmiTargets[$Choice - 1] -Force

$Config | ConvertTo-Json -Depth 5 | Set-Content $ConfigPath -Encoding UTF8

# Stop the current daemon so the hotkey is free for validation.
if (Test-Path $PidPath) {
    try {
        $ControllerPid = [int](Get-Content $PidPath)
        Stop-Process -Id $ControllerPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 350
    } catch {}
}
Remove-Item $PidPath -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Checking hotkey..."
& $VenvPython $ControllerPath --check-hotkey
if ($LASTEXITCODE -ne 0) {
    Write-Warning "The selected hotkey cannot be registered. Run Configure again and choose another one."
    Read-Host "Press Enter to close"
    exit 2
}

Write-Host "Validating TV connection..."
& $VenvPython $ControllerPath --pair
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Could not connect/pair. The saved configuration remains, but the controller will not be restarted."
    Read-Host "Press Enter to close"
    exit 3
}

Start-Process -FilePath $VenvPythonW -ArgumentList "`"$ControllerPath`"" -WorkingDirectory $AppDir
Write-Host ""
Write-Host "Saved and restarted." -ForegroundColor Green
Write-Host "Hotkey: $($Config.hotkey)"
Write-Host "Connection: $ControlMethod"
if ($Config.enable_idle_off) {
    Write-Host "Auto Picture Off: $($Config.idle_minutes) minute(s)"
} else {
    Write-Host "Auto Picture Off: disabled"
}
Start-Sleep -Seconds 2
