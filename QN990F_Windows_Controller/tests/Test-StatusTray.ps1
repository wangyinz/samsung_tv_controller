param([switch]$RequireWpf)

# No installed state is read and no real controller or TV is contacted.
$ErrorActionPreference = "Stop"
$SourceDir = Split-Path -Parent $PSScriptRoot
$SourcePath = Join-Path $SourceDir "StatusTray.ps1"
$ParseErrors = $null
$Ast = [System.Management.Automation.Language.Parser]::ParseFile($SourcePath, [ref]$null, [ref]$ParseErrors)
if ($ParseErrors.Count) { throw ($ParseErrors | Out-String) }

$script:Assertions = 0
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:Assertions++
}

# Compile the actual native placement helper; only its pure geometry is used off Windows.
$NativeLiteral = $Ast.Find({
    param($Node)
    $Node -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
        $Node.Value.Contains("public static class TVFlyoutNative")
}, $true)
$NativeSource = "using System; using System.Runtime.InteropServices; " +
    $NativeLiteral.Value.Substring($NativeLiteral.Value.IndexOf("public static class TVFlyoutNative"))
Add-Type -TypeDefinition $NativeSource
Add-Type -TypeDefinition @'
using System;
using System.Collections;
public static class TrayResourceAssertions {
    public static bool HasType(IDictionary resources, string name, Type expected) {
        // Deliberately inspect inside CLR: PowerShell's -is unwraps PSObject.
        return expected.IsInstanceOfType(resources[name]);
    }
}
'@
$Probe = @{}
$Probe["wrapped"] = New-Object System.Text.StringBuilder
$Probe["raw"] = [System.Text.StringBuilder]::new()
Assert-True ($Probe["wrapped"] -is [System.Text.StringBuilder]) "Regression fixture reproduces PowerShell unwrapping"
Assert-True (-not [TrayResourceAssertions]::HasType($Probe, "wrapped", [System.Text.StringBuilder])) "Reject wrapped resources"
Assert-True ([TrayResourceAssertions]::HasType($Probe, "raw", [System.Text.StringBuilder])) "Accept raw CLR resources"

foreach ($Case in @(
    @(0, 0, 1920, 1040, 1850, 1060, 360, 140),
    @(0, 0, 1920, 1040, 2, 2, 360, 500),
    @(-1920, 0, 0, 1040, -15, 1060, 360, 500),
    @(1920, -2160, 5760, 0, 5500, -20, 720, 1000),
    @(0, 0, 1920, 1040, 1850, 1060, 1080, 1200),
    @(-1280, 0, 0, 680, -10, 700, 3000, 3000),
    @(0, 0, 4, 4, 0, 0, 360, 500)
)) {
    $Area = New-Object TVFlyoutNative+Rect
    $Area.Left = $Case[0]; $Area.Top = $Case[1]; $Area.Right = $Case[2]; $Area.Bottom = $Case[3]
    $Rect = [TVFlyoutNative]::Placement($Area, $Case[4], $Case[5], $Case[6], $Case[7])
    $Inner = [TVFlyoutNative]::InsetArea($Area)
    Assert-True ($Rect.Left -ge $Inner.Left -and $Rect.Right -le $Inner.Right) "Horizontal work-area bounds"
    Assert-True ($Rect.Top -ge $Inner.Top -and $Rect.Bottom -le $Inner.Bottom) "Vertical work-area bounds"
}

