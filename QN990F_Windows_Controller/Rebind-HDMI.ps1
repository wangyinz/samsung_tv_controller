param([switch]$NoPause)

$ErrorActionPreference = "Stop"
$AppDir = Join-Path $env:LOCALAPPDATA "QN990FController"
$ConfigPath = Join-Path $AppDir "config.json"
$ControllerPath = Join-Path $AppDir "QN990FController.py"
$VenvPython = Join-Path $AppDir "venv\Scripts\python.exe"
$VenvPythonW = Join-Path $AppDir "venv\Scripts\pythonw.exe"
$PidPath = Join-Path $AppDir "controller.pid"

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class QN990FRebindWindow {
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr FindWindow(string className, string windowName);
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll")]
    public static extern bool PostMessage(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);
}
'@

function Stop-ProbeTree($Probe) {
    # python.exe in a venv can be a launcher with a real Python child. Reap
    # both before disposing redirected pipes, including when the launcher has
    # already exited but its child still holds stdout/stderr open.
    $Descendants = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ParentProcessId -eq $Probe.Id -and $_.Name -in @("python.exe", "pythonw.exe") })
    if (-not $Probe.HasExited) {
        $Killer = New-Object Diagnostics.ProcessStartInfo
        $Killer.FileName = Join-Path $env:SystemRoot "System32\taskkill.exe"
        $Killer.Arguments = "/PID $($Probe.Id) /T /F"
        $Killer.UseShellExecute = $false
        $Killer.CreateNoWindow = $true
        $KillProcess = [Diagnostics.Process]::Start($Killer)
        try {
            if (-not $KillProcess.WaitForExit(3000)) {
                $KillProcess.Kill()
                [void]$KillProcess.WaitForExit(2000)
            }
        } finally { $KillProcess.Dispose() }
    }
    foreach ($Child in $Descendants) {
        $Live = Get-CimInstance Win32_Process -Filter "ProcessId = $($Child.ProcessId)" -ErrorAction SilentlyContinue
        if ($Live -and $Live.ParentProcessId -eq $Probe.Id -and
            $Live.Name -in @("python.exe", "pythonw.exe")) {
            Stop-Process -Id $Child.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    [void]$Probe.WaitForExit(2000)
}

function Get-HdmiTargets {
    $Startup = New-Object Diagnostics.ProcessStartInfo
    $Startup.FileName = $VenvPython
    $Startup.Arguments = '"{0}" --list-hdmi-targets' -f $ControllerPath
    $Startup.WorkingDirectory = $AppDir
    $Startup.UseShellExecute = $false
    $Startup.CreateNoWindow = $true
    $Startup.RedirectStandardOutput = $true
    $Startup.RedirectStandardError = $true
    $Probe = [Diagnostics.Process]::Start($Startup)
    try {
        $Stdout = $Probe.StandardOutput.ReadToEndAsync()
        $Stderr = $Probe.StandardError.ReadToEndAsync()
        if (-not $Probe.WaitForExit(15000)) {
            Stop-ProbeTree $Probe
            throw "HDMI display detection timed out. The binding was not changed."
        }
        if (-not $Stdout.Wait(2000) -or -not $Stderr.Wait(2000)) {
            Stop-ProbeTree $Probe
            throw "HDMI display detection left a child process running. The binding was not changed."
        }
        $Output = $Stdout.GetAwaiter().GetResult()
        $ErrorOutput = $Stderr.GetAwaiter().GetResult()
        if ($Probe.ExitCode -ne 0) {
            throw "HDMI display detection failed (exit $($Probe.ExitCode)): $ErrorOutput"
        }
        $Parsed = ConvertFrom-Json -InputObject $Output
        $Targets = @($Parsed)
        if (-not $Targets -or $Targets.Count -eq 0) {
            throw "No identifiable HDMI display is connected. Connect this PC to the TV and try again."
        }
        foreach ($Target in $Targets) {
            if (-not $Target.path -or -not $Target.name -or
                $null -eq $Target.manufacturer -or $null -eq $Target.product) {
                throw "HDMI display detection returned an incomplete identity. The binding was not changed."
            }
        }
        $Ids = @($Targets | ForEach-Object {
            "$( [string]$_.path )|$( [int]$_.manufacturer )|$( [int]$_.product )"
        })
        if (@($Ids | Select-Object -Unique).Count -ne $Targets.Count) {
            throw "HDMI display detection returned an ambiguous identity. The binding was not changed."
        }
        return $Targets
    } finally {
        if (-not $Probe.HasExited) { Stop-ProbeTree $Probe }
        $Probe.Dispose()
    }
}

function Test-SameTarget($Left, $Right) {
    return $Left -and $Right -and
        ([string]$Left.path -ceq [string]$Right.path) -and
        ([int]$Left.manufacturer -eq [int]$Right.manufacturer) -and
        ([int]$Left.product -eq [int]$Right.product)
}

function Get-ControllerProcesses {
    $ScriptPattern = '(?i)(?:^|\s)"?' + [regex]::Escape($ControllerPath) + '"?(?=\s|$)'
    return @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.Name -in @("python.exe", "pythonw.exe") -and
        $_.CommandLine -and ([string]$_.CommandLine) -match $ScriptPattern
    })
}

