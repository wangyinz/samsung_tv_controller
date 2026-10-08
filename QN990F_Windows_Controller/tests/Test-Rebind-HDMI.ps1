# Exercises the installed helper's actual transaction with a temporary app
# directory. Native display probing and daemon process control are mocked.
$ErrorActionPreference = "Stop"
$Source = Join-Path (Split-Path -Parent $PSScriptRoot) "Rebind-HDMI.ps1"
$Tokens = $null
$Errors = $null
$Ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$Tokens, [ref]$Errors)
if ($Errors.Count) { throw ($Errors | Out-String) }
foreach ($Name in @("Invoke-Rebind", "Test-SameTarget")) {
    $Definition = $Ast.Find({
        param($Node)
        $Node -is [Management.Automation.Language.FunctionDefinitionAst] -and $Node.Name -eq $Name
    }, $true)
    if (-not $Definition) { throw "Missing $Name" }
    . ([scriptblock]::Create($Definition.Extent.Text))
}

$script:Assertions = 0
function Assert-True([bool]$Value, [string]$Message) {
    if (-not $Value) { throw $Message }
    $script:Assertions++
}

$Old = [pscustomobject]@{ name = "OLD"; path = "old-monitor"; manufacturer = 1; product = 2 }
$New = [pscustomobject]@{ name = "SAMSUNG"; path = "new-monitor"; manufacturer = 3; product = 4 }
$script:MockTargets = @($New)
$script:MockRunning = $false
$script:StopCount = 0
$script:StartCount = 0
$script:ProbeCount = 0
$script:Responses = New-Object 'System.Collections.Generic.Queue[string]'
function Get-HdmiTargets {
    $script:ProbeCount++
    if ($script:DisconnectOnProbe -eq $script:ProbeCount) { return @() }
    return $script:MockTargets
}
function Get-ControllerProcesses {
    if ($script:MockRunning) { return @([pscustomobject]@{ ProcessId = 123 }) }
    return @()
}
function Stop-ControllerGracefully($Processes) {
    $script:StopCount++
    $script:MockRunning = $false
    return $true
}
function Start-Controller {
    $script:StartCount++
    $script:MockRunning = $true
}
function Read-Host([string]$Prompt) { return $script:Responses.Dequeue() }

$AppDir = Join-Path ([IO.Path]::GetTempPath()) ("qn990f-rebind-test-" + [Guid]::NewGuid().ToString("N"))
[void][IO.Directory]::CreateDirectory($AppDir)
$ConfigPath = Join-Path $AppDir "config.json"
$ControllerPath = Join-Path $AppDir "QN990FController.py"
$VenvPython = Join-Path $AppDir "venv\Scripts\python.exe"
[void][IO.Directory]::CreateDirectory((Split-Path -Parent $VenvPython))
[IO.File]::WriteAllText($ControllerPath, "# test only")
[IO.File]::WriteAllText($VenvPython, "test only")
$Original = '{"hdmi_target":{"name":"OLD","path":"old-monitor","manufacturer":1,"product":2},"enable_idle_off":false,"idle_minutes":0,"enable_volume_control":true,"smartthings_device_id":"kept"}'
try {
    [IO.File]::WriteAllText($ConfigPath, $Original)
    $script:Responses.Enqueue("")
    Invoke-Rebind
    Assert-True ([IO.File]::ReadAllText($ConfigPath) -ceq $Original) "Cancel keeps exact configuration bytes"
    Assert-True ($script:StopCount -eq 0) "Cancel does not stop the controller"

    $script:MockRunning = $true
    $script:ProbeCount = 0
    $script:Responses.Enqueue("1")
    $script:Responses.Enqueue("YES")
    Invoke-Rebind
    $Saved = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    Assert-True (Test-SameTarget $Saved.hdmi_target $New) "Confirmed target was stored"
    Assert-True ($Saved.enable_idle_off -eq $false -and $Saved.idle_minutes -eq 0) "Disabled idle setting was preserved"
    Assert-True ($Saved.enable_volume_control -eq $true -and $Saved.smartthings_device_id -eq "kept") "Unrelated options were preserved"
    Assert-True ($script:StopCount -eq 1 -and $script:StartCount -eq 1 -and $script:MockRunning) "Running daemon was gracefully stopped and restarted (stop=$script:StopCount start=$script:StartCount running=$script:MockRunning)"

    $script:ProbeCount = 0
    $script:Responses.Enqueue("1")
    $script:Responses.Enqueue("YES")
    Invoke-Rebind
    Assert-True ($script:StopCount -eq 1 -and $script:StartCount -eq 1) "Selecting the existing binding is a no-op"

    [IO.File]::WriteAllText($ConfigPath, $Original)
    $script:ProbeCount = 0
    $script:DisconnectOnProbe = 2
    $script:Responses.Enqueue("1")
    $script:Responses.Enqueue("YES")
    try { Invoke-Rebind; throw "Expected disconnect failure" }
    catch {
        Assert-True ($_.Exception.Message -like "*disconnect*") "Disconnected target is rejected"
    }
    Assert-True ([IO.File]::ReadAllText($ConfigPath) -ceq $Original) "Disconnect leaves exact config untouched"
    Assert-True ($script:StopCount -eq 1) "Probe failure did not stop daemon"

    $script:ProbeCount = 0
    $script:DisconnectOnProbe = 3
    $script:Responses.Enqueue("1")
    $script:Responses.Enqueue("YES")
    try { Invoke-Rebind; throw "Expected post-stop disconnect failure" }
    catch {
        Assert-True ($_.Exception.Message -like "*disconnect*") "Post-stop disconnect is rejected"
    }
    Assert-True ([IO.File]::ReadAllText($ConfigPath) -ceq $Original) "Post-stop probe failure keeps exact config"
    Assert-True ($script:StopCount -eq 2 -and $script:StartCount -eq 2 -and $script:MockRunning) "Post-stop failure restarts the original daemon"
    Write-Host "Rebind helper tests passed ($script:Assertions assertions)."
} finally {
    [IO.Directory]::Delete($AppDir, $true)
}
