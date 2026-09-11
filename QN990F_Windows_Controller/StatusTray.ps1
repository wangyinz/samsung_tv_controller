$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;

public static class TVStatusIcon {
    [DllImport("user32.dll")]
    private static extern bool DestroyIcon(IntPtr icon);

    public static Icon Create(string badge, Color badgeColor) {
        using (var bitmap = new Bitmap(32, 32))
        using (var graphics = Graphics.FromImage(bitmap))
        using (var border = new Pen(Color.White, 2))
        using (var font = new Font("Segoe UI", 17, FontStyle.Bold, GraphicsUnit.Pixel))
        using (var badgeFont = new Font("Segoe UI", 12, FontStyle.Bold, GraphicsUnit.Pixel))
        using (var badgeBrush = new SolidBrush(badgeColor))
        using (var format = new StringFormat()) {
            graphics.SmoothingMode = SmoothingMode.AntiAlias;
            graphics.FillRectangle(Brushes.Black, 1, 3, 29, 24);
            graphics.DrawRectangle(border, 1, 3, 29, 24);
            format.Alignment = StringAlignment.Center;
            format.LineAlignment = StringAlignment.Center;
            graphics.DrawString("TV", font, Brushes.White, new RectangleF(1, 3, 29, 24), format);
            if (badge.Length > 0) {
                graphics.FillEllipse(badgeBrush, 18, 18, 14, 14);
                graphics.DrawString(badge, badgeFont, Brushes.Black, new RectangleF(18, 17, 14, 14), format);
            }
            var handle = bitmap.GetHicon();
            try {
                using (var icon = Icon.FromHandle(handle)) { return (Icon)icon.Clone(); }
            } finally { DestroyIcon(handle); }
        }
    }
}
'@

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
$VolumeStatusPath = Join-Path $AppDir "volume-status.json"
$VolumeRequestPath = Join-Path $AppDir "volume-request.json"

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
    $ReadyIcon = [TVStatusIcon]::Create("", [System.Drawing.Color]::Transparent)
    $AttentionIcon = [TVStatusIcon]::Create("!", [System.Drawing.Color]::Gold)
    $StoppedIcon = [TVStatusIcon]::Create("x", [System.Drawing.Color]::Tomato)
    $Notify.Icon = $ReadyIcon
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

    $TVVolumeLabel = $Menu.Items.Add("TV volume: loading")
    $TVVolumeLabel.Enabled = $false
    $VolumeSlider = New-Object System.Windows.Forms.TrackBar
    $VolumeSlider.Minimum = 0
    $VolumeSlider.Maximum = 100
    $VolumeSlider.SmallChange = 1
    $VolumeSlider.LargeChange = 10
    $VolumeSlider.TickStyle = [System.Windows.Forms.TickStyle]::None
    $VolumeSlider.AutoSize = $false
    $VolumeSlider.Size = New-Object System.Drawing.Size(240, 32)
    $VolumeSlider.AccessibleName = "TV volume"
    $VolumeSlider.Enabled = $false
    $SliderHost = New-Object System.Windows.Forms.ToolStripControlHost($VolumeSlider)
    $SliderHost.AutoSize = $false
    $SliderHost.Size = $VolumeSlider.Size
    [void]$Menu.Items.Add($SliderHost)
    $VolumeNotice = $Menu.Items.Add("")
    $VolumeNotice.Enabled = $false
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

    $script:VolumeSession = ""
    $script:PendingVolumeId = ""
    $script:PendingVolumeTime = [DateTime]::MinValue
    $script:VolumeDragging = $false
    $script:VolumeEditing = $false
    $script:VolumeSyncing = $false
    $script:VolumeDirty = $false
    function Submit-Volume {
        if (-not $VolumeSlider.Enabled -or -not $script:VolumeSession -or
            -not $script:VolumeDirty) { return }
        $RequestId = [Guid]::NewGuid().ToString("N")
        $Payload = @{
            session = $script:VolumeSession
            id = $RequestId
            value = [int]$VolumeSlider.Value
            created_at = ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0)
        } | ConvertTo-Json -Compress
        $TemporaryPath = "$VolumeRequestPath.tmp"
        try {
            [IO.File]::WriteAllText($TemporaryPath, $Payload)
            if ([IO.File]::Exists($VolumeRequestPath)) {
                [IO.File]::Replace($TemporaryPath, $VolumeRequestPath, [NullString]::Value)
            } else {
                [IO.File]::Move($TemporaryPath, $VolumeRequestPath)
            }
            $script:PendingVolumeId = $RequestId
            $script:PendingVolumeTime = [DateTime]::UtcNow
            $script:VolumeDirty = $false
            $VolumeNotice.Text = "Setting TV volume to $($VolumeSlider.Value)..."
        } catch { Show-TrayError $_.Exception.Message }
    }
    $VolumeSlider.Add_MouseDown({
        param($Source, $EventArgs)
        if ($EventArgs.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
            $script:VolumeDragging = $true
        }
    })
    $VolumeSlider.Add_MouseUp({
        $script:VolumeDragging = $false
        Submit-Volume
    })
    $VolumeSlider.Add_KeyDown({
        param($Source, $EventArgs)
        if ($EventArgs.KeyCode.ToString() -in @("Left", "Right", "Up", "Down", "Home", "End", "PageUp", "PageDown")) {
            $script:VolumeEditing = $true
        }
    })
    $VolumeSlider.Add_KeyUp({
        $script:VolumeEditing = $false
        Submit-Volume
    })
    $VolumeSlider.Add_Scroll({
        if ($script:VolumeSyncing) { return }
        $script:VolumeDirty = $true
        $TVVolumeLabel.Text = "TV volume: $($VolumeSlider.Value) / 100"
        if (-not $script:VolumeDragging -and -not $script:VolumeEditing) {
            Submit-Volume
        }
    })

    function Update-VolumeStatus([bool]$ControllerRunning, [int]$ControllerPid) {
        if ($script:VolumeDragging -or $script:VolumeEditing) { return }
        try {
            $Info = Get-Content -LiteralPath $VolumeStatusPath -Raw | ConvertFrom-Json
        } catch {
            $VolumeSlider.Enabled = $false
            $TVVolumeLabel.Text = "TV volume: unavailable"
            $VolumeNotice.Text = "Start or update the controller to use the slider."
            return
        }
        $Available = $ControllerRunning -and [bool]$Info.available -and
            ([int]$Info.pid -eq $ControllerPid)
        $VolumeSlider.Enabled = $Available
        if ([string]$Info.session -ne $script:VolumeSession) {
            $script:VolumeSession = [string]$Info.session
            $script:PendingVolumeId = ""
        }
        if ($script:PendingVolumeId) {
            if ([string]$Info.request_id -eq $script:PendingVolumeId) {
                $script:PendingVolumeId = ""
            } elseif ($Available -and
                ([DateTime]::UtcNow - $script:PendingVolumeTime).TotalSeconds -lt 60) {
                return
            } else {
                $script:PendingVolumeId = ""
                $VolumeNotice.Text = "No response; restart the controller and try again."
                return
            }
        }
        if ($null -ne $Info.value) {
            $script:VolumeSyncing = $true
            try { $VolumeSlider.Value = [Math]::Min(100, [Math]::Max(0, [int]$Info.value)) }
            finally { $script:VolumeSyncing = $false }
            $TVVolumeLabel.Text = "TV volume: $($VolumeSlider.Value) / 100"
        } else {
            $TVVolumeLabel.Text = "TV volume: unknown"
        }
        $VolumeNotice.Text = [string]$Info.message
        if (-not $Available -and -not $VolumeNotice.Text) {
            $VolumeNotice.Text = "Controller is stopped."
        }
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
        $StatusPid = 0
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
        Update-VolumeStatus $Running $StatusPid

        $NeedsAttention = $State -in @("authorization_required", "error", "stopped")
        $Notify.Icon = if ($State -eq "stopped" -or -not $Running) {
            $StoppedIcon
        } elseif ($NeedsAttention) {
            $AttentionIcon
        } else {
            $ReadyIcon
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
    $Timer.Interval = 1000
    $Timer.Add_Tick({ Update-TrayStatus })
    $Menu.Add_Opening({ Update-TrayStatus })
    Update-TrayStatus
    $Timer.Start()
    [System.Windows.Forms.Application]::Run()
} finally {
    if ($Timer) { $Timer.Stop(); $Timer.Dispose() }
    if ($Notify) { $Notify.Visible = $false; $Notify.Dispose() }
    if ($Menu) { $Menu.Dispose() }
    if ($ReadyIcon) { $ReadyIcon.Dispose() }
    if ($AttentionIcon) { $AttentionIcon.Dispose() }
    if ($StoppedIcon) { $StoppedIcon.Dispose() }
    try {
        if ((Test-Path -LiteralPath $TrayPidPath) -and
            ([int](Get-Content -LiteralPath $TrayPidPath)) -eq $PID) {
            Remove-Item -LiteralPath $TrayPidPath -Force -ErrorAction SilentlyContinue
        }
    } catch {}
    if ($HasTrayMutex) { $TrayMutex.ReleaseMutex() }
    $TrayMutex.Dispose()
}
