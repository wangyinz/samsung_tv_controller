$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

$AppDir = Join-Path $env:LOCALAPPDATA "QN990FController"
$StatusPath = Join-Path $AppDir "status.json"
$ConfigPath = Join-Path $AppDir "config.json"
$ControllerPath = Join-Path $AppDir "QN990FController.py"
$ControllerPidPath = Join-Path $AppDir "controller.pid"
$PythonWPath = Join-Path $AppDir "venv\Scripts\pythonw.exe"
$TrayPidPath = Join-Path $AppDir "status-tray.pid"
$ConfigurePath = Join-Path $AppDir "Configure-QN990FController.ps1"
$ReauthorizePath = Join-Path $AppDir "Reauthorize-SmartThings.ps1"
$LogPath = Join-Path $AppDir "controller.log"

$TrayMutex = New-Object System.Threading.Mutex(
    $false,
    "Local\SamsungTVPictureControllerStatusTray"
)
$HasTrayMutex = $false
try {
    try {
        $HasTrayMutex = $TrayMutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $HasTrayMutex = $true
    }
    if (-not $HasTrayMutex) { exit 0 }

    $PID | Set-Content -LiteralPath $TrayPidPath -Encoding ASCII

    $Notify = New-Object System.Windows.Forms.NotifyIcon
    $Notify.Text = "Samsung TV Picture Controller"
    $Notify.Icon = [System.Drawing.SystemIcons]::Information
    $Notify.Visible = $true

    $Menu = New-Object System.Windows.Forms.ContextMenuStrip
    $ControllerItem = New-Object System.Windows.Forms.ToolStripMenuItem
    $ControllerItem.Text = "Controller: loading"
    $ControllerItem.Enabled = $false
    [void]$Menu.Items.Add($ControllerItem)

    $VolumeItem = New-Object System.Windows.Forms.ToolStripMenuItem
    $VolumeItem.Text = "Volume integration: loading"
    $VolumeItem.Enabled = $false
    [void]$Menu.Items.Add($VolumeItem)
    [void]$Menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))

    $ConfigureItem = $Menu.Items.Add("Configure Controller...")
    $ReauthorizeItem = $Menu.Items.Add("Reauthorize SmartThings...")
    $RestartItem = $Menu.Items.Add("Restart Controller")
    $LogItem = $Menu.Items.Add("Open Controller Log")
    [void]$Menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))
    $ExitItem = $Menu.Items.Add("Quit Controller")
    $Notify.ContextMenuStrip = $Menu

    function Show-TrayError([string]$Message) {
        $Notify.ShowBalloonTip(
            6000,
            "Samsung TV Picture Controller",
            $Message,
            [System.Windows.Forms.ToolTipIcon]::Error
        )
    }

    function Start-ControllerHelper([string]$ScriptPath) {
        if (-not (Test-Path -LiteralPath $ScriptPath)) {
            throw "The installed helper is missing: $ScriptPath"
        }
        Start-Process powershell.exe -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", "`"$ScriptPath`""
        ) -WorkingDirectory $AppDir
    }

    function Stop-Controller {
        if (-not (Test-Path -LiteralPath $ControllerPidPath)) { return }
        $ControllerPid = [int](Get-Content -LiteralPath $ControllerPidPath -ErrorAction Stop)
        $ControllerProcess = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $ControllerPid" -ErrorAction SilentlyContinue
        if (-not $ControllerProcess) { return }
        if (-not $ControllerProcess.CommandLine -or
            ([string]$ControllerProcess.CommandLine).IndexOf(
                $ControllerPath,
                [StringComparison]::OrdinalIgnoreCase
            ) -lt 0) {
            throw "The saved controller PID belongs to another process; it was not stopped."
        }
        Stop-Process -Id $ControllerPid -Force -ErrorAction Stop
        for ($Attempt = 0; $Attempt -lt 50; $Attempt++) {
            if (-not (Get-Process -Id $ControllerPid -ErrorAction SilentlyContinue)) {
                return
            }
            Start-Sleep -Milliseconds 100
        }
        throw "The controller did not stop within five seconds."
    }

    $ConfigureItem.Add_Click({
        try { Start-ControllerHelper $ConfigurePath }
        catch { Show-TrayError $_.Exception.Message }
    })
    $ReauthorizeItem.Add_Click({
        try { Start-ControllerHelper $ReauthorizePath }
        catch { Show-TrayError $_.Exception.Message }
    })
    $LogItem.Add_Click({
        try {
            if (-not (Test-Path -LiteralPath $LogPath)) {
                throw "The controller log does not exist yet."
            }
            Start-Process notepad.exe -ArgumentList @("`"$LogPath`"")
        } catch { Show-TrayError $_.Exception.Message }
    })
    $RestartItem.Add_Click({
        try {
            Stop-Controller
            if (-not (Test-Path -LiteralPath $PythonWPath)) {
                throw "The installed Python runtime is missing. Rerun INSTALL-ME.cmd."
            }
            Start-Process -FilePath $PythonWPath `
                -ArgumentList "`"$ControllerPath`"" `
                -WorkingDirectory $AppDir -WindowStyle Hidden
        } catch { Show-TrayError $_.Exception.Message }
    })
    $ExitItem.Add_Click({
        try {
            Stop-Controller
            [System.Windows.Forms.Application]::Exit()
        } catch { Show-TrayError $_.Exception.Message }
    })
    $Notify.Add_DoubleClick({
        try {
            if (Test-Path -LiteralPath $LogPath) {
                Start-Process notepad.exe -ArgumentList @("`"$LogPath`"")
            }
        } catch { Show-TrayError $_.Exception.Message }
    })

    $script:LastState = $null
    function Update-TrayStatus {
        $State = "stopped"
        $Running = $false
        if (Test-Path -LiteralPath $StatusPath) {
            try {
                $Status = Get-Content -LiteralPath $StatusPath -Raw | ConvertFrom-Json
                $Running = [bool]$Status.running
                $State = if ($Status.state) { [string]$Status.state } else { "unknown" }
                if ($Running) {
                    $StatusPid = [int]$Status.pid
                    $Running = $null -ne (
                        Get-Process -Id $StatusPid -ErrorAction SilentlyContinue
                    )
                }
                if (-not $Running -and $State -ne "error") { $State = "stopped" }
            } catch {
                if ($null -ne $script:LastState) { return }
                $State = "unknown"
            }
        }

        $VolumeEnabled = $false
        $ControlMethod = "unknown"
        if (Test-Path -LiteralPath $ConfigPath) {
            try {
                $Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
                $VolumeEnabled = [bool]$Config.enable_volume_control
                $ControlMethod = if ($Config.control_method) {
                    [string]$Config.control_method
                } else { "lan" }
            } catch {}
        }

        $ControllerItem.Text = "Controller: $State ($ControlMethod)"
        $VolumeItem.Text = if ($VolumeEnabled) {
            "Volume integration: enabled"
        } else {
            "Volume integration: disabled"
        }
        $ReauthorizeItem.Visible = ($ControlMethod -eq "smartthings")

        $NeedsAttention = $State -in @("authorization_required", "error", "stopped")
        $Notify.Icon = if ($NeedsAttention) {
            [System.Drawing.SystemIcons]::Error
        } else {
            [System.Drawing.SystemIcons]::Information
        }
        $Tooltip = "Samsung TV Controller: $State"
        if ($Tooltip.Length -gt 63) { $Tooltip = $Tooltip.Substring(0, 63) }
        $Notify.Text = $Tooltip

        if ($null -ne $script:LastState -and $script:LastState -ne $State -and
            $NeedsAttention) {
            $Detail = if ($State -eq "authorization_required") {
                "SmartThings authorization needs renewal."
            } elseif ($State -eq "stopped") {
                "The background controller is stopped."
            } else {
                "The background controller reported an error. Open the log for details."
            }
            Show-TrayError $Detail
        }
        $script:LastState = $State
    }

    $Timer = New-Object System.Windows.Forms.Timer
    $Timer.Interval = 3000
    $Timer.Add_Tick({ Update-TrayStatus })
    Update-TrayStatus
    $Timer.Start()
    [System.Windows.Forms.Application]::Run()
} finally {
    if ($Timer) { $Timer.Stop(); $Timer.Dispose() }
    if ($Notify) { $Notify.Visible = $false; $Notify.Dispose() }
    if ($Menu) { $Menu.Dispose() }
    try {
        if ((Test-Path -LiteralPath $TrayPidPath) -and
            ([int](Get-Content -LiteralPath $TrayPidPath)) -eq $PID) {
            Remove-Item -LiteralPath $TrayPidPath -Force -ErrorAction SilentlyContinue
        }
    } catch {}
    if ($HasTrayMutex) { $TrayMutex.ReleaseMutex() }
    $TrayMutex.Dispose()
}
