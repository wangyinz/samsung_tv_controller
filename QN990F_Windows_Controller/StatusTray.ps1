$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
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

public static class TVFlyoutNative {
    [StructLayout(LayoutKind.Sequential)]
    public struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)]
    private struct Point { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)]
    private struct MonitorInfo {
        public int Size;
        public Rect Monitor, Work;
        public uint Flags;
    }
    [DllImport("user32.dll")] private static extern bool GetCursorPos(out Point point);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
    [DllImport("user32.dll")] private static extern IntPtr MonitorFromPoint(Point point, uint flags);
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    private static extern bool GetMonitorInfo(IntPtr monitor, ref MonitorInfo info);
    [DllImport("user32.dll")]
    private static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);

    public static void Style(IntPtr hwnd, bool dark) {
        int rounded = 2, darkMode = dark ? 1 : 0;
        // Unsupported DWM attributes are ignored on older Windows versions.
        DwmSetWindowAttribute(hwnd, 33, ref rounded, sizeof(int));
        DwmSetWindowAttribute(hwnd, 20, ref darkMode, sizeof(int));
    }

    public static Rect Placement(Rect area, int x, int y, int width, int height) {
        int left = Math.Max(area.Left + 8, Math.Min(x - width, area.Right - width - 8));
        int top = Math.Max(area.Top + 8, Math.Min(y - height - 8, area.Bottom - height - 8));
        return new Rect { Left = left, Top = top, Right = left + width, Bottom = top + height };
    }

    private static Point anchor;
    private static Rect workArea;
    private static bool hasAnchor;

    public static void CaptureAnchor() {
        hasAnchor = false;
        if (!GetCursorPos(out anchor)) { return; }
        var info = new MonitorInfo { Size = Marshal.SizeOf(typeof(MonitorInfo)) };
        if (!GetMonitorInfo(MonitorFromPoint(anchor, 2), ref info)) { return; }
        workArea = info.Work;
        hasAnchor = true;
    }

    public static void Position(IntPtr hwnd) {
        Rect window;
        if (!hasAnchor || !GetWindowRect(hwnd, out window)) { return; }
        var target = Placement(workArea, anchor.X, anchor.Y, window.Right - window.Left, window.Bottom - window.Top);
        // Native coordinates keep the flyout inside the clicked monitor's work area.
        SetWindowPos(hwnd, IntPtr.Zero, target.Left, target.Top, 0, 0, 0x15);
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

    $XamlPath = Join-Path $PSScriptRoot "StatusFlyout.xaml"
    $XamlReader = [System.Xml.XmlReader]::Create($XamlPath)
    try { $Flyout = [System.Windows.Markup.XamlReader]::Load($XamlReader) }
    finally { $XamlReader.Dispose() }
    $ControllerItem = $Flyout.FindName("ControllerItem")
    $VolumeItem = $Flyout.FindName("VolumeItem")
    $TVVolumeLabel = $Flyout.FindName("TVVolumeLabel")
    $VolumeSlider = $Flyout.FindName("VolumeSlider")
    $VolumeNotice = $Flyout.FindName("VolumeNotice")
    $ConfigureItem = $Flyout.FindName("ConfigureItem")
    $ReauthorizeItem = $Flyout.FindName("ReauthorizeItem")
    $RestartItem = $Flyout.FindName("RestartItem")
    $LogItem = $Flyout.FindName("LogItem")
    $ExitItem = $Flyout.FindName("ExitItem")
    $StatusPanel = $Flyout.FindName("StatusPanel")
    $ActionsPanel = $Flyout.FindName("ActionsPanel")
    $WindowHelper = New-Object System.Windows.Interop.WindowInteropHelper($Flyout)
    $WindowHandle = $WindowHelper.EnsureHandle()
    $script:Exiting = $false

    function Set-FlyoutTheme {
        $ThemePath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        $Personalize = Get-ItemProperty -LiteralPath $ThemePath -ErrorAction SilentlyContinue
        $Dark = $null -ne $Personalize -and $Personalize.AppsUseLightTheme -eq 0
        if ([System.Windows.SystemParameters]::HighContrast) {
            $Colors = @{
                SurfaceBrush = [System.Windows.SystemColors]::WindowColor
                TextBrush = [System.Windows.SystemColors]::WindowTextColor
                SecondaryBrush = [System.Windows.SystemColors]::WindowTextColor
                BorderBrush = [System.Windows.SystemColors]::WindowTextColor
                TrackBrush = [System.Windows.SystemColors]::GrayTextColor
                AccentBrush = [System.Windows.SystemColors]::HighlightColor
                HoverBrush = [System.Windows.SystemColors]::ControlColor
                PressedBrush = [System.Windows.SystemColors]::ControlDarkColor
                ThumbBrush = [System.Windows.SystemColors]::WindowColor
            }
        } elseif ($Dark) {
            $Colors = @{
                SurfaceBrush = "#292929"; TextBrush = "#FAFAFA"; SecondaryBrush = "#C7C7C7"
                BorderBrush = "#4A4A4A"; TrackBrush = "#999999"; AccentBrush = "#4CC2FF"
                HoverBrush = "#10FFFFFF"; PressedBrush = "#20FFFFFF"; ThumbBrush = "#454545"
            }
        } else {
            $Colors = @{
                SurfaceBrush = "#F3F3F3"; TextBrush = "#1B1B1B"; SecondaryBrush = "#606060"
                BorderBrush = "#D5D5D5"; TrackBrush = "#8A8A8A"; AccentBrush = "#0067C0"
                HoverBrush = "#10000000"; PressedBrush = "#18000000"; ThumbBrush = "#FFFFFF"
            }
        }
        foreach ($Name in $Colors.Keys) {
            $Color = [System.Windows.Media.ColorConverter]::ConvertFromString([string]$Colors[$Name])
            $Flyout.Resources[$Name] = New-Object System.Windows.Media.SolidColorBrush($Color)
        }
        [TVFlyoutNative]::Style($WindowHandle, $Dark)
    }

    function Show-Flyout([bool]$FullMenu) {
        [TVFlyoutNative]::CaptureAnchor()
        Update-TrayStatus
        Set-FlyoutTheme
        $PanelVisibility = if ($FullMenu) { "Visible" } else { "Collapsed" }
        $StatusPanel.Visibility = $PanelVisibility
        $ActionsPanel.Visibility = $PanelVisibility
        $Flyout.Opacity = 0
        $Flyout.Show()
        $Flyout.UpdateLayout()
        [TVFlyoutNative]::Position($WindowHandle)
        $Flyout.UpdateLayout()
        [TVFlyoutNative]::Position($WindowHandle)
        $Flyout.Opacity = 1
        [void][TVFlyoutNative]::SetForegroundWindow($WindowHandle)
        [void]$Flyout.Activate()
        if ($VolumeSlider.IsEnabled) { [void]$VolumeSlider.Focus() }
        elseif ($FullMenu) { [void]$ConfigureItem.Focus() }
    }

    function Hide-Flyout {
        $script:VolumeDragging = $false
        $script:VolumeEditing = $false
        Submit-Volume
        $Flyout.Hide()
    }

    $Flyout.Add_Deactivated({
        if ($Flyout.IsVisible -and -not $script:Exiting) { Hide-Flyout }
    })
    $Flyout.Add_PreviewKeyDown({
        param($Source, $EventArgs)
        if ($EventArgs.Key -eq [System.Windows.Input.Key]::Escape) {
            $EventArgs.Handled = $true
            Hide-Flyout
        }
    })
    $Flyout.Add_Closing({
        param($Source, $EventArgs)
        if (-not $script:Exiting) {
            $EventArgs.Cancel = $true
            Hide-Flyout
        }
    })
    $Flyout.Add_SizeChanged({
        if ($Flyout.IsVisible) { [TVFlyoutNative]::Position($WindowHandle) }
    })

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
        if (-not $VolumeSlider.IsEnabled -or -not $script:VolumeSession -or
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
            $VolumeNotice.Text = "Setting TV volume to $([int]$VolumeSlider.Value)..."
        } catch { Show-TrayError $_.Exception.Message }
    }
    # Observe the tunneling event at the window BEFORE Slider's class handler
    # changes Value for a track click. Do not send cloud requests while dragging.
    $Flyout.Add_PreviewMouseDown({
        param($Source, $EventArgs)
        if ($EventArgs.ChangedButton -eq [System.Windows.Input.MouseButton]::Left -and
            $VolumeSlider.IsMouseOver) {
            $script:VolumeDragging = $true
        }
    })
    $Flyout.Add_PreviewMouseUp({
        param($Source, $EventArgs)
        if ($EventArgs.ChangedButton -eq [System.Windows.Input.MouseButton]::Left) {
            $script:VolumeDragging = $false
            Submit-Volume
        }
    })
    $VolumeSlider.AddHandler(
        [System.Windows.Controls.Primitives.Thumb]::DragCompletedEvent,
        [System.Windows.Controls.Primitives.DragCompletedEventHandler]{
            $script:VolumeDragging = $false
            Submit-Volume
        }, $true
    )
    $VolumeSlider.Add_PreviewKeyDown({
        param($Source, $EventArgs)
        if ($EventArgs.Key.ToString() -in @("Left", "Right", "Up", "Down", "Home", "End", "PageUp", "PageDown")) {
            $script:VolumeEditing = $true
        }
    })
    $VolumeSlider.Add_PreviewKeyUp({
        $script:VolumeEditing = $false
        Submit-Volume
    })
    $VolumeSlider.Add_LostKeyboardFocus({
        $script:VolumeEditing = $false
        Submit-Volume
    })
    $VolumeSlider.Add_ValueChanged({
        if ($script:VolumeSyncing) { return }
        $script:VolumeDirty = $true
        $TVVolumeLabel.Text = [string][int]$VolumeSlider.Value
        if (-not $script:VolumeDragging -and -not $script:VolumeEditing) {
            Submit-Volume
        }
    })

    function Update-VolumeStatus([bool]$ControllerRunning, [int]$ControllerPid) {
        if ($script:VolumeDragging -or $script:VolumeEditing) { return }
        try {
            $Info = Get-Content -LiteralPath $VolumeStatusPath -Raw | ConvertFrom-Json
        } catch {
            $VolumeSlider.IsEnabled = $false
            $TVVolumeLabel.Text = [string][char]0x2014
            $VolumeNotice.Text = "Start or update the controller to use the slider."
            return
        }
        $Available = $ControllerRunning -and [bool]$Info.available -and
            ([int]$Info.pid -eq $ControllerPid)
        $VolumeSlider.IsEnabled = $Available
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
            $TVVolumeLabel.Text = [string][int]$VolumeSlider.Value
        } else {
            $TVVolumeLabel.Text = [string][char]0x2014
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
        Hide-Flyout
        try { Start-ControllerHelper $ConfigurePath }
        catch { Show-TrayError $_.Exception.Message }
    })
    $ReauthorizeItem.Add_Click({
        Hide-Flyout
        try { Start-ControllerHelper $ReauthorizePath }
        catch { Show-TrayError $_.Exception.Message }
    })
    $LogItem.Add_Click({
        Hide-Flyout
        try {
            if (-not (Test-Path -LiteralPath $LogPath)) {
                throw "The controller log does not exist yet."
            }
            Start-Process notepad.exe -ArgumentList @("`"$LogPath`"")
        } catch { Show-TrayError $_.Exception.Message }
    })
    $RestartItem.Add_Click({
        Hide-Flyout
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
            $script:Exiting = $true
            [System.Windows.Threading.Dispatcher]::CurrentDispatcher.InvokeShutdown()
        } catch { Show-TrayError $_.Exception.Message }
    })
    $Notify.Add_MouseClick({
        param($Source, $EventArgs)
        try {
            if ($EventArgs.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
                Show-Flyout $false
            } elseif ($EventArgs.Button -eq [System.Windows.Forms.MouseButtons]::Right) {
                Show-Flyout $true
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
            "Keyboard volume: enabled"
        } else {
            "Keyboard volume: disabled"
        }
        $ReauthorizeItem.Visibility = if ($ControlMethod -eq "smartthings") { "Visible" } else { "Collapsed" }
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

    $Timer = New-Object System.Windows.Threading.DispatcherTimer
    $Timer.Interval = [TimeSpan]::FromSeconds(1)
    $Timer.Add_Tick({ Update-TrayStatus })
    Update-TrayStatus
    $Timer.Start()
    [System.Windows.Threading.Dispatcher]::Run()
} finally {
    if ($Timer) { $Timer.Stop() }
    if ($Notify) { $Notify.Visible = $false; $Notify.Dispose() }
    $script:Exiting = $true
    if ($Flyout) { $Flyout.Close() }
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
