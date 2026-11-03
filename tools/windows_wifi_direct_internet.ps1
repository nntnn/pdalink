param(
    [ValidateSet("start", "stop", "status", "test")]
    [string]$Action = "start",
    [string]$Ssid = "",
    [Security.SecureString]$Password,
    [ValidateSet("wpa2-psk", "wpa3-sae", "wpa2-wpa3")]
    [string]$Security = "wpa2-psk",
    [string]$Interface = "",
    [ValidateSet("wifi-direct", "local-hotspot")]
    [string]$Transport = "wifi-direct",
    [string]$ProxyAddress = "192.168.49.1",
    [int]$ProxyPort = 8001,
    [string]$Url = "https://ifconfig.me",
    [string]$StatePath = "",
    [switch]$KeepWifi,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "windows_wifi_direct_common.ps1")

[void](Assert-PdaLinkPrivateIpv4 -Address $ProxyAddress)
if ($ProxyPort -lt 1 -or $ProxyPort -gt 65535) {
    throw "ProxyPort must be between 1 and 65535"
}
$PacUrl = "http://${ProxyAddress}:$ProxyPort/proxy.pac"
$ExpectedProxyEndpoint = "${ProxyAddress}:$ProxyPort"
$InternetSettingsPath = "Software\Microsoft\Windows\CurrentVersion\Internet Settings"
$ProxyValueNames = @("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL", "AutoDetect")
$AllowedRegistryKinds = @("DWord", "QWord", "Binary", "MultiString", "String", "ExpandString")
$StateOwner = "pdalink-windows-wifi-direct-internet"

if (-not $StatePath) {
    if (-not $env:LOCALAPPDATA) {
        throw "LOCALAPPDATA is not available; pass -StatePath explicitly"
    }
    $StatePath = Join-Path $env:LOCALAPPDATA "PdaLink\runtime\windows-wifi-direct-internet.json"
}
if ($Force -and $Action -ne "stop") {
    throw "-Force is accepted only with stop"
}

