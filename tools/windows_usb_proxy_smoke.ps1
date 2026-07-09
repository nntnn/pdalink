param(
    [string]$Adb = "",
    [string]$Apk = "",
    [int]$LocalPort = 18080,
    [int]$RemotePort = 8000,
    [string]$Url = "https://ifconfig.me",
    [switch]$NoStartAndroid,
    [switch]$NoStopAndroid,
    [switch]$KeepForward
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $Adb) {
    $Adb = Join-Path $Root ".tools\platform-tools-windows\adb.exe"
}
if (-not (Test-Path $Adb)) {
    throw "adb.exe not found at $Adb. Install Windows platform-tools or pass -Adb."
}

function Invoke-Adb {
    & $Adb @args
    if ($LASTEXITCODE -ne 0) {
        throw "adb failed: $($args -join ' ')"
    }
}

function Invoke-Curl {
    & curl.exe @args
    if ($LASTEXITCODE -ne 0) {
        throw "curl failed: $($args -join ' ')"
    }
}

function Find-ForwardLine {
    param(
        [string[]]$Forwards,
        [int]$Port
    )
    $local = "tcp:$Port"
    return @($Forwards | Where-Object {
        $parts = $_.Trim() -split "\s+"
        $parts.Count -ge 3 -and $parts[1] -eq $local
    })
}

$Devices = & $Adb devices -l
if ($LASTEXITCODE -ne 0) {
    throw "adb devices failed"
}
$DeviceLines = @($Devices | Where-Object { $_ -match "\sdevice\s" -and $_ -notmatch "^List of devices" })
if ($DeviceLines.Count -eq 0) {
    $Devices | Out-String | Write-Host
    throw "no authorized adb device found"
}

$ExistingForwards = & $Adb forward --list
if ($LASTEXITCODE -ne 0) {
    throw "adb forward --list failed"
}
$ExistingLocalForwards = @(Find-ForwardLine -Forwards $ExistingForwards -Port $LocalPort)
if ($ExistingLocalForwards.Count -gt 0) {
    $ExistingLocalForwards | Out-String | Write-Host
    throw "tcp:$LocalPort is already forwarded; refusing to take ownership"
}

if ($Apk) {
    Invoke-Adb install -r $Apk
}

if (-not $NoStartAndroid) {
    Invoke-Adb shell am start-foreground-service -n "dev.pdalink.prototype/.ProxyService" -a "dev.pdalink.prototype.START_USB"
}

$CreatedForward = $false
try {
    Invoke-Adb forward "tcp:$LocalPort" "tcp:$RemotePort"
    $CreatedForward = $true

    Write-Host "== PAC probe =="
    Invoke-Curl --max-time 10 --fail "http://127.0.0.1:$LocalPort/proxy.pac"

    Write-Host "== HTTPS CONNECT smoke =="
    Invoke-Curl --max-time 30 --fail -x "http://127.0.0.1:$LocalPort" $Url

    Write-Host ""
    Write-Host "PdaLink Windows USB proxy smoke passed"
} finally {
    if ($CreatedForward -and -not $KeepForward) {
        & $Adb forward --remove "tcp:$LocalPort" | Out-Host
    }
    if (-not $NoStopAndroid) {
        & $Adb shell am start-foreground-service -n "dev.pdalink.prototype/.ProxyService" -a "dev.pdalink.prototype.STOP" | Out-Host
    }
}
