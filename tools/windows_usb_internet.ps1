param(
    [ValidateSet("start", "stop", "status", "test")]
    [string]$Action = "start",
    [string]$Adb = "",
    [string]$Apk = "",
    [int]$LocalPort = 18080,
    [int]$RemotePort = 8000,
    [string]$Url = "https://ifconfig.me",
    [string]$ProxyBypass = "localhost;127.*;<local>",
    [string]$BackupPath = "",
    [switch]$NoStartAndroid,
    [switch]$NoStopAndroid,
    [switch]$KeepForward,
    [switch]$WinHttp,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $Adb) {
    $Adb = Join-Path $Root ".tools\platform-tools-windows\adb.exe"
}
if (-not $BackupPath) {
    $BackupPath = Join-Path $Root ".tools\runtime\windows-system-proxy-backup.json"
}

$InternetSettingsPath = "Software\Microsoft\Windows\CurrentVersion\Internet Settings"
$InternetSettingsRegistryPath = "HKCU:\$InternetSettingsPath"
$ProxyValueNames = @("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL", "AutoDetect")
$script:CreatedForward = $false
$script:ProxyBackupOwned = $false

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message =="
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

function Invoke-Netsh {
    & netsh @args
    if ($LASTEXITCODE -ne 0) {
        throw "netsh failed: $($args -join ' ')"
    }
}

function Require-Adb {
    if (-not (Test-Path $Adb)) {
        throw "adb.exe not found at $Adb. Install Windows platform-tools or pass -Adb."
    }
}

function Get-AdbDeviceLines {
    Require-Adb
    $devices = & $Adb devices -l
    if ($LASTEXITCODE -ne 0) {
        throw "adb devices failed"
    }
    return @($devices | Where-Object { $_ -match "\sdevice\s" -and $_ -notmatch "^List of devices" })
}

function Assert-AdbDevice {
    $deviceLines = @(Get-AdbDeviceLines)
    if ($deviceLines.Count -eq 0) {
        & $Adb devices -l | Out-Host
        throw "no authorized adb device found"
    }
}

function Get-AdbForwards {
    Require-Adb
    $forwards = & $Adb forward --list
    if ($LASTEXITCODE -ne 0) {
        throw "adb forward --list failed"
    }
    return @($forwards)
}

function Find-ForwardLine {
    param([int]$Port)
    $local = "tcp:$Port"
    return @(Get-AdbForwards | Where-Object {
        $parts = $_.Trim() -split "\s+"
        $parts.Count -ge 3 -and $parts[1] -eq $local
    })
}

function Ensure-Forward {
    $existing = @(Find-ForwardLine -Port $LocalPort)
    if ($existing.Count -gt 0) {
        $remote = "tcp:$RemotePort"
        $matching = @($existing | Where-Object {
            $parts = $_.Trim() -split "\s+"
            $parts.Count -ge 3 -and $parts[2] -eq $remote
        })
        if ($matching.Count -eq 0) {
            $existing | Out-String | Write-Host
            throw "tcp:$LocalPort is already forwarded to a different target; refusing to take ownership"
        }
        Write-Host "forward already present: $($matching[0])"
        return $false
    }

    Invoke-Adb forward "tcp:$LocalPort" "tcp:$RemotePort"
    Write-Host "created forward tcp:$LocalPort -> tcp:$RemotePort"
    return $true
}

function Remove-Forward {
    $existing = @(Find-ForwardLine -Port $LocalPort)
    if ($existing.Count -eq 0) {
        return
    }

    $remote = "tcp:$RemotePort"
    $matching = @($existing | Where-Object {
        $parts = $_.Trim() -split "\s+"
        $parts.Count -ge 3 -and $parts[2] -eq $remote
    })
    if ($matching.Count -eq 0) {
        $existing | Out-String | Write-Host
        Write-Warning "tcp:$LocalPort is no longer forwarded to tcp:$RemotePort; leaving current forward in place"
        return
    }

    & $Adb forward --remove "tcp:$LocalPort" | Out-Host
}

