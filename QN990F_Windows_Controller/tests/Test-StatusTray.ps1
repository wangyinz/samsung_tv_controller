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
foreach ($Case in @(
    @(0, 0, 1920, 1040, 1850, 1060, 360, 140),
    @(0, 0, 1920, 1040, 2, 2, 360, 500),
    @(-1920, 0, 0, 1040, -15, 1060, 360, 500),
    @(1920, -2160, 5760, 0, 5500, -20, 720, 1000)
)) {
    $Area = New-Object TVFlyoutNative+Rect
    $Area.Left = $Case[0]; $Area.Top = $Case[1]; $Area.Right = $Case[2]; $Area.Bottom = $Case[3]
    $Rect = [TVFlyoutNative]::Placement($Area, $Case[4], $Case[5], $Case[6], $Case[7])
    Assert-True ($Rect.Left -ge $Area.Left + 8 -and $Rect.Right -le $Area.Right - 8) "Horizontal work-area bounds"
    Assert-True ($Rect.Top -ge $Area.Top + 8 -and $Rect.Bottom -le $Area.Bottom - 8) "Vertical work-area bounds"
}

# Execute the implementation's functions, not copies of the volume protocol.
foreach ($Name in @("Submit-Volume", "Update-VolumeStatus", "Hide-Flyout", "Set-FlyoutTheme")) {
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
        "ConfigureItem", "ReauthorizeItem", "RestartItem", "LogItem", "ExitItem", "StatusPanel", "ActionsPanel"
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
        Set-FlyoutTheme
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
    Write-Host "PASS: $script:Assertions assertions. WPF exercised: $WithWpf"
} finally {
    if ($Flyout) { $Flyout.Close() }
    Remove-Item -LiteralPath $TestDir -Recurse -Force
}