function Get-ControllerWindow($ProcessInfo) {
    $ProcessId = [uint32]$ProcessInfo.ProcessId
    $Window = [QN990FRebindWindow]::FindWindow("QN990FRawInput_$ProcessId", $null)
    if ($Window -eq [IntPtr]::Zero) { return [IntPtr]::Zero }
    $OwnerId = [uint32]0
    [void][QN990FRebindWindow]::GetWindowThreadProcessId($Window, [ref]$OwnerId)
    if ($OwnerId -ne $ProcessId) { return [IntPtr]::Zero }
    return $Window
}

function Stop-ControllerGracefully($Processes) {
    if ($Processes.Count -eq 0) { return $false }
    $Owners = @($Processes | Where-Object {
        (Get-ControllerWindow $_) -ne [IntPtr]::Zero
    })
    if ($Owners.Count -ne 1) {
        throw "Could not identify exactly one running controller window. The binding was not changed."
    }
    $Owner = $Owners[0]
    $Window = Get-ControllerWindow $Owner
    if (-not [QN990FRebindWindow]::PostMessage($Window, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)) {
        throw "Could not request the controller to stop. The binding was not changed."
    }
    $Deadline = [DateTime]::UtcNow.AddSeconds(55)
    while ([DateTime]::UtcNow -lt $Deadline) {
        $Remaining = @(Get-ControllerProcesses)
        if ($Remaining.Count -eq 0) { return $true }
        Start-Sleep -Milliseconds 200
    }
    throw "The controller did not stop within 55 seconds. The binding was not changed."
}

