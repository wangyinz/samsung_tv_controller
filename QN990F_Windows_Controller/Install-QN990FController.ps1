param(
    [string]$TvIp = "",
    [double]$IdleMinutes = -1,
    [string]$Hotkey = "",
    [ValidateSet("", "lan", "smartthings")]
    [string]$ControlMethod = "",
    [Nullable[bool]]$EnableVolumeControl = $null
)

$ErrorActionPreference = "Stop"
$AppDir = Join-Path $env:LOCALAPPDATA "QN990FController"
$VenvDir = Join-Path $AppDir "venv"
$ControllerPath = Join-Path $AppDir "QN990FController.py"
$SourceControllerPath = Join-Path $PSScriptRoot "QN990FController.py"
$ConfigPath = Join-Path $AppDir "config.json"
$TokenPath = Join-Path $AppDir "samsung-token.txt"
$InstallStatePath = Join-Path $AppDir "install-complete"
$PidPath = Join-Path $AppDir "controller.pid"
$StatusPath = Join-Path $AppDir "status.json"
$StartupDir = [Environment]::GetFolderPath("Startup")
$StartupShortcut = Join-Path $StartupDir "Samsung TV Picture Controller.lnk"
$LegacyStartupShortcut = Join-Path $StartupDir "QN990F Picture Controller.lnk"
$ProgramsDir = [Environment]::GetFolderPath("Programs")
$StartMenuDir = Join-Path $ProgramsDir "Samsung TV Picture Controller"
$LegacyStartMenuDir = Join-Path $ProgramsDir "QN990F Controller"
$SmartThingsProfile = "local.qn990f.picture-controller"
$SmartThingsCliVersion = "2.1.1"
$SmartThingsCliAsset = "smartthings-windows-x64.zip"
$SmartThingsCliSha256 = "3f634dd76e77fded35a4f71485b5810f29f39351fc604733551a1e01ea773563"
$SmartThingsCliPath = Join-Path $AppDir "smartthings.exe"

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Stop-ExistingController {
    $ExpectedProcessPaths = @(
        (Join-Path $VenvDir "Scripts\python.exe"),
        (Join-Path $VenvDir "Scripts\pythonw.exe")
    )
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
            if ($Process -and
                (($ControllerPids -contains $ControllerPid) -or
                 ($Process.Path -and ($ExpectedProcessPaths -contains $Process.Path)))) {
                $ControllerPids += $ControllerPid
            } elseif ($Process) {
                Write-Warning "Ignoring stale controller PID $ControllerPid because it belongs to another process."
            }
        } catch {
            # Stale PID file; ignore.
        }
    }

    $ControllerPids = @($ControllerPids | Sort-Object -Unique)
    if ($ControllerPids.Count -gt 0) {
        Write-Host "Stopping existing Samsung TV Picture Controller (PID $($ControllerPids -join ', '))..."
        foreach ($ControllerPid in $ControllerPids) {
            Stop-Process -Id $ControllerPid -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 300
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

Write-Host "Samsung TV Picture Controller installer for Windows" -ForegroundColor Green

foreach ($RequiredFile in @(
    "QN990FController.py",
    "Configure-QN990FController.ps1",
    "Uninstall-QN990FController.ps1",
    "README.txt"
)) {
    if (-not (Test-Path (Join-Path $PSScriptRoot $RequiredFile))) {
        throw "$RequiredFile is missing. Keep the files from the ZIP in the same folder."
    }
}

$ExistingConfig = $null
$ExistingConfigText = $null
$HasExistingConfig = Test-Path $ConfigPath
$IsUpgrade = $HasExistingConfig -and (
    (Test-Path $InstallStatePath) -or
    ((Test-Path $ControllerPath) -and (
        (Test-Path $StartupShortcut) -or (Test-Path $LegacyStartupShortcut)
    ))
)
if ($HasExistingConfig) {
    try {
        $ExistingConfigText = Get-Content $ConfigPath -Raw -ErrorAction Stop
        $ExistingConfig = $ExistingConfigText | ConvertFrom-Json -ErrorAction Stop
        if ($null -eq $ExistingConfig -or -not ($ExistingConfig -is [PSCustomObject])) {
            throw "The JSON root must be an object."
        }
    } catch {
        throw "The existing config.json is invalid and was not changed. Fix or remove '$ConfigPath', then rerun the installer. $($_.Exception.Message)"
    }
}
$HadExistingToken = Test-Path $TokenPath
$ExistingTokenBytes = if ($HadExistingToken) {
    [IO.File]::ReadAllBytes($TokenPath)
} else {
    $null
}

if (-not $ControlMethod) {
    if ($ExistingConfig -and ($ExistingConfig.PSObject.Properties.Name -contains "control_method")) {
        $ControlMethod = [string]$ExistingConfig.control_method
    } else {
        Write-Host "Control connection:"
        Write-Host "  1. Direct LAN WebSocket"
        Write-Host "  2. SmartThings cloud"
        $ConnectionChoice = Read-Host "Choose connection [1]"
        $ControlMethod = if ($ConnectionChoice -eq "2") { "smartthings" } else { "lan" }
    }
}

function Invoke-SmartThingsCliProcess {
    param(
        [string]$Path,
        [string]$Arguments,
        [int]$TimeoutMilliseconds
    )
    $CliProcess = $null
    $SmartThingsMutex = New-Object System.Threading.Mutex(
        $false,
        "Local\SamsungTVPictureControllerSmartThings"
    )
    $HasSmartThingsMutex = $false
    try {
        try {
            $HasSmartThingsMutex = $SmartThingsMutex.WaitOne($TimeoutMilliseconds)
        } catch [System.Threading.AbandonedMutexException] {
            $HasSmartThingsMutex = $true
        }
        if (-not $HasSmartThingsMutex) {
            throw "Timed out waiting for another SmartThings operation."
        }
        $StartInfo = New-Object System.Diagnostics.ProcessStartInfo
        $StartInfo.FileName = $Path
        $StartInfo.Arguments = $Arguments
        $StartInfo.UseShellExecute = $false
        $StartInfo.CreateNoWindow = $true
        $StartInfo.RedirectStandardOutput = $true
        $StartInfo.RedirectStandardError = $true
        $CliProcess = New-Object System.Diagnostics.Process
        $CliProcess.StartInfo = $StartInfo
        if (-not $CliProcess.Start()) {
            throw "The SmartThings CLI process could not start."
        }
        $StdOutTask = $CliProcess.StandardOutput.ReadToEndAsync()
        $StdErrTask = $CliProcess.StandardError.ReadToEndAsync()
        if (-not $CliProcess.WaitForExit($TimeoutMilliseconds)) {
            $CliProcess.Kill()
            [void]$CliProcess.WaitForExit(5000)
            throw "The SmartThings CLI command timed out."
        }
        if ((-not $StdOutTask.Wait(5000)) -or (-not $StdErrTask.Wait(5000))) {
            throw "The SmartThings CLI output did not close after the command exited."
        }
        return [PSCustomObject]@{
            ExitCode = $CliProcess.ExitCode
            StdOut = $StdOutTask.Result
            StdErr = $StdErrTask.Result
        }
    } finally {
        if ($CliProcess) { $CliProcess.Dispose() }
        if ($HasSmartThingsMutex) { $SmartThingsMutex.ReleaseMutex() }
        $SmartThingsMutex.Dispose()
    }
}

function Get-SmartThingsCliVersion([string]$Path) {
    try {
        $Result = Invoke-SmartThingsCliProcess -Path $Path -Arguments "--version" `
            -TimeoutMilliseconds 15000
        if ($Result.ExitCode -eq 0) {
            return ($Result.StdOut + $Result.StdErr).Trim()
        }
    } catch {}
    return ""
}

function Install-PrivateSmartThingsCli {
    $ExpectedVersionPattern = "(^|[/\s])$([regex]::Escape($SmartThingsCliVersion))($|\s)"
    if (Test-Path $SmartThingsCliPath) {
        $InstalledVersion = Get-SmartThingsCliVersion $SmartThingsCliPath
        if ($InstalledVersion -match $ExpectedVersionPattern) {
            Write-Host "Using SmartThings CLI ${SmartThingsCliVersion}: $SmartThingsCliPath"
            return
        }
        Write-Warning "Replacing an unusable or unsupported private SmartThings CLI in $AppDir."
    }

    $ReleaseTag = [Uri]::EscapeDataString("@smartthings/cli@$SmartThingsCliVersion")
    $DownloadUrl = "https://github.com/SmartThingsCommunity/smartthings-cli/releases/download/$ReleaseTag/$SmartThingsCliAsset"
    $TempDir = Join-Path ([IO.Path]::GetTempPath()) ("qn990f-smartthings-" + [Guid]::NewGuid().ToString("N"))
    $ArchivePath = Join-Path $TempDir $SmartThingsCliAsset
    $ExtractDir = Join-Path $TempDir "extracted"
    $ExtractedCli = Join-Path $ExtractDir "smartthings.exe"

    try {
        New-Item -ItemType Directory -Path $TempDir | Out-Null
        Write-Host "Downloading SmartThings CLI $SmartThingsCliVersion from the official GitHub release..."
        Invoke-WebRequest -UseBasicParsing -Uri $DownloadUrl -OutFile $ArchivePath

        $ActualSha256 = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($ActualSha256 -ne $SmartThingsCliSha256) {
            throw "SmartThings CLI download failed SHA-256 verification. Expected $SmartThingsCliSha256 but received $ActualSha256."
        }

        Expand-Archive -LiteralPath $ArchivePath -DestinationPath $ExtractDir
        if (-not (Test-Path $ExtractedCli)) {
            throw "The verified SmartThings CLI archive did not contain smartthings.exe."
        }
        $DownloadedVersion = Get-SmartThingsCliVersion $ExtractedCli
        if ($DownloadedVersion -notmatch $ExpectedVersionPattern) {
            throw "The downloaded SmartThings CLI could not start or reported an unexpected version: $DownloadedVersion"
        }

        Copy-Item -LiteralPath $ExtractedCli -Destination $SmartThingsCliPath -Force
        Write-Host "Installed private SmartThings CLI ${SmartThingsCliVersion}: $SmartThingsCliPath"
    } catch {
        throw "Could not install the official SmartThings CLI $SmartThingsCliVersion. Check internet access and try again. $($_.Exception.Message)"
    } finally {
        Remove-Item -LiteralPath $TempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($ControlMethod -eq "lan") {
    if ((-not $PSBoundParameters.ContainsKey("TvIp")) -and $ExistingConfig) {
        $TvIp = [string]$ExistingConfig.tv_ip
    }
    if ([string]::IsNullOrWhiteSpace($TvIp)) {
        $TvIp = Read-Host "Enter the Samsung TV LAN IP address (example: 192.168.1.50)"
    }
    if ([string]::IsNullOrWhiteSpace($TvIp)) {
        throw "TV IP address cannot be empty."
    }
    $TvIp = $TvIp.Trim()
} else {
    $TvIp = ""
}
$TvIpChanged = $ExistingConfig -and
    (([string]$ExistingConfig.tv_ip).Trim() -ne $TvIp)
$ControlMethodChanged = $ExistingConfig -and
    ([string]$ExistingConfig.control_method).Trim().ToLowerInvariant() -ne $ControlMethod

$IdleWasChanged = $PSBoundParameters.ContainsKey("IdleMinutes")
if ((-not $IdleWasChanged) -and $ExistingConfig -and
    ($ExistingConfig.PSObject.Properties.Name -contains "idle_minutes")) {
    try {
        $IdleMinutes = [double]$ExistingConfig.idle_minutes
    } catch {
        throw "The existing config.json has an invalid idle_minutes value and was not changed."
    }
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
    $IdleWasChanged = $true
}

if ((-not $PSBoundParameters.ContainsKey("Hotkey")) -and $ExistingConfig -and
    ($ExistingConfig.PSObject.Properties.Name -contains "hotkey")) {
    $Hotkey = [string]$ExistingConfig.hotkey
}
if ([string]::IsNullOrWhiteSpace($Hotkey)) {
    $Hotkey = "Ctrl+Alt+P"
}
$Hotkey = $Hotkey.Trim()
$VolumeDefault = $true
if ($ExistingConfig -and
    ($ExistingConfig.PSObject.Properties.Name -contains "enable_volume_control")) {
    $VolumeDefault = [bool]$ExistingConfig.enable_volume_control
}
if ($null -eq $EnableVolumeControl) {
    $VolumeDefaultText = if ($VolumeDefault) { "Y" } else { "N" }
    $VolumeChoice = Read-Host "Enable integrated system/TV volume-key control? [$VolumeDefaultText]"
    if ([string]::IsNullOrWhiteSpace($VolumeChoice)) {
        $EnableVolumeControl = $VolumeDefault
    } elseif ($VolumeChoice -match '^[Yy]') {
        $EnableVolumeControl = $true
    } elseif ($VolumeChoice -match '^[Nn]') {
        $EnableVolumeControl = $false
    } else {
        throw "Volume control must be Y or N."
    }
}
$EnableVolumeControl = [bool]$EnableVolumeControl
$NeedsPairing = $ControlMethod -eq "smartthings" -or (-not $IsUpgrade) -or
    (-not $HadExistingToken) -or $TvIpChanged -or $ControlMethodChanged

function Restore-PreviousInstallation {
    if ($ExistingConfig) {
        Set-Content -Path $ConfigPath -Value $ExistingConfigText -Encoding UTF8
    } else {
        Remove-Item $ConfigPath -Force -ErrorAction SilentlyContinue
    }

    if ($HadExistingToken) {
        [IO.File]::WriteAllBytes($TokenPath, $ExistingTokenBytes)
    } else {
        Remove-Item $TokenPath -Force -ErrorAction SilentlyContinue
    }

    foreach ($FileName in $InstalledFileNames) {
        $BackupFile = Join-Path $BackupDir $FileName
        $InstalledFile = Join-Path $AppDir $FileName
        if (Test-Path $BackupFile) {
            Copy-Item -LiteralPath $BackupFile -Destination $InstalledFile -Force
        } else {
            Remove-Item -LiteralPath $InstalledFile -Force -ErrorAction SilentlyContinue
        }
    }

    if ($HadStartupShortcut) {
        Copy-Item -LiteralPath $BackupStartupShortcut -Destination $StartupShortcut -Force
    } else {
        Remove-Item $StartupShortcut -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $StartMenuDir -Recurse -Force -ErrorAction SilentlyContinue
    if ($HadStartMenu) {
        Copy-Item -LiteralPath $BackupStartMenuDir -Destination $StartMenuDir -Recurse -Force
    }
    if ($HadInstallState) {
        Copy-Item -LiteralPath $BackupInstallState -Destination $InstallStatePath -Force
    } else {
        Remove-Item $InstallStatePath -Force -ErrorAction SilentlyContinue
    }

    if ($IsUpgrade -and (Test-Path $ControllerPath) -and (Test-Path $VenvPythonW)) {
        Remove-Item $StatusPath -Force -ErrorAction SilentlyContinue
        Start-Process -FilePath $VenvPythonW -ArgumentList "`"$ControllerPath`"" -WorkingDirectory $AppDir
    }
}

if ($IsUpgrade) {
    Write-Host "Existing installation found. Settings and Samsung pairing token will be preserved."
}
Write-Host "Hotkey: $Hotkey -> Picture Off"
Write-Host "Key, mouse button, or wheel input after blanking -> wake"

New-Item -ItemType Directory -Force -Path $AppDir | Out-Null

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
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPythonW = Join-Path $VenvDir "Scripts\pythonw.exe"
$VenvIsUsable = (Test-Path $VenvPython) -and (Test-Path $VenvPythonW) -and
    (Test-PythonInvocation -Exe $VenvPython)
if (-not $VenvIsUsable) {
    if (Test-Path $VenvDir) {
        Stop-ExistingController
        Remove-Item -LiteralPath $VenvDir -Recurse -Force
    }
    $VenvArgs = @($Python.PrefixArgs) + @("-m", "venv", $VenvDir)
    & $Python.Exe @VenvArgs
    if ($LASTEXITCODE -ne 0) { throw "Failed to create Python virtual environment." }
}

Write-Step "Installing samsungtvws 3.0.5"
& $VenvPython -m pip install --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to update pip inside the virtual environment." }

& $VenvPython -m pip install --disable-pip-version-check "samsungtvws==3.0.5"
if ($LASTEXITCODE -ne 0) { throw "Failed to install samsungtvws." }

$SmartThingsCli = ""
$SmartThingsDeviceId = ""
if ($ControlMethod -eq "smartthings") {
    Write-Step "Installing the official SmartThings CLI"
    Install-PrivateSmartThingsCli
    $SmartThingsCli = $SmartThingsCliPath

    if ($ExistingConfig -and -not $ControlMethodChanged -and
        ($ExistingConfig.PSObject.Properties.Name -contains "smartthings_device_id")) {
        $SmartThingsDeviceId = [string]$ExistingConfig.smartthings_device_id
    }
    if ([string]::IsNullOrWhiteSpace($SmartThingsDeviceId)) {
        Write-Step "Signing in to SmartThings and selecting a TV"
        Write-Host "Your browser may open for Samsung account sign-in and device authorization."
        $env:SMARTTHINGS_TOKEN = $null
        $DeviceArguments = "devices --json --profile `"$SmartThingsProfile`" --token `"`" --language NONE"
        $DevicesResult = Invoke-SmartThingsCliProcess -Path $SmartThingsCli `
            -Arguments $DeviceArguments -TimeoutMilliseconds 300000
        if ($DevicesResult.ExitCode -ne 0) {
            throw "Could not list SmartThings devices. Check sign-in and internet access."
        }
        $DevicesJson = $DevicesResult.StdOut
        $DevicesValue = $DevicesJson | ConvertFrom-Json
        $Devices = if ($DevicesValue -is [array]) {
            @($DevicesValue)
        } elseif ($DevicesValue.PSObject.Properties.Name -contains "items") {
            @($DevicesValue.items)
        } else {
            @($DevicesValue)
        }
        $CompatibleTvs = @($Devices | Where-Object {
            $Device = $_
            if ($Device.manufacturerName -ne "Samsung Electronics" -or $Device.type -ne "OCF") {
                return $false
            }
            $Main = @($Device.components | Where-Object { $_.id -eq "main" }) | Select-Object -First 1
            if (-not $Main) { return $false }
            $CapabilityIds = @($Main.capabilities | ForEach-Object {
                if ($_ -is [string]) { $_ } else { $_.id }
            })
            $CategoryNames = @($Main.categories | ForEach-Object {
                if ($_ -is [string]) { $_ } else { $_.name }
            })
            return ($CapabilityIds -contains "execute") -and
                ($CapabilityIds -contains "samsungvd.remoteControl") -and
                ((-not $EnableVolumeControl) -or
                 ($CapabilityIds -contains "audioVolume")) -and
                ($CategoryNames -contains "Television")
        })
        if ($CompatibleTvs.Count -eq 0) {
            $VolumeRequirement = if ($EnableVolumeControl) { " and audio-volume" } else { "" }
            throw "No compatible Samsung SmartThings TV with remote${VolumeRequirement} control was found."
        }
        Write-Host "SmartThings TVs:"
        for ($Index = 0; $Index -lt $CompatibleTvs.Count; $Index++) {
            $Device = $CompatibleTvs[$Index]
            $Label = if ($Device.label) { $Device.label } else { $Device.name }
            Write-Host "  $($Index + 1). $Label [$($Device.deviceId)]"
        }
        $ChoiceText = Read-Host "Choose the TV [1]"
        if ([string]::IsNullOrWhiteSpace($ChoiceText)) { $ChoiceText = "1" }
        $Choice = 0
        if ((-not [int]::TryParse($ChoiceText, [ref]$Choice)) -or
            $Choice -lt 1 -or $Choice -gt $CompatibleTvs.Count) {
            throw "Invalid SmartThings TV selection."
        }
        $SmartThingsDeviceId = [string]$CompatibleTvs[$Choice - 1].deviceId
    }
}

Write-Step "Checking the controller package"
& $VenvPython $SourceControllerPath --self-test
if ($LASTEXITCODE -ne 0) {
    throw "The controller self-test failed. The installed controller and configuration were not replaced."
}

$InstalledFileNames = @(
    "QN990FController.py",
    "Configure-QN990FController.ps1",
    "Reauthorize-SmartThings.ps1",
    "Uninstall-QN990FController.ps1",
    "README.txt"
)
$BackupDir = Join-Path $AppDir ("install-backup-" + [Guid]::NewGuid().ToString("N"))
$BackupStartupShortcut = Join-Path $BackupDir "StartupShortcut.lnk"
$BackupStartMenuDir = Join-Path $BackupDir "StartMenu"
$BackupInstallState = Join-Path $BackupDir "install-complete"
$HadStartupShortcut = $IsUpgrade -and (Test-Path $StartupShortcut)
$HadStartMenu = $IsUpgrade -and (Test-Path $StartMenuDir)
$HadInstallState = $IsUpgrade -and (Test-Path $InstallStatePath)
$CandidateProcess = $null

try {
    New-Item -ItemType Directory -Path $BackupDir | Out-Null
    foreach ($FileName in $InstalledFileNames) {
        $InstalledFile = Join-Path $AppDir $FileName
        if (Test-Path $InstalledFile) {
            Copy-Item -LiteralPath $InstalledFile -Destination (Join-Path $BackupDir $FileName)
        }
    }
    if ($HadStartupShortcut) {
        Copy-Item -LiteralPath $StartupShortcut -Destination $BackupStartupShortcut
    }
    if ($HadStartMenu) {
        Copy-Item -LiteralPath $StartMenuDir -Destination $BackupStartMenuDir -Recurse
    }
    if ($HadInstallState) {
        Copy-Item -LiteralPath $InstallStatePath -Destination $BackupInstallState
    }

    Stop-ExistingController

    try {
        if (-not $IsUpgrade) {
            Remove-Item $InstallStatePath -Force -ErrorAction SilentlyContinue
            Remove-Item $StartupShortcut -Force -ErrorAction SilentlyContinue
            Remove-Item $StartMenuDir -Recurse -Force -ErrorAction SilentlyContinue
        }

        $Config = [ordered]@{
            control_method = $ControlMethod
            tv_ip = $TvIp
            port = 8002
            idle_minutes = [double]$IdleMinutes
            enable_idle_off = ($IdleMinutes -gt 0)
            respect_display_required = $true
            hotkey = $Hotkey
            picture_off_key = "KEY_PICTURE_OFF"
            wake_key = "KEY_RETURN"
            wake_guard_ms = 800
            poll_interval_ms = 50
            socket_timeout_seconds = 5.0
            key_press_delay_seconds = 0.05
            remote_name = "Samsung-TV-Picture-Controller"
            connection_refresh_seconds = 8.0
            input_wake_debounce_ms = 180
            enable_mouse_move_wake = $false
            mouse_wake_threshold_counts = 24
            mouse_motion_window_ms = 500
            ignored_input_device_substrings = @()
            smartthings_cli = $SmartThingsCli
            smartthings_profile = $SmartThingsProfile
            smartthings_device_id = $SmartThingsDeviceId
            smartthings_command_timeout_seconds = 20.0
            smartthings_auth_check_interval_seconds = 1800.0
            enable_volume_control = $EnableVolumeControl
            tv_volume_floor = 10
            tv_volume_refresh_seconds = 30.0
        }
        if ($ExistingConfig) {
            foreach ($Property in $ExistingConfig.PSObject.Properties) {
                $Config[$Property.Name] = $Property.Value
            }
            if (-not ($ExistingConfig.PSObject.Properties.Name -contains "remote_name")) {
                $Config["remote_name"] = "QN990F-PC-Controller"
            }
        }
        $Config["tv_ip"] = $TvIp
        $Config["control_method"] = $ControlMethod
        if ($ControlMethod -eq "smartthings") {
            $Config["smartthings_cli"] = $SmartThingsCli
            $Config["smartthings_profile"] = $SmartThingsProfile
            $Config["smartthings_device_id"] = $SmartThingsDeviceId
        }
        $Config["idle_minutes"] = [double]$IdleMinutes
        if ((-not $ExistingConfig) -or $IdleWasChanged -or
            (-not ($ExistingConfig.PSObject.Properties.Name -contains "enable_idle_off"))) {
            $Config["enable_idle_off"] = ($IdleMinutes -gt 0)
        }
        $Config["hotkey"] = $Hotkey
        $Config["enable_volume_control"] = $EnableVolumeControl
        $Config["tv_volume_refresh_seconds"] = [Math]::Max(
            30.0, [double]$Config["tv_volume_refresh_seconds"]
        )
        $Config | ConvertTo-Json -Depth 5 | Set-Content -Path $ConfigPath -Encoding UTF8

        Write-Step "Checking the global hotkey"
        & $VenvPython $SourceControllerPath --check-hotkey
        if ($LASTEXITCODE -ne 0) {
            throw "The hotkey '$Hotkey' is already in use or invalid. Rerun the installer with another hotkey, for example: -Hotkey 'Ctrl+Alt+O'."
        }

        if ($NeedsPairing) {
            if ($ControlMethod -eq "smartthings") {
                Write-Step "Validating SmartThings cloud control"
                Write-Host "Keep the selected TV on and connected to the internet."
            } else {
                Write-Step "Pairing with the TV"
                Write-Host "Turn the Samsung TV on and keep it on the same LAN/subnet as this PC."
                Write-Host "When the TV asks whether to allow $($Config['remote_name']), choose Allow." -ForegroundColor Yellow
                if ($TvIpChanged) {
                    Remove-Item $TokenPath -Force -ErrorAction SilentlyContinue
                }
            }
            & $VenvPython $SourceControllerPath --pair
            if ($LASTEXITCODE -ne 0) {
                if ($ControlMethod -eq "smartthings") {
                    throw "SmartThings validation failed. Check sign-in, internet access, and the selected TV."
                }
                throw "Pairing failed. Check the TV IP, LAN connectivity, and Samsung Device Connection Manager permissions."
            }
        } else {
            Write-Step "Preserving TV pairing"
            Write-Host "Existing Samsung pairing token was kept; no pairing command was sent."
        }

        if (-not $IsUpgrade) {
            Write-Step "Testing Picture Off + wake"
            Write-Host "The TV should go black for about 2 seconds and then return."
            $TestReady = Read-Host "Run this Picture Off test now? [y/N]"
            if ($TestReady -notmatch '^[Yy]') {
                throw "No TV command was sent. Automatic startup was not enabled."
            }
            & $VenvPython $SourceControllerPath --test
            if ($LASTEXITCODE -ne 0) {
                throw "The Picture Off/wake test failed. Automatic startup was not enabled. See $AppDir\controller.log."
            }
            $Answer = Read-Host "Did the TV actually go black and then come back? [y/N]"
            if ($Answer -notmatch '^[Yy]') {
                throw "Picture Off/wake was not visually confirmed. Automatic startup was not enabled."
            }
        } else {
            Write-Host "Skipped the Picture Off/wake visual test during upgrade."
        }

        Write-Step "Copying controller files"
        Copy-Item $SourceControllerPath $ControllerPath -Force
        Copy-Item (Join-Path $PSScriptRoot "Configure-QN990FController.ps1") (Join-Path $AppDir "Configure-QN990FController.ps1") -Force
        Copy-Item (Join-Path $PSScriptRoot "Reauthorize-SmartThings.ps1") (Join-Path $AppDir "Reauthorize-SmartThings.ps1") -Force
        Copy-Item (Join-Path $PSScriptRoot "Uninstall-QN990FController.ps1") (Join-Path $AppDir "Uninstall-QN990FController.ps1") -Force
        Copy-Item (Join-Path $PSScriptRoot "README.txt") (Join-Path $AppDir "README.txt") -Force

        Write-Step "Adding automatic startup"
        New-Shortcut `
            -Path $StartupShortcut `
            -Target $VenvPythonW `
            -Arguments "`"$ControllerPath`"" `
            -WorkingDirectory $AppDir

        New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null
        $PowerShellExe = (Get-Command powershell.exe).Source
        New-Shortcut `
            -Path (Join-Path $StartMenuDir "Configure Samsung TV Picture Controller.lnk") `
            -Target $PowerShellExe `
            -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$AppDir\Configure-QN990FController.ps1`"" `
            -WorkingDirectory $AppDir
        Remove-Item (Join-Path $StartMenuDir "Reauthorize SmartThings.lnk") -Force -ErrorAction SilentlyContinue
        if ($ControlMethod -eq "smartthings") {
            New-Shortcut `
                -Path (Join-Path $StartMenuDir "Reauthorize SmartThings.lnk") `
                -Target $PowerShellExe `
                -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$AppDir\Reauthorize-SmartThings.ps1`"" `
                -WorkingDirectory $AppDir
        }
        New-Shortcut `
            -Path (Join-Path $StartMenuDir "Uninstall Samsung TV Picture Controller.lnk") `
            -Target $PowerShellExe `
            -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$AppDir\Uninstall-QN990FController.ps1`"" `
            -WorkingDirectory $AppDir

        Write-Step "Starting the controller"
        Remove-Item $StatusPath -Force -ErrorAction SilentlyContinue
        $CandidateProcess = Start-Process -FilePath $VenvPythonW -ArgumentList "`"$ControllerPath`"" -WorkingDirectory $AppDir -PassThru

        $Started = $false
        for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
            Start-Sleep -Milliseconds 100
            if ($CandidateProcess.HasExited) { break }
            if (Test-Path $StatusPath) {
                try {
                    $Status = Get-Content $StatusPath -Raw | ConvertFrom-Json
                    $Started = [bool]$Status.running
                } catch {}
                if ($Started) { break }
            }
        }
        if (-not $Started) {
            throw "The background process did not report a running state. Check $AppDir\controller.log."
        }
        "complete" | Set-Content -Path $InstallStatePath -Encoding ASCII
        Remove-Item $LegacyStartupShortcut -Force -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $LegacyStartMenuDir "Configure QN990F Controller.lnk") -Force -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $LegacyStartMenuDir "Uninstall QN990F Controller.lnk") -Force -ErrorAction SilentlyContinue
        Remove-Item $LegacyStartMenuDir -Force -ErrorAction SilentlyContinue
    } catch {
        $InstallError = $_
        if ($CandidateProcess) {
            try {
                if (-not $CandidateProcess.HasExited) {
                    Stop-Process -Id $CandidateProcess.Id -Force -ErrorAction SilentlyContinue
                    Start-Sleep -Milliseconds 300
                }
            } catch {}
        }
        Restore-PreviousInstallation
        throw $InstallError
    }
} finally {
    Remove-Item $BackupDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
if ($IsUpgrade) {
    Write-Host "Updated/repaired." -ForegroundColor Green
} else {
    Write-Host "Installed." -ForegroundColor Green
}
Write-Host "  Hotkey:           $Hotkey"
Write-Host "  Connection:       $ControlMethod"
if ([bool]($Config["enable_idle_off"])) {
    Write-Host "  Automatic blank:  after $IdleMinutes minute(s) of keyboard/mouse inactivity"
} else {
    Write-Host "  Automatic blank:  disabled"
}
Write-Host "  Wake:             key, mouse button, or wheel"
Write-Host "  Pointer movement: ignored by default"
if ($EnableVolumeControl) {
    Write-Host "  Volume control:   enabled; TV above 10 first"
} else {
    Write-Host "  Volume control:   disabled"
}
Write-Host "  Config/logs:      $AppDir"
Write-Host ""
Write-Host "For reliable use, reserve the TV's IP in your router/DHCP settings."
Write-Host "Also set Windows' own 'turn off my screen' timeout longer than this controller's timeout (or Never)."