function Get-InternetSettingsSnapshot {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($InternetSettingsPath, $false)
    if ($null -eq $key) {
        throw "HKCU:\$InternetSettingsPath not found"
    }
    try {
        $names = @($key.GetValueNames())
        $values = foreach ($name in $ProxyValueNames) {
            $exists = $names -contains $name
            if ($exists) {
                [pscustomobject]@{
                    name = $name
                    exists = $true
                    kind = $key.GetValueKind($name).ToString()
                    value = $key.GetValue($name)
                }
            } else {
                [pscustomobject]@{
                    name = $name
                    exists = $false
                    kind = ""
                    value = $null
                }
            }
        }
        return [pscustomobject]@{
            created_at = (Get-Date).ToString("o")
            action = "pdalink-windows-usb-internet"
            local_port = $LocalPort
            remote_port = $RemotePort
            created_forward = [bool]$script:CreatedForward
            winhttp_managed = [bool]$WinHttp
            values = @($values)
            winhttp_before = (Get-WinHttpProxyText)
        }
    } finally {
        $key.Close()
    }
}

function Save-ProxyBackup {
    if (Test-Path $BackupPath) {
        if (-not $Force) {
            throw "proxy backup already exists: $BackupPath. Run stop first or pass -Force if you are recovering manually."
        }
        Write-Host "overwriting existing proxy backup: $BackupPath"
    }

    $directory = Split-Path -Parent $BackupPath
    if (-not (Test-Path $directory)) {
        New-Item -ItemType Directory -Path $directory | Out-Null
    }
    Get-InternetSettingsSnapshot | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 -Path $BackupPath
    $script:ProxyBackupOwned = $true
    Write-Host "saved proxy backup: $BackupPath"
}

function Get-WinHttpProxyText {
    $output = & netsh winhttp show proxy 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "netsh winhttp show proxy failed"
    }
    return ($output | Out-String)
}

function Test-WinHttpDirect {
    param([string]$Text)
    return $Text -match "Direct access"
}

function Assert-WinHttpCanBeManaged {
    $current = Get-WinHttpProxyText
    if (-not (Test-WinHttpDirect $current)) {
        throw "-WinHttp can only be used when the current WinHTTP proxy is direct. Current settings cannot be losslessly restored by this script."
    }
}

function Set-RegistryValue {
    param(
        [string]$Name,
        [object]$Value,
        [Microsoft.Win32.RegistryValueKind]$Kind
    )
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($InternetSettingsPath, $true)
    if ($null -eq $key) {
        throw "HKCU:\$InternetSettingsPath not found"
    }
    try {
        $key.SetValue($Name, $Value, $Kind)
    } finally {
        $key.Close()
    }
}

function Remove-RegistryValue {
    param([string]$Name)
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($InternetSettingsPath, $true)
    if ($null -eq $key) {
        return
    }
    try {
        $key.DeleteValue($Name, $false)
    } finally {
        $key.Close()
    }
}

function Convert-RegistryValueForKind {
    param(
        [object]$Value,
        [Microsoft.Win32.RegistryValueKind]$Kind
    )

    switch ($Kind.ToString()) {
        "DWord" { return [int]$Value }
        "QWord" { return [long]$Value }
        "Binary" { return [byte[]]@($Value) }
        "MultiString" { return [string[]]@($Value) }
        "String" { return [string]$Value }
        "ExpandString" { return [string]$Value }
        default { return $Value }
    }
}

function Enable-WindowsProxy {
    if ($WinHttp) {
        Assert-WinHttpCanBeManaged
    }
    Save-ProxyBackup
    $proxyServer = "http=127.0.0.1:$LocalPort;https=127.0.0.1:$LocalPort"
    Set-RegistryValue -Name "ProxyEnable" -Value 1 -Kind ([Microsoft.Win32.RegistryValueKind]::DWord)
    Set-RegistryValue -Name "ProxyServer" -Value $proxyServer -Kind ([Microsoft.Win32.RegistryValueKind]::String)
    Set-RegistryValue -Name "ProxyOverride" -Value $ProxyBypass -Kind ([Microsoft.Win32.RegistryValueKind]::String)
    Set-RegistryValue -Name "AutoDetect" -Value 0 -Kind ([Microsoft.Win32.RegistryValueKind]::DWord)
    Remove-RegistryValue -Name "AutoConfigURL"
    Send-InternetSettingsChanged
    Write-Host "enabled Windows user proxy: $proxyServer"

    if ($WinHttp) {
        Write-Host "importing WinHTTP proxy from current user settings"
        Invoke-Netsh winhttp import proxy source=ie | Out-Host
    }
}