Assert-True ([TVFlyoutNative]::OpacityAt(0, 255, 0, 180) -eq 0) "Fade starts transparent"
Assert-True ([TVFlyoutNative]::OpacityAt(0, 255, 180, 180) -eq 255) "Fade finishes opaque"
Assert-True ([TVFlyoutNative]::OpacityAt(255, 0, 120, 120) -eq 0) "Close fade finishes transparent"
Assert-True ([TVFlyoutNative]::OpacityAt(60, 255, 0, 180) -eq 60) "Reopening starts from current opacity"
Assert-True ([TVFlyoutNative]::OpacityAt(0, 255, 1000, 180) -eq 255) "Late timer tick cannot overshoot"
Assert-True ([TVFlyoutNative]::OpacityAt(0, 255, 0, 0) -eq 255) "Disabled-duration fade completes immediately"
$Previous = 0
foreach ($Elapsed in 0..180) {
    $Alpha = [TVFlyoutNative]::OpacityAt(0, 255, $Elapsed, 180)
    if ($Alpha -lt $Previous -or $Alpha -gt 255) { throw "Non-monotonic opacity" }
    $Previous = $Alpha
}
Assert-True ($Previous -eq 255) "Fade is bounded and monotonic"

# Execute the implementation's functions, not copies of the volume protocol.
foreach ($Name in @(
    "Submit-Volume", "Update-VolumeStatus", "Complete-VolumeDrag", "Set-VolumeFromPointer",
    "Set-FlyoutTheme", "Set-FlyoutBounds", "Start-FlyoutFade", "Complete-FlyoutFade"
)) {
    $Definition = $Ast.Find({
        param($Node)
        $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $Node.Name -eq $Name
    }, $true)
    . ([scriptblock]::Create($Definition.Extent.Text))
}
function Show-TrayError([string]$Message) { throw $Message }