function Start-Controller {
    if (-not (Test-Path -LiteralPath $VenvPythonW)) {
        throw "The installed Python runtime is missing; the controller could not restart."
    }
    $Started = Start-Process -FilePath $VenvPythonW -ArgumentList ('"{0}" --suppress-startup-idle' -f $ControllerPath) `
        -WorkingDirectory $AppDir -WindowStyle Hidden -PassThru
    if (-not $Started) { throw "The controller could not restart." }
    try {
        $Deadline = [DateTime]::UtcNow.AddSeconds(15)
        while ([DateTime]::UtcNow -lt $Deadline) {
            $Processes = @(Get-ControllerProcesses)
            $Owners = @($Processes | Where-Object {
                (Get-ControllerWindow $_) -ne [IntPtr]::Zero
            })
            if ($Owners.Count -eq 1 -and
                ($Owners[0].ProcessId -eq $Started.Id -or
                 $Owners[0].ParentProcessId -eq $Started.Id)) { return }
            if ($Owners.Count -gt 1) {
                throw "More than one controller is running. Inspect the controller log."
            }
            Start-Sleep -Milliseconds 200
        }
        throw "The controller did not start within 15 seconds. Inspect the controller log."
    } finally {
        $Started.Dispose()
    }
}

function Invoke-Rebind {
    if ((-not (Test-Path -LiteralPath $ConfigPath)) -or
        (-not (Test-Path -LiteralPath $ControllerPath)) -or
        (-not (Test-Path -LiteralPath $VenvPython))) {
        throw "Samsung TV Picture Controller is not installed correctly."
    }

    Write-Host "Samsung TV Picture Controller - rebind HDMI TV" -ForegroundColor Green
    Write-Host "Connect this PC to the configured TV by HDMI before continuing."
    $OriginalBytes = [IO.File]::ReadAllBytes($ConfigPath)
    $OriginalHash = [Security.Cryptography.SHA256]::Create()
    try { $OriginalDigest = [Convert]::ToBase64String($OriginalHash.ComputeHash($OriginalBytes)) }
    finally { $OriginalHash.Dispose() }
    $Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    $Targets = @(Get-HdmiTargets)
    Write-Host "Currently connected HDMI displays:"
    for ($Index = 0; $Index -lt $Targets.Count; $Index++) {
        $Target = $Targets[$Index]
        $Suffix = if (Test-SameTarget $Config.hdmi_target $Target) { " (current binding)" } else { "" }
        Write-Host "  $($Index + 1). $($Target.name) [EDID $($Target.manufacturer):$($Target.product)]"
        Write-Host "     $($Target.path)$Suffix"
    }
    $ChoiceText = Read-Host "Enter the number of the HDMI display attached to the configured TV (Enter cancels)"
    if ([string]::IsNullOrWhiteSpace($ChoiceText)) { Write-Host "Cancelled. No changes made."; return }
    $Choice = 0
    if ((-not [int]::TryParse($ChoiceText.Trim(), [ref]$Choice)) -or
        $Choice -lt 1 -or $Choice -gt $Targets.Count) {
        throw "Invalid display selection. No changes made."
    }
    $Selected = $Targets[$Choice - 1]
    Write-Host "Selected: $($Selected.name) [EDID $($Selected.manufacturer):$($Selected.product)]"
    Write-Host "          $($Selected.path)"
    if ((Read-Host "Confirm this HDMI display is the configured TV: type YES") -cne "YES") {
        Write-Host "Cancelled. No changes made."
        return
    }
    $FreshTargets = @(Get-HdmiTargets)
    if (@($FreshTargets | Where-Object { Test-SameTarget $_ $Selected }).Count -ne 1) {
        throw "The selected HDMI display disconnected or changed. No changes made."
    }
    $Selected = @($FreshTargets | Where-Object { Test-SameTarget $_ $Selected })[0]
    if (Test-SameTarget $Config.hdmi_target $Selected) {
        Write-Host "This HDMI display is already bound. No settings or processes were changed."
        return
    }

    # Serialize with interactive SmartThings authorization; the controller also
    # needs a graceful exit so an in-flight refresh can finish before restart.
    $AuthMutex = New-Object System.Threading.Mutex($false, "Local\SamsungTVPictureControllerReauthorization")
    $OwnAuthMutex = $false
    try {
        try { $OwnAuthMutex = $AuthMutex.WaitOne(0) }
        catch [System.Threading.AbandonedMutexException] { $OwnAuthMutex = $true }
        if (-not $OwnAuthMutex) { throw "SmartThings authorization is in progress. Try again after it finishes." }

        $Processes = @(Get-ControllerProcesses)
        $WasRunning = $Processes.Count -gt 0
        if ($WasRunning) { [void](Stop-ControllerGracefully $Processes) }

        $TemporaryPath = Join-Path $AppDir ("config.rebind-" + [Guid]::NewGuid().ToString("N") + ".tmp")
        $BackupPath = Join-Path $AppDir ("config.rebind-" + [Guid]::NewGuid().ToString("N") + ".bak")
        $Replaced = $false
        $KeepBackup = $false
        try {
            $CurrentBytes = [IO.File]::ReadAllBytes($ConfigPath)
            $CurrentHash = [Security.Cryptography.SHA256]::Create()
            try { $CurrentDigest = [Convert]::ToBase64String($CurrentHash.ComputeHash($CurrentBytes)) }
            finally { $CurrentHash.Dispose() }
            if ($CurrentDigest -cne $OriginalDigest) {
                throw "Configuration changed during selection. Nothing was overwritten."
            }
            $StillAttached = @(Get-HdmiTargets | Where-Object { Test-SameTarget $_ $Selected })
            if ($StillAttached.Count -ne 1) {
                throw "The selected HDMI display disconnected. Nothing was saved."
            }
            $Selected = $StillAttached[0]
            $Config | Add-Member -NotePropertyName hdmi_target -NotePropertyValue $Selected -Force
            $Json = $Config | ConvertTo-Json -Depth 20
            [IO.File]::WriteAllText($TemporaryPath, $Json, [Text.UTF8Encoding]::new($false))
            [IO.File]::Replace($TemporaryPath, $ConfigPath, $BackupPath)
            $Replaced = $true
            $KeepBackup = $true
            if ($WasRunning) { Start-Controller }
            $KeepBackup = $false
            Write-Host "HDMI TV binding updated." -ForegroundColor Green
        } catch {
            $Failure = $_
            if ($Replaced -and @(Get-ControllerProcesses).Count -eq 0) {
                $RestorePath = Join-Path $AppDir ("config.rebind-" + [Guid]::NewGuid().ToString("N") + ".tmp")
                try {
                    [IO.File]::WriteAllBytes($RestorePath, $OriginalBytes)
                    [IO.File]::Replace($RestorePath, $ConfigPath, $null)
                    $KeepBackup = $false
                    if ($WasRunning) { Start-Controller }
                } finally {
                    Remove-Item -LiteralPath $RestorePath -Force -ErrorAction SilentlyContinue
                }
            } elseif (-not $Replaced -and $WasRunning -and @(Get-ControllerProcesses).Count -eq 0) {
                Start-Controller
            }
            if ($KeepBackup) {
                Write-Warning "The previous configuration remains at $BackupPath for recovery."
            }
            throw $Failure
        } finally {
            Remove-Item -LiteralPath $TemporaryPath -Force -ErrorAction SilentlyContinue
            if (-not $KeepBackup) {
                Remove-Item -LiteralPath $BackupPath -Force -ErrorAction SilentlyContinue
            }
        }
    } finally {
        if ($OwnAuthMutex) { $AuthMutex.ReleaseMutex() }
        $AuthMutex.Dispose()
    }
}

$RebindMutex = New-Object System.Threading.Mutex($false, "Local\SamsungTVPictureControllerHdmiRebind")
$OwnRebindMutex = $false
$ExitCode = 0
try {
    try { $OwnRebindMutex = $RebindMutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $OwnRebindMutex = $true }
    if (-not $OwnRebindMutex) {
        throw "Another HDMI rebind window is already open. Use that window."
    }
    Invoke-Rebind
} catch {
    Write-Warning $_.Exception.Message
    $ExitCode = 1
} finally {
    if ($OwnRebindMutex) { $RebindMutex.ReleaseMutex() }
    $RebindMutex.Dispose()
    if (-not $NoPause) { [void](Read-Host "Press Enter to close") }
}
exit $ExitCode