function Restore-WindowsProxy {
    if (-not (Test-Path $BackupPath)) {
        if (-not $Force) {
            throw "proxy backup not found: $BackupPath. Pass -Force to disable the user proxy without restoring previous values."
        }
        Set-RegistryValue -Name "ProxyEnable" -Value 0 -Kind ([Microsoft.Win32.RegistryValueKind]::DWord)
        Send-InternetSettingsChanged
        Write-Host "disabled Windows user proxy without backup"
        return
    }

    $backup = Get-Content -Raw -Path $BackupPath | ConvertFrom-Json
    foreach ($entry in @($backup.values)) {
        if ($entry.exists) {
            $kind = [Microsoft.Win32.RegistryValueKind]::$($entry.kind)
            $value = Convert-RegistryValueForKind -Value $entry.value -Kind $kind
            Set-RegistryValue -Name $entry.name -Value $value -Kind $kind
        } else {
            Remove-RegistryValue -Name $entry.name
        }
    }
    Send-InternetSettingsChanged

    if (($backup.PSObject.Properties.Name -contains "winhttp_managed") -and $backup.winhttp_managed) {
        Write-Host "restoring WinHTTP direct proxy state"
        Invoke-Netsh winhttp reset proxy | Out-Host
    }

    Remove-Item -LiteralPath $BackupPath -Force
    Write-Host "restored Windows user proxy from backup"
}