function Get-InternetSettingsValues {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($InternetSettingsPath, $false)
    if ($null -eq $key) {
        throw "HKCU:\$InternetSettingsPath not found"
    }
    try {
        $existingNames = @($key.GetValueNames())
        foreach ($name in $ProxyValueNames) {
            if ($existingNames -contains $name) {
                [pscustomobject]@{
                    name = $name
                    exists = $true
                    kind = $key.GetValueKind($name).ToString()
                    value = $key.GetValue(
                        $name,
                        $null,
                        [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
                    )
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
    } finally {
        $key.Close()
    }
}

function Convert-RegistryValueForKind {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$Kind
    )

    switch ($Kind) {
        "DWord" { return [int]$Value }
        "QWord" { return [long]$Value }
        "Binary" { return [byte[]]@($Value) }
        "MultiString" { return [string[]]@($Value) }
        "String" { return [string]$Value }
        "ExpandString" { return [string]$Value }
        default { throw "unsupported registry value kind in restore state: $Kind" }
    }
}

function Set-InternetSettingsEntry {
    param([Parameter(Mandatory = $true)][object]$Entry)

    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($InternetSettingsPath, $true)
    if ($null -eq $key) {
        throw "HKCU:\$InternetSettingsPath not found"
    }
    try {
        if (-not [bool]$Entry.exists) {
            $key.DeleteValue([string]$Entry.name, $false)
            return
        }
        $kindName = [string]$Entry.kind
        if ($kindName -notin $AllowedRegistryKinds) {
            throw "unsupported registry value kind in restore state: $kindName"
        }
        $kind = [Enum]::Parse([Microsoft.Win32.RegistryValueKind], $kindName)
        $value = Convert-RegistryValueForKind -Value $Entry.value -Kind $kindName
        $key.SetValue([string]$Entry.name, $value, $kind)
    } finally {
        $key.Close()
    }
}

function Set-InternetSettingsValues {
    param([Parameter(Mandatory = $true)][object[]]$Values)

    foreach ($entry in $Values) {
        Set-InternetSettingsEntry -Entry $entry
    }
}

function New-PdaLinkAppliedValues {
    param([Parameter(Mandatory = $true)][object[]]$Before)

    foreach ($entry in $Before) {
        switch ([string]$entry.name) {
            "ProxyEnable" {
                [pscustomobject]@{ name = "ProxyEnable"; exists = $true; kind = "DWord"; value = 0 }
            }
            "AutoConfigURL" {
                [pscustomobject]@{ name = "AutoConfigURL"; exists = $true; kind = "String"; value = $PacUrl }
            }
            "AutoDetect" {
                [pscustomobject]@{ name = "AutoDetect"; exists = $true; kind = "DWord"; value = 0 }
            }
            default {
                [pscustomobject]@{
                    name = [string]$entry.name
                    exists = [bool]$entry.exists
                    kind = [string]$entry.kind
                    value = $entry.value
                }
            }
        }
    }
}

function Test-InternetSettingsValuesEqual {
    param(
        [Parameter(Mandatory = $true)][object[]]$Left,
        [Parameter(Mandatory = $true)][object[]]$Right
    )

    return (($Left | ConvertTo-Json -Depth 6 -Compress) -eq ($Right | ConvertTo-Json -Depth 6 -Compress))
}

function Send-InternetSettingsChanged {
    if (-not ("PdaLinkWifiDirectWinInet" -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class PdaLinkWifiDirectWinInet
{
    [DllImport("wininet.dll", SetLastError = true)]
    public static extern bool InternetSetOption(IntPtr hInternet, int dwOption, IntPtr lpBuffer, int dwBufferLength);
}
"@
    }
    foreach ($option in @(39, 37)) {
        if (-not [PdaLinkWifiDirectWinInet]::InternetSetOption([IntPtr]::Zero, $option, [IntPtr]::Zero, 0)) {
            $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            throw "InternetSetOption($option) failed with Win32 error $errorCode"
        }
    }
}

function Assert-RegistryValuesShape {
    param([Parameter(Mandatory = $true)][object[]]$Values)

    if ($Values.Count -ne $ProxyValueNames.Count) {
        throw "invalid proxy restore state: wrong registry value count"
    }
    $seen = @{}
    foreach ($entry in $Values) {
        $properties = @($entry.PSObject.Properties.Name)
        foreach ($required in @("name", "exists", "kind", "value")) {
            if ($properties -notcontains $required) {
                throw "invalid proxy restore state: registry entry missing $required"
            }
        }
        $name = [string]$entry.name
        if ($name -notin $ProxyValueNames -or $seen.ContainsKey($name)) {
            throw "invalid proxy restore state: unexpected or duplicate value $name"
        }
        $seen[$name] = $true
        if ([bool]$entry.exists -and ([string]$entry.kind -notin $AllowedRegistryKinds)) {
            throw "invalid proxy restore state: unsupported kind for $name"
        }
        if (-not [bool]$entry.exists -and [string]$entry.kind) {
            throw "invalid proxy restore state: absent value $name has a registry kind"
        }
    }
}

function Assert-PdaLinkProxyState {
    param([Parameter(Mandatory = $true)][object]$State)

    if ([int]$State.version -ne 1 -or [string]$State.owner -ne $StateOwner) {
        throw "invalid PdaLink proxy restore state owner or version"
    }
    if ([string]$State.transport -notin @("wifi-direct", "local-hotspot") -or
        [string]$State.manager -ne "wininet-pac") {
        throw "invalid PdaLink proxy restore state transport"
    }
    if ([string]$State.pac_url -ne $PacUrl -or [string]$State.proxy_endpoint -ne $ExpectedProxyEndpoint) {
        throw "invalid PdaLink proxy restore state endpoint"
    }
    if ([string]$State.profile_name -notmatch '^PdaLink-WiFi-Direct-[0-9a-f]{32}$') {
        throw "invalid PdaLink-owned WLAN profile in restore state"
    }
    Assert-RegistryValuesShape -Values @($State.before)
    Assert-RegistryValuesShape -Values @($State.applied)
    $expectedApplied = @(New-PdaLinkAppliedValues -Before @($State.before))
    if (-not (Test-InternetSettingsValuesEqual -Left @($State.applied) -Right $expectedApplied)) {
        throw "invalid PdaLink applied proxy snapshot"
    }
}

function Write-PdaLinkProxyStateExclusive {
    param([Parameter(Mandatory = $true)][object]$State)

    $directory = Split-Path -Parent $StatePath
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($State | ConvertTo-Json -Depth 8))
    $stream = $null
    try {
        $stream = [IO.File]::Open($StatePath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}

function Read-PdaLinkProxyState {
    if (-not (Test-Path -LiteralPath $StatePath)) {
        throw "PdaLink Wi-Fi Direct proxy state not found: $StatePath"
    }
    try {
        $utf8 = [Text.UTF8Encoding]::new($false, $true)
        $state = [IO.File]::ReadAllText($StatePath, $utf8) | ConvertFrom-Json
    } catch {
        throw "invalid PdaLink proxy restore state $StatePath`: $($_.Exception.Message)"
    }
    try {
        $endpointUri = [Uri]("http://" + [string]$state.proxy_endpoint)
        $endpointHost = Assert-PdaLinkPrivateIpv4 -Address $endpointUri.Host
        if ($endpointUri.Port -lt 1 -or $endpointUri.Port -gt 65535 -or
            $endpointUri.AbsolutePath -notin @("", "/") -or $endpointUri.Query -or $endpointUri.Fragment) {
            throw "invalid endpoint"
        }
    } catch {
        throw "invalid PdaLink proxy restore state endpoint"
    }
    $script:ProxyAddress = $endpointHost
    $script:ProxyPort = [int]$endpointUri.Port
    $script:Transport = [string]$state.transport
    $script:PacUrl = "http://${ProxyAddress}:$ProxyPort/proxy.pac"
    $script:ExpectedProxyEndpoint = "${ProxyAddress}:$ProxyPort"
    Assert-PdaLinkProxyState -State $state
    return $state
}

function Invoke-PdaLinkExplicitProxyTest {
    $proxyUrl = "http://$ExpectedProxyEndpoint"
    & curl.exe --silent --show-error --fail --max-time 30 --proxy $proxyUrl --noproxy "" $Url | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "explicit Wi-Fi Direct proxy test failed for $Url"
    }
}

function Test-PdaLinkSystemProxy {
    $uri = [Uri]$Url
    $proxy = [System.Net.WebRequest]::GetSystemWebProxy()
    $proxyUri = $proxy.GetProxy($uri)
    Write-Host "system proxy resolution for $Url -> $proxyUri"
    if ($proxyUri.Host -ne $ProxyAddress -or $proxyUri.Port -ne $ProxyPort) {
        throw "system PAC did not resolve to $ExpectedProxyEndpoint"
    }

    [System.Net.WebRequest]::DefaultWebProxy = $proxy
    [System.Net.WebRequest]::DefaultWebProxy.Credentials = [System.Net.CredentialCache]::DefaultCredentials
    $request = [System.Net.HttpWebRequest]::Create($uri)
    $request.Method = "GET"
    $request.UserAgent = "PdaLink-Windows-WiFi-Direct"
    $request.Timeout = 30000
    $response = $request.GetResponse()
    try {
        Write-Host "HTTP status: $([int]$response.StatusCode) $($response.StatusDescription)"
        $reader = [IO.StreamReader]::new($response.GetResponseStream())
        try {
            $body = $reader.ReadToEnd()
            if ($body.Length -gt 300) {
                $body = $body.Substring(0, 300)
            }
            Write-Host $body
        } finally {
            $reader.Dispose()
        }
    } finally {
        $response.Dispose()
    }
}

function Restore-PdaLinkProxyFromState {
    param([Parameter(Mandatory = $true)][object]$State)

    Set-InternetSettingsValues -Values @($State.before)
    Send-InternetSettingsChanged
    $restored = @(Get-InternetSettingsValues)
    if (-not (Test-InternetSettingsValuesEqual -Left $restored -Right @($State.before))) {
        throw "Windows proxy values did not match the saved snapshot after restore"
    }
}

function Start-PdaLinkWifiDirectInternet {
    if (Test-Path -LiteralPath $StatePath) {
        throw "PdaLink proxy restore state already exists: $StatePath. Run stop first."
    }
    if (-not $Ssid) {
        throw "start requires -Ssid from the Android app"
    }
    if ($null -eq $Password) {
        $script:Password = Read-Host "Wi-Fi Direct password shown by Android" -AsSecureString
    }

    $wifi = $null
    $state = $null
    $stateWritten = $false
    $rollbackComplete = $false
    $profileCleanupComplete = $false
    try {
        Write-PdaLinkStep "Connecting Windows to Android Wi-Fi"
        $wifi = Connect-PdaLinkWifiDirect -Ssid $Ssid -Password $Password -Security $Security -Interface $Interface

        Write-PdaLinkStep "Verifying trusted Wi-Fi route and PAC"
        $endpoint = Test-PdaLinkWifiDirectEndpoint -ProxyAddress $ProxyAddress -ProxyPort $ProxyPort -Interface $Interface
        Invoke-PdaLinkExplicitProxyTest

        Write-PdaLinkStep "Saving and applying the Windows user PAC"
        $before = @(Get-InternetSettingsValues)
        $applied = @(New-PdaLinkAppliedValues -Before $before)
        $state = [pscustomobject]@{
            version = 1
            owner = $StateOwner
            transport = $Transport
            security = $Security
            manager = "wininet-pac"
            created_at = [DateTime]::UtcNow.ToString("o")
            pac_url = $PacUrl
            proxy_endpoint = $ExpectedProxyEndpoint
            ssid = $Ssid
            profile_name = [string]$wifi.profile_name
            requested_interface = $Interface
            interface_alias = [string]$endpoint.route.interface_alias
            interface_index = [int]$endpoint.route.interface_index
            source_address = [string]$endpoint.route.source_address
            before = $before
            applied = $applied
        }
        Write-PdaLinkProxyStateExclusive -State $state
        $stateWritten = $true
        Set-InternetSettingsValues -Values $applied
        Send-InternetSettingsChanged
        $current = @(Get-InternetSettingsValues)
        if (-not (Test-InternetSettingsValuesEqual -Left $current -Right $applied)) {
            throw "Windows did not retain the PdaLink PAC settings"
        }

        Write-PdaLinkStep "Testing Windows system proxy"
        Test-PdaLinkSystemProxy
        Write-Host ""
        Write-Host "PdaLink Windows Wi-Fi Direct proxy internet is active."
        Write-Host "Stop with: powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_internet.ps1 stop"
    } catch {
        Write-PdaLinkStep "Rolling back failed Wi-Fi Direct proxy start"
        if ($stateWritten -and $null -ne $state) {
            try {
                Restore-PdaLinkProxyFromState -State $state
                $rollbackComplete = $true
            } catch {
                Write-Warning "Proxy rollback failed; restore state retained at $StatePath`: $($_.Exception.Message)"
            }
        } else {
            $rollbackComplete = $true
        }
        if ($rollbackComplete -and $null -ne $wifi) {
            try {
                Remove-PdaLinkWifiProfile -ProfileName ([string]$wifi.profile_name) -Interface $Interface
                $profileCleanupComplete = $true
            } catch {
                Write-Warning "Could not remove the PdaLink WLAN profile after rollback; restore state retained for stop retry: $($_.Exception.Message)"
            }
        } elseif ($null -eq $wifi) {
            $profileCleanupComplete = $true
        }
        if ($stateWritten -and $rollbackComplete -and $profileCleanupComplete) {
            Remove-Item -LiteralPath $StatePath -Force
        }
        throw
    }
}

function Get-PdaLinkProxyLifecycleStatus {
    if (-not (Test-Path -LiteralPath $StatePath)) {
        return [pscustomobject]@{ state = "inactive"; detail = "no restore state"; payload = $null }
    }
    try {
        $state = Read-PdaLinkProxyState
    } catch {
        return [pscustomobject]@{ state = "invalid"; detail = $_.Exception.Message; payload = $null }
    }
    $current = @(Get-InternetSettingsValues)
    $owned = Test-InternetSettingsValuesEqual -Left $current -Right @($state.applied)
    $restored = Test-InternetSettingsValuesEqual -Left $current -Right @($state.before)
    if (-not $owned -and -not $restored) {
        return [pscustomobject]@{ state = "drifted"; detail = "WinINET values changed after PdaLink start"; payload = $state }
    }
    if ($owned) {
        try {
        [void](Test-PdaLinkWifiDirectEndpoint -ProxyAddress $ProxyAddress -ProxyPort $ProxyPort -Interface ([string]$state.interface_alias) -TimeoutSeconds 3)
        return [pscustomobject]@{ state = "active"; detail = "PAC, route, and WinINET ownership verified"; payload = $state }
        } catch {
            return [pscustomobject]@{ state = "unreachable"; detail = $_.Exception.Message; payload = $state }
        }
    }
    return [pscustomobject]@{ state = "inactive"; detail = "proxy restored; WLAN cleanup pending"; payload = $state }
}

function Show-PdaLinkWifiDirectInternetStatus {
    $status = Get-PdaLinkProxyLifecycleStatus
    Write-Host "PdaLink Windows Wi-Fi Direct proxy internet"
    Write-Host "state: $($status.state)"
    Write-Host "detail: $($status.detail)"
    Write-Host "PAC: $PacUrl"
    Write-Host "restore state: $StatePath"
    if ($null -ne $status.payload) {
        Write-Host "SSID: $($status.payload.ssid)"
        Write-Host "interface: $($status.payload.interface_alias) ($($status.payload.interface_index))"
        Write-Host "source: $($status.payload.source_address)"
    }
}

function Test-PdaLinkWifiDirectInternet {
    $status = Get-PdaLinkProxyLifecycleStatus
    if ($status.state -ne "active") {
        throw "PdaLink Wi-Fi Direct proxy is not active: $($status.state) - $($status.detail)"
    }
    Test-PdaLinkSystemProxy
}

function Stop-PdaLinkWifiDirectInternet {
    $state = Read-PdaLinkProxyState
    $current = @(Get-InternetSettingsValues)
    $owned = Test-InternetSettingsValuesEqual -Left $current -Right @($state.applied)
    $alreadyRestored = Test-InternetSettingsValuesEqual -Left $current -Right @($state.before)
    if (-not $owned -and -not $alreadyRestored -and -not $Force) {
        throw "Windows proxy settings drifted after PdaLink start; refusing to overwrite them. Inspect status, then use stop -Force only for deliberate snapshot recovery."
    }

    if (-not $alreadyRestored) {
        Write-PdaLinkStep "Restoring the previous Windows user proxy"
        Restore-PdaLinkProxyFromState -State $state
    } else {
        Write-Host "Windows user proxy already matches the saved pre-PdaLink snapshot."
    }

    if (-not $KeepWifi) {
        Write-PdaLinkStep "Removing the PdaLink-owned Wi-Fi profile"
        Remove-PdaLinkWifiProfile -ProfileName ([string]$state.profile_name) -Interface ([string]$state.requested_interface)
    } else {
        Write-Host "Keeping PdaLink WLAN profile and current Wi-Fi connection by request."
    }

    Remove-Item -LiteralPath $StatePath -Force
    Write-Host "PdaLink Windows Wi-Fi Direct proxy internet is stopped."
}

switch ($Action) {
    "start" { Start-PdaLinkWifiDirectInternet }
    "stop" { Stop-PdaLinkWifiDirectInternet }
    "status" { Show-PdaLinkWifiDirectInternetStatus }
    "test" { Test-PdaLinkWifiDirectInternet }
}