$WithWpf = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
if ($RequireWpf -and -not $WithWpf) { throw "WPF checks require Windows PowerShell -Sta." }
$TestDir = Join-Path ([IO.Path]::GetTempPath()) ("tv-flyout-test-" + [Guid]::NewGuid().ToString("N"))
[void][IO.Directory]::CreateDirectory($TestDir)
try {
    $VolumeStatusPath = Join-Path $TestDir "volume-status.json"
    $VolumeRequestPath = Join-Path $TestDir "volume-request.json"
    $VolumeSlider = [pscustomobject]@{ IsEnabled = $false; Value = 0 }
    $TVVolumeLabel = [pscustomobject]@{ Text = "" }
    $VolumeNotice = [pscustomobject]@{ Text = "" }
    $Flyout = $null
    $ControlNames = @(
        "ControllerItem", "VolumeItem", "TVVolumeLabel", "VolumeSlider", "VolumeNotice",
        "ConfigureItem", "ReauthorizeItem", "RestartItem", "LogItem", "ExitItem", "StatusPanel", "ActionsPanel", "FlyoutScroll"
    )
    if ($WithWpf) {
        if ([Threading.Thread]::CurrentThread.ApartmentState -ne "STA") { throw "Run with -Sta." }
        Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
        $Reader = [Xml.XmlReader]::Create((Join-Path $SourceDir "StatusFlyout.xaml"))
        try { $Flyout = [Windows.Markup.XamlReader]::Load($Reader) }
        finally { $Reader.Dispose() }
        foreach ($Name in $ControlNames) {
            Set-Variable -Name $Name -Value $Flyout.FindName($Name)
            Assert-True ($null -ne (Get-Variable -Name $Name -ValueOnly)) "Missing XAML control: $Name"
        }
        $WindowHelper = New-Object Windows.Interop.WindowInteropHelper($Flyout)
        $WindowHandle = $WindowHelper.EnsureHandle()
        $StyleReader = [TVFlyoutNative].GetMethod("ExtendedStyle", [Reflection.BindingFlags]"Static,NonPublic")
        $OriginalStyle = $StyleReader.Invoke($null, @($WindowHandle))
        try {
            if ([TVFlyoutNative]::SetOpacity($WindowHandle, 128)) {
                $FadingStyle = $StyleReader.Invoke($null, @($WindowHandle))
                Assert-True (($FadingStyle -band 0x80000) -ne 0) "Native fade uses a temporary layered style"
                Assert-True ([TVFlyoutNative]::SetOpacity($WindowHandle, 255)) "Native fade can finish fully opaque"
            } else {
                Write-Host "SKIP: native alpha unavailable; production falls back to immediate visibility."
            }
        } finally {
            [TVFlyoutNative]::ResetOpacity($WindowHandle)
        }
        Assert-True ($StyleReader.Invoke($null, @($WindowHandle)) -eq $OriginalStyle) "Native window style is restored after fading"
        Set-FlyoutTheme
        foreach ($Name in @(
            "SurfaceBrush", "TextBrush", "SecondaryBrush", "BorderBrush", "TrackBrush",
            "AccentBrush", "HoverBrush", "PressedBrush", "ThumbBrush"
        )) {
            Assert-True ([TrayResourceAssertions]::HasType($Flyout.Resources, $Name, [Windows.Media.SolidColorBrush])) `
                "Theme resource must remain a SolidColorBrush: $Name"
        }
        $Flyout.Resources["RegressionProbe"] = New-Object Windows.Media.SolidColorBrush([Windows.Media.Colors]::Red)
        Assert-True (-not [TrayResourceAssertions]::HasType($Flyout.Resources, "RegressionProbe", [Windows.Media.SolidColorBrush])) "Real ResourceDictionary must reject a wrapped brush"
        $Flyout.Resources.Remove("RegressionProbe")
        [TVFlyoutNative]::CaptureAnchor()
        Set-FlyoutBounds
        Assert-True ($Flyout.MaxHeight -gt 0 -and -not [double]::IsInfinity($Flyout.MaxHeight)) "Flyout height follows monitor bounds"
        [void]$VolumeSlider.ApplyTemplate()
        $Track = $VolumeSlider.Template.FindName("PART_Track", $VolumeSlider)
        Assert-True ($null -ne $Track.Thumb) "The slider must have a usable thumb"
        Assert-True ($Track.DecreaseRepeatButton.Command -eq [Windows.Controls.Slider]::DecreaseLarge) "Decrease track command"
        foreach ($Full in @($true, $false)) {
            $StatusPanel.Visibility = if ($Full) { "Visible" } else { "Collapsed" }
            $ActionsPanel.Visibility = $StatusPanel.Visibility
            $Flyout.Content.Measure([Windows.Size]::new(360, [double]::PositiveInfinity))
            $Flyout.Content.Arrange([Windows.Rect]::new(0, 0, 360, $Flyout.Content.DesiredSize.Height))
            $Flyout.Content.UpdateLayout()
            Assert-True ($VolumeSlider.ActualWidth -gt 200) "Slider has enough space in each view"
            if ($Full) { $FullHeight = $Flyout.Content.DesiredSize.Height }
            else { Assert-True ($Flyout.Content.DesiredSize.Height -lt $FullHeight) "Left-click view must be volume-only" }
        }
        $StatusPanel.Visibility = "Visible"
        $ActionsPanel.Visibility = "Visible"
        $Flyout.Content.Measure([Windows.Size]::new(260, 180))
        $Flyout.Content.Arrange([Windows.Rect]::new(0, 0, 260, 180))
        $Flyout.Content.UpdateLayout()
        Assert-True ($FlyoutScroll.ScrollableHeight -gt 0) "Small viewport exposes scrolling"
        $FlyoutScroll.ScrollToEnd()
        $Flyout.Content.UpdateLayout()
        Assert-True ($FlyoutScroll.VerticalOffset -gt 0) "Bottom actions remain reachable"
        # Wire the real ValueChanged handler to catch accidental sync->request loops.
        $Hook = $Ast.Find({
            param($Node)
            $Node -is [System.Management.Automation.Language.InvokeMemberExpressionAst] -and
                $Node.Expression.Extent.Text -eq '$VolumeSlider' -and $Node.Member.Value -eq "Add_ValueChanged"
        }, $true)
        . ([scriptblock]::Create($Hook.Extent.Text))
    } else {
        [xml]$Xaml = Get-Content -LiteralPath (Join-Path $SourceDir "StatusFlyout.xaml") -Raw
        Assert-True ($Xaml.DocumentElement.LocalName -eq "Window") "Valid XAML XML"
        foreach ($Name in $ControlNames) {
            $Element = $Xaml.SelectSingleNode("//*[@*[local-name()='Name' and .='$Name']]")
            Assert-True ($null -ne $Element) "Missing XAML control: $Name"
        }
        Write-Host "SKIP: WPF loading/rendering requires Windows; testing protocol with fake controls."
    }

    $script:VolumeSession = ""
    $script:PendingVolumeId = ""
    $script:PendingVolumeTime = [DateTime]::MinValue
    $script:VolumeDragging = $false
    $script:VolumeEditing = $false
    $script:VolumeSyncing = $false
    $script:VolumeDirty = $false
    $Status = @{ session = "session-1"; pid = 123; available = $true; value = 14; request_id = ""; message = "" }
    function Write-TestStatus { [IO.File]::WriteAllText($VolumeStatusPath, ($Status | ConvertTo-Json -Compress)) }
    Write-TestStatus
    Update-VolumeStatus $true 123
    Assert-True ($VolumeSlider.IsEnabled -and $VolumeSlider.Value -eq 14) "Initial TV volume"
    Assert-True (-not (Test-Path -LiteralPath $VolumeRequestPath)) "Status synchronization must not submit requests"

    foreach ($Value in @(0, 100, 37)) {
        $script:VolumeDragging = $true
        $VolumeSlider.Value = $Value
        $script:VolumeDirty = $true
        Update-VolumeStatus $true 123
        Assert-True ($VolumeSlider.Value -eq $Value) "Polling must not move the thumb during a drag"
        $PreviousId = $script:PendingVolumeId
        if ($PreviousId) {
            $Previous = Get-Content -LiteralPath $VolumeRequestPath -Raw | ConvertFrom-Json
            Assert-True ($Previous.id -eq $PreviousId) "Dragging must not submit intermediate targets"
        } else {
            Assert-True (-not (Test-Path -LiteralPath $VolumeRequestPath)) "First drag must wait for release"
        }
        $script:VolumeDragging = $false
        Submit-Volume
        $Request = Get-Content -LiteralPath $VolumeRequestPath -Raw | ConvertFrom-Json
        Assert-True ($Request.value -eq $Value -and $Request.session -eq "session-1") "Exact target and session"
        Assert-True ($Request.id -ne $PreviousId -and $Request.id -eq $script:PendingVolumeId) "Unique pending ID"
        Assert-True (-not (Test-Path -LiteralPath "$VolumeRequestPath.tmp")) "Atomic write must consume temporary file"
        Update-VolumeStatus $true 123
        Assert-True ($VolumeSlider.Value -eq $Value) "Old status must not replace a pending target"
    }
    $Status.value = 37; $Status.request_id = $Request.id
    Write-TestStatus
    Update-VolumeStatus $true 123
    Assert-True (-not $script:PendingVolumeId -and $TVVolumeLabel.Text -eq "37") "Matching acknowledgement"

    $Status.message = "TV volume command failed."
    Write-TestStatus
    Update-VolumeStatus $true 123
    Assert-True ($VolumeNotice.Text -eq $Status.message) "Failures must remain visible"
    $Status.session = "session-2"; $Status.value = 20; $Status.message = ""
    $script:PendingVolumeId = "old-session-request"
    Write-TestStatus
    Update-VolumeStatus $true 123
    Assert-True ($script:VolumeSession -eq "session-2" -and -not $script:PendingVolumeId) "Restart clears old pending state"

    $script:VolumeEditing = $true
    $VolumeSlider.Value = 45
    $script:VolumeDirty = $true
    Update-VolumeStatus $true 123
    Assert-True ($VolumeSlider.Value -eq 45) "Polling must not move the thumb during keyboard editing"
    $script:VolumeEditing = $false
    Submit-Volume
    $script:PendingVolumeTime = [DateTime]::UtcNow.AddSeconds(-61)
    Update-VolumeStatus $true 123
    Assert-True ($VolumeNotice.Text -like "No response*" -and -not $script:PendingVolumeId) "Expired pending requests"

    Update-VolumeStatus $false 123
    Assert-True (-not $VolumeSlider.IsEnabled) "Stopped controller disables the slider"
    $Before = [IO.File]::ReadAllText($VolumeRequestPath)
    $script:VolumeDirty = $true
    Submit-Volume
    Assert-True ([IO.File]::ReadAllText($VolumeRequestPath) -eq $Before) "Disabled slider must not submit"
    Update-VolumeStatus $true 999
    Assert-True (-not $VolumeSlider.IsEnabled) "Stale PID disables the slider"
    $Status.available = $false; $Status.message = "Exact volume requires SmartThings."
    Write-TestStatus
    Update-VolumeStatus $true 123
    Assert-True (-not $VolumeSlider.IsEnabled -and $VolumeNotice.Text -eq $Status.message) "Unsupported connection explanation"

    Remove-Item -LiteralPath $VolumeStatusPath
    Update-VolumeStatus $true 123
    Assert-True (-not $VolumeSlider.IsEnabled -and $VolumeNotice.Text -like "Start or update*") "Missing status is safe"

    # Run the actual input handlers with a fake capture owner. No system input
    # is injected, even on Windows; requests stay in this test's temp directory.
    if (-not $WithWpf) {
        Add-Type -TypeDefinition @'
namespace System.Windows.Input {
    public enum MouseButton { Left, Middle, Right }
    public enum MouseButtonState { Released, Pressed }
}
'@
    }
    function Source-Handler([string]$Target, [string]$Member) {
        $Hook = $Ast.Find({ param($Node)
            $Node -is [System.Management.Automation.Language.InvokeMemberExpressionAst] -and
            $Node.Expression.Extent.Text -eq $Target -and $Node.Member.Value -eq $Member
        }, $true)
        $Block = $Hook.Find({ param($Node)
            $Node -is [System.Management.Automation.Language.ScriptBlockExpressionAst]
        }, $true)
        return $Block.ScriptBlock.GetScriptBlock()
    }
    $Down = Source-Handler '$Flyout' 'Add_PreviewMouseDown'
    $Up = Source-Handler '$Flyout' 'Add_PreviewMouseUp'
    $Move = Source-Handler '$VolumeSlider' 'Add_PreviewMouseMove'
    $Change = Source-Handler '$VolumeSlider' 'Add_ValueChanged'
    $LostCapture = Source-Handler '$VolumeSlider' 'Add_LostMouseCapture'
    $ThumbComplete = Source-Handler '$VolumeSlider' 'AddHandler'
    $KeyDown = Source-Handler '$VolumeSlider' 'Add_PreviewKeyDown'
    $KeyUp = Source-Handler '$VolumeSlider' 'Add_PreviewKeyUp'
    $Track = [pscustomobject]@{ Thumb = [pscustomobject]@{ IsMouseOver = $false } }
    $Track | Add-Member ScriptMethod ValueFromPoint { param($Point) return $Point }
    $Template = [pscustomobject]@{ Track = $Track }
    $Template | Add-Member ScriptMethod FindName { param($Name, $Owner) return $this.Track }
    $VolumeSlider = [pscustomobject]@{
        Template = $Template; Value = 14; IsEnabled = $true; IsMouseOver = $true
        IsMouseCaptured = $false; IsMouseCaptureWithin = $false; CaptureAllowed = $true; CaptureCalls = 0
    }
    $VolumeSlider | Add-Member ScriptMethod CaptureMouse {
        $this.CaptureCalls++
        $this.IsMouseCaptured = $this.CaptureAllowed
        $this.IsMouseCaptureWithin = $this.CaptureAllowed
        return $this.CaptureAllowed
    }
    $VolumeSlider | Add-Member ScriptMethod ReleaseMouseCapture {
        $this.IsMouseCaptured = $false
        $this.IsMouseCaptureWithin = $false
    }
    $Pointer = [pscustomobject]@{
        ChangedButton = [System.Windows.Input.MouseButton]::Left
        LeftButton = [System.Windows.Input.MouseButtonState]::Pressed
        Position = 30
    }
    $Pointer | Add-Member ScriptMethod GetPosition { param($RelativeTo) return $this.Position }
    $script:VolumeSession = "input-session"
    $script:VolumeDirty = $false
    $script:VolumeDragging = $false
    $script:TrackDragging = $false
    $Before = [IO.File]::ReadAllText($VolumeRequestPath)
    & $Down $null $Pointer
    Assert-True ($script:TrackDragging -and $VolumeSlider.IsMouseCaptured) "Track click captures release outside window"
    Set-VolumeFromPointer $Pointer
    & $Change
    $Pointer.Position = 80
    & $Move $null $Pointer
    & $Change
    Assert-True ($VolumeSlider.Value -eq 80) "Captured track drag follows the pointer"
    Assert-True ([IO.File]::ReadAllText($VolumeRequestPath) -eq $Before) "Track drag sends no intermediate requests"
    $Pointer.Position = 150
    $Pointer.LeftButton = [System.Windows.Input.MouseButtonState]::Released
    & $Up $null $Pointer
    $Request = Get-Content -LiteralPath $VolumeRequestPath -Raw | ConvertFrom-Json
    Assert-True ($Request.value -eq 100 -and $Request.session -eq "input-session") "Outside release submits final clamped target"
    Assert-True (-not $script:VolumeDragging -and -not $VolumeSlider.IsMouseCaptured) "Release clears capture and drag state"
    & $LostCapture
    & $ThumbComplete
    $After = Get-Content -LiteralPath $VolumeRequestPath -Raw | ConvertFrom-Json
    Assert-True ($After.id -eq $Request.id) "Repeated completion notifications do not resend"

    $Pointer.LeftButton = [System.Windows.Input.MouseButtonState]::Pressed
    & $Down $null $Pointer
    $VolumeSlider.Value = 22
    & $Change
    $VolumeSlider.IsMouseCaptured = $false
    $VolumeSlider.IsMouseCaptureWithin = $false
    & $LostCapture
    $Request = Get-Content -LiteralPath $VolumeRequestPath -Raw | ConvertFrom-Json
    Assert-True ($Request.value -eq 22 -and -not $script:VolumeDragging) "Unexpected capture loss commits and unsticks polling"

    $VolumeSlider.CaptureAllowed = $false
    & $Down $null $Pointer
    $VolumeSlider.Value = 47
    & $Change
    $Request = Get-Content -LiteralPath $VolumeRequestPath -Raw | ConvertFrom-Json
    Assert-True ($Request.value -eq 47 -and -not $script:VolumeDragging) "Refused capture falls back to a working track click"

    $VolumeSlider.CaptureAllowed = $true
    $Track.Thumb.IsMouseOver = $true
    $Calls = $VolumeSlider.CaptureCalls
    & $Down $null $Pointer
    $VolumeSlider.IsMouseCaptureWithin = $true
    Assert-True ($VolumeSlider.CaptureCalls -eq $Calls -and -not $script:TrackDragging) "Native thumb capture is not stolen"
    $VolumeSlider.Value = 25
    & $Change
    $VolumeSlider.IsMouseCaptureWithin = $false
    & $ThumbComplete
    $Request = Get-Content -LiteralPath $VolumeRequestPath -Raw | ConvertFrom-Json
    Assert-True ($Request.value -eq 25 -and -not $script:VolumeDragging) "Native thumb completion still submits"

    $Before = [IO.File]::ReadAllText($VolumeRequestPath)
    & $KeyDown $null ([pscustomobject]@{ Key = "Right" })
    $VolumeSlider.Value = 26
    & $Change
    Assert-True ([IO.File]::ReadAllText($VolumeRequestPath) -eq $Before) "Keyboard edit waits for release"
    & $KeyUp
    $Request = Get-Content -LiteralPath $VolumeRequestPath -Raw | ConvertFrom-Json
    Assert-True ($Request.value -eq 26 -and -not $script:VolumeEditing) "Keyboard release still submits"

    # Exercise the real transition functions/tick, replacing only native window
    # opacity. This covers race/failure behavior without a desktop or sleeps.
    $SavedFlyout = $Flyout
    try {
        $Flyout = [pscustomobject]@{ IsVisible = $true; HideCount = 0 }
        $Flyout | Add-Member ScriptMethod Hide { $this.IsVisible = $false; $this.HideCount++ }
        $FadeTimer = [pscustomobject]@{ Enabled = $false }
        $FadeTimer | Add-Member ScriptMethod Stop { $this.Enabled = $false }
        $FadeTimer | Add-Member ScriptMethod Start { $this.Enabled = $true }
        $FadeClock = [pscustomobject]@{ Elapsed = [pscustomobject]@{ TotalMilliseconds = 0 } }
        $FadeClock | Add-Member ScriptMethod Restart { $this.Elapsed.TotalMilliseconds = 0 }
        $script:NativeOpacityWorks = $true
        $script:OpacityResetCount = 0
        function Set-FlyoutOpacity([byte]$Alpha) { return $script:NativeOpacityWorks }
        function Restore-FlyoutOpacity { $script:OpacityResetCount++ }
        $FadeTick = Source-Handler '$FadeTimer' 'Add_Tick'
        $script:Exiting = $false
        $script:FlyoutAlpha = 0
        Start-FlyoutFade $true $false
        Assert-True ($script:FlyoutAlpha -eq 255 -and -not $FadeTimer.Enabled -and $Flyout.IsVisible) "Animations disabled: show immediately"

        $script:FlyoutAlpha = 0
        Start-FlyoutFade $true $true
        $FadeClock.Elapsed.TotalMilliseconds = 60
        & $FadeTick
        Assert-True ($script:FlyoutAlpha -gt 0 -and $script:FlyoutAlpha -lt 255 -and $FadeTimer.Enabled) "Open fade progresses without blocking"
        Start-FlyoutFade $false $true
        $FadeClock.Elapsed.TotalMilliseconds = 30
        & $FadeTick
        $ClosingAlpha = $script:FlyoutAlpha
        Start-FlyoutFade $true $true
        Assert-True ($script:FadeFrom -eq $ClosingAlpha -and $script:FlyoutTargetVisible) "Reopen reverses an in-flight close"
        $FadeClock.Elapsed.TotalMilliseconds = 200
        & $FadeTick
        Assert-True ($Flyout.IsVisible -and $Flyout.HideCount -eq 0 -and -not $FadeTimer.Enabled) "Old close cannot hide a reopened window"

        $script:FlyoutAlpha = 0
        Start-FlyoutFade $true $true
        $script:NativeOpacityWorks = $false
        $FadeClock.Elapsed.TotalMilliseconds = 30
        & $FadeTick
        Assert-True ($Flyout.IsVisible -and $script:FlyoutAlpha -eq 255 -and -not $FadeTimer.Enabled) "Failure during a fade cannot leave an invisible flyout"

        $script:NativeOpacityWorks = $false
        $script:FlyoutAlpha = 0
        Start-FlyoutFade $true $true
        Assert-True ($script:FlyoutAlpha -eq 255 -and $Flyout.IsVisible -and -not $FadeTimer.Enabled) "Native opacity failure falls back to visible window"
        Start-FlyoutFade $false $true
        Assert-True (-not $Flyout.IsVisible -and $Flyout.HideCount -eq 1) "Native opacity failure still closes"
        Assert-True ($script:OpacityResetCount -ge 4) "Completed/fallback transitions restore native styles"
    } finally {
        $Flyout = $SavedFlyout
    }
    Write-Host "PASS: $script:Assertions assertions. WPF exercised: $WithWpf"
} finally {
    if ($Flyout) { $Flyout.Close() }
    Remove-Item -LiteralPath $TestDir -Recurse -Force
}