function Send-InternetSettingsChanged {
    if (-not ("PdaLinkWinInet" -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class PdaLinkWinInet
{
    [DllImport("wininet.dll", SetLastError = true)]
    public static extern bool InternetSetOption(IntPtr hInternet, int dwOption, IntPtr lpBuffer, int dwBufferLength);
}
"@
    }
    [void][PdaLinkWinInet]::InternetSetOption([IntPtr]::Zero, 39, [IntPtr]::Zero, 0)
    [void][PdaLinkWinInet]::InternetSetOption([IntPtr]::Zero, 37, [IntPtr]::Zero, 0)
}

function Show-WindowsProxyStatus {
    Write-Host "WinINET user proxy:"
    $snapshot = Get-InternetSettingsSnapshot
    foreach ($entry in @($snapshot.values)) {
        if ($entry.exists) {
            Write-Host "  $($entry.name): $($entry.value)"
        } else {
            Write-Host "  $($entry.name): <unset>"
        }
    }
    Write-Host "Backup:"
    if (Test-Path $BackupPath) {
        Write-Host "  $BackupPath"
    } else {
        Write-Host "  none"
    }
    Write-Host "ADB forwards:"
    if (Test-Path $Adb) {
        Get-AdbForwards | ForEach-Object { Write-Host "  $_" }
    } else {
        Write-Host "  adb not found at $Adb"
    }
    Write-Host "WinHTTP proxy:"
    & netsh winhttp show proxy | ForEach-Object { Write-Host "  $_" }
}

function Test-SystemProxy {
    $uri = [Uri]$Url
    $proxy = [System.Net.WebRequest]::GetSystemWebProxy()
    $proxyUri = $proxy.GetProxy($uri)
    Write-Host "system proxy resolution for $Url -> $proxyUri"
    if ($proxyUri.Host -notin @("127.0.0.1", "localhost") -or $proxyUri.Port -ne $LocalPort) {
        throw "system proxy did not resolve to 127.0.0.1:$LocalPort"
    }

    [System.Net.WebRequest]::DefaultWebProxy = $proxy
    [System.Net.WebRequest]::DefaultWebProxy.Credentials = [System.Net.CredentialCache]::DefaultCredentials
    $request = [System.Net.HttpWebRequest]::Create($uri)
    $request.Method = "GET"
    $request.UserAgent = "PdaLink-Windows-System-Proxy-Smoke"
    $request.Timeout = 30000
    $response = $request.GetResponse()
    try {
        Write-Host "HTTP status: $([int]$response.StatusCode) $($response.StatusDescription)"
        $reader = [System.IO.StreamReader]::new($response.GetResponseStream())
        try {
            $body = $reader.ReadToEnd()
            if ($body.Length -gt 300) {
                $body = $body.Substring(0, 300)
            }
            Write-Host $body
        } finally {
            $reader.Close()
        }
    } finally {
        $response.Close()
    }
}

function Start-WindowsUsbInternet {
    $script:CreatedForward = $false
    $script:ProxyBackupOwned = $false
    $proxyChanged = $false
    Assert-AdbDevice
    try {
        if ($Apk) {
            Write-Step "Installing APK"
            Invoke-Adb install -r $Apk
        }
        if (-not $NoStartAndroid) {
            Write-Step "Starting Android proxy"
            Invoke-Adb shell am start-foreground-service -n "dev.pdalink.prototype/.ProxyService" -a "dev.pdalink.prototype.START_USB"
        }

        Write-Step "Preparing ADB forward"
        $script:CreatedForward = Ensure-Forward

        Write-Step "Checking forwarded proxy"
        Invoke-Curl --max-time 10 --fail "http://127.0.0.1:$LocalPort/proxy.pac"
        Invoke-Curl --max-time 30 --fail -x "http://127.0.0.1:$LocalPort" $Url

        Write-Step "Enabling Windows proxy"
        Enable-WindowsProxy
        $proxyChanged = $true

        Write-Step "Testing Windows system proxy"
        Test-SystemProxy
    } catch {
        Write-Step "Rolling back failed start"
        if ($proxyChanged -or $script:ProxyBackupOwned) {
            try { Restore-WindowsProxy } catch { Write-Warning $_ }
        }
        if ($script:CreatedForward) {
            try { Remove-Forward } catch { Write-Warning $_ }
        }
        if (-not $NoStopAndroid -and (Test-Path $Adb)) {
            try {
                & $Adb shell am start-foreground-service -n "dev.pdalink.prototype/.ProxyService" -a "dev.pdalink.prototype.STOP" | Out-Host
            } catch {
                Write-Warning $_
            }
        }
        throw
    }

    Write-Host ""
    Write-Host "PdaLink Windows USB internet is enabled for Windows proxy-aware traffic."
    Write-Host "Stop with: powershell -ExecutionPolicy Bypass -File .\tools\windows_usb_internet.ps1 stop"
}

function Stop-WindowsUsbInternet {
    $backup = $null
    if (Test-Path $BackupPath) {
        $backup = Get-Content -Raw -Path $BackupPath | ConvertFrom-Json
    }
    $shouldRemoveForward = $Force
    if ($null -ne $backup -and ($backup.PSObject.Properties.Name -contains "created_forward")) {
        $shouldRemoveForward = [bool]$backup.created_forward
    }

    Write-Step "Restoring Windows proxy"
    Restore-WindowsProxy

    if (-not $KeepForward -and $shouldRemoveForward) {
        Write-Step "Removing PdaLink ADB forward"
        Remove-Forward
    } elseif (-not $KeepForward) {
        Write-Step "Leaving pre-existing ADB forward in place"
    }

    if (-not $NoStopAndroid -and (Test-Path $Adb)) {
        Write-Step "Stopping Android proxy"
        & $Adb shell am start-foreground-service -n "dev.pdalink.prototype/.ProxyService" -a "dev.pdalink.prototype.STOP" | Out-Host
    }

    Write-Host ""
    Write-Host "PdaLink Windows USB internet is stopped."
}

switch ($Action) {
    "start" { Start-WindowsUsbInternet }
    "stop" { Stop-WindowsUsbInternet }
    "status" { Show-WindowsProxyStatus }
    "test" { Test-SystemProxy }
}
