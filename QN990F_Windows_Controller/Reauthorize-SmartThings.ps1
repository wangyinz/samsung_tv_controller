$ErrorActionPreference = "Stop"

$AppDir = Join-Path $env:LOCALAPPDATA "QN990FController"
$ConfigPath = Join-Path $AppDir "config.json"
$ControllerPath = Join-Path $AppDir "QN990FController.py"
$VenvPython = Join-Path $AppDir "venv\Scripts\python.exe"
$VenvPythonW = Join-Path $AppDir "venv\Scripts\pythonw.exe"
$PidPath = Join-Path $AppDir "controller.pid"

if ((-not (Test-Path $ConfigPath)) -or
    (-not (Test-Path $ControllerPath)) -or
    (-not (Test-Path $VenvPython))) {
    throw "Samsung TV Picture Controller is not installed correctly."
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
if ([string]$Config.control_method -ne "smartthings") {
    throw "This installation is not using SmartThings cloud control."
}
$SmartThingsCli = [string]$Config.smartthings_cli
$SmartThingsCliScript = if (
    $Config.PSObject.Properties.Name -contains "smartthings_cli_script"
) { [string]$Config.smartthings_cli_script } else { "" }
if ([string]::IsNullOrWhiteSpace($SmartThingsCliScript)) {
    Write-Warning "This installation still uses the unsigned standalone SmartThings executable, which Windows Application Control can block. Rerun INSTALL-ME.cmd to install the signed Node.js runtime before reauthorizing."
    Read-Host "Press Enter to close"
    exit 3
}
if ((-not (Test-Path $SmartThingsCli)) -or
    (-not (Test-Path $SmartThingsCliScript))) {
    Write-Warning "The SmartThings runtime is incomplete. Rerun INSTALL-ME.cmd to repair it before reauthorizing."
    Read-Host "Press Enter to close"
    exit 3
}

Write-Host "Samsung TV Picture Controller - SmartThings reauthorization" -ForegroundColor Green
Write-Host "The background controller will stop while browser sign-in completes."
Write-Host "No Picture Off or wake command will be sent."
Write-Host ""

$ControllerPids = @()
try {
    $ControllerPids = @(Get-CimInstance Win32_Process -ErrorAction Stop |
        Where-Object {
            $_.Name -in @("python.exe", "pythonw.exe") -and
            $_.CommandLine -and
            ([string]$_.CommandLine).IndexOf(
                $ControllerPath,
                [StringComparison]::OrdinalIgnoreCase
            ) -ge 0
        } |
        ForEach-Object { [int]$_.ProcessId })
} catch {}

if (Test-Path $PidPath) {
    try {
        $ControllerPid = [int](Get-Content $PidPath -ErrorAction Stop)
        $Process = Get-Process -Id $ControllerPid -ErrorAction SilentlyContinue
        $ExpectedPaths = @($VenvPython, $VenvPythonW)
        if ($Process -and
            (($ControllerPids -contains $ControllerPid) -or
             ($Process.Path -and ($ExpectedPaths -contains $Process.Path)))) {
            $ControllerPids += $ControllerPid
        } elseif ($Process) {
            Write-Warning "Ignoring stale controller PID $ControllerPid because it belongs to another process."
        }
    } catch {}
}
$ControllerPids = @($ControllerPids | Sort-Object -Unique)
foreach ($ControllerPid in $ControllerPids) {
    Stop-Process -Id $ControllerPid -Force -ErrorAction SilentlyContinue
}
if ($ControllerPids.Count -gt 0) {
    Start-Sleep -Milliseconds 300
}
Remove-Item $PidPath -Force -ErrorAction SilentlyContinue

& $VenvPython $ControllerPath --pair
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Reauthorization failed. The controller remains stopped to avoid a token race."
    Read-Host "Press Enter to close"
    exit 2
}

Start-Process -FilePath $VenvPythonW -ArgumentList "`"$ControllerPath`"" -WorkingDirectory $AppDir
Write-Host ""
Write-Host "SmartThings authorization was renewed and the controller restarted." -ForegroundColor Green
Read-Host "Press Enter to close"
