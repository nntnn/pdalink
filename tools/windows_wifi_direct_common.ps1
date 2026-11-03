function Write-PdaLinkStep {
    param([Parameter(Mandatory = $true)][string]$Message)

    Write-Host ""
    Write-Host "== $Message =="
}

function ConvertFrom-PdaLinkQueryComponent {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)

    try {
        return [Uri]::UnescapeDataString($Value.Replace("+", " "))
    } catch {
        throw "connection URI encoding is invalid"
    }
}

function Assert-PdaLinkPrivateIpv4 {
    param([Parameter(Mandatory = $true)][string]$Address)

    $parsed = [Net.IPAddress]::None
    if (-not [Net.IPAddress]::TryParse($Address, [ref]$parsed) -or
        $parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        throw "proxy endpoint must use an IPv4 address"
    }
    $bytes = $parsed.GetAddressBytes()
    $private = $bytes[0] -eq 10 -or
        ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or
        ($bytes[0] -eq 192 -and $bytes[1] -eq 168)
    if (-not $private) {
        throw "proxy endpoint must use a private IPv4 address"
    }
    return $parsed.ToString()
}

function ConvertFrom-PdaLinkConnectionUri {
    param([Parameter(Mandatory = $true)][string]$ConnectionUri)

    $raw = $ConnectionUri.Trim()
    if (-not $raw) {
        throw "connection URI is empty"
    }
    try {
        $uri = [Uri]$raw
    } catch {
        throw "connection URI is malformed"
    }
    if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne "pdalink" -or $uri.Host -ne "connect" -or
        $uri.UserInfo -or $uri.Port -ne -1 -or $uri.AbsolutePath -notin @("", "/") -or $uri.Fragment) {
        throw "connection URI target is invalid"
    }

    $allowed = @{
        v = $true
        transport = $true
        ssid = $true
        security = $true
        password = $true
        proxy = $true
    }
    $values = @{}
    $query = $uri.Query.TrimStart("?")
    if (-not $query) {
        throw "connection URI has no parameters"
    }
    foreach ($entry in $query.Split("&")) {
        $separator = $entry.IndexOf("=")
        if ($separator -le 0) {
            throw "connection URI parameter is malformed"
        }
        $key = ConvertFrom-PdaLinkQueryComponent -Value $entry.Substring(0, $separator)
        $value = ConvertFrom-PdaLinkQueryComponent -Value $entry.Substring($separator + 1)
        if (-not $allowed.ContainsKey($key)) {
            throw "unknown connection URI parameter: $key"
        }
        if ($values.ContainsKey($key)) {
            throw "duplicate connection URI parameter: $key"
        }
        $values[$key] = $value
    }
    foreach ($required in @("v", "transport", "ssid", "security", "password", "proxy")) {
        if (-not $values.ContainsKey($required) -or -not [string]$values[$required]) {
            throw "missing connection URI parameter: $required"
        }
    }
    if ([string]$values.v -ne "1") {
        throw "unsupported connection URI version"
    }
    $transport = ([string]$values.transport).ToLowerInvariant()
    if ($transport -notin @("local-hotspot", "wifi-direct")) {
        throw "unsupported Wi-Fi transport"
    }
    $security = ([string]$values.security).ToLowerInvariant()
    if ($security -notin @("wpa2-psk", "wpa3-sae", "wpa2-wpa3")) {
        throw "unsupported Wi-Fi security mode"
    }
    $ssid = [string]$values.ssid
    $ssidBytes = [Text.Encoding]::UTF8.GetByteCount($ssid)
    if ($ssidBytes -lt 1 -or $ssidBytes -gt 32 -or $ssid.Contains([char]0)) {
        throw "SSID must be between 1 and 32 UTF-8 bytes"
    }
    $password = [string]$values.password
    if ($password.Length -lt 8 -or $password.Length -gt 63) {
        throw "Wi-Fi password must be between 8 and 63 characters"
    }
    foreach ($character in $password.ToCharArray()) {
        if ([int]$character -lt 0x20 -or [int]$character -gt 0x7e) {
            throw "Wi-Fi password must contain printable ASCII characters"
        }
    }

    try {
        $proxy = [Uri]([string]$values.proxy)
    } catch {
        throw "proxy endpoint is malformed"
    }
    if (-not $proxy.IsAbsoluteUri -or $proxy.Scheme -ne "http" -or $proxy.UserInfo -or
        $proxy.Port -lt 1 -or $proxy.Port -gt 65535 -or $proxy.AbsolutePath -notin @("", "/") -or
        $proxy.Query -or $proxy.Fragment) {
        throw "proxy must be an HTTP endpoint on a private IPv4 address"
    }
    $proxyHost = Assert-PdaLinkPrivateIpv4 -Address $proxy.Host
    return [pscustomobject]@{
        version = 1
        transport = $transport
        ssid = $ssid
        security = $security
        password = $password
        proxy_url = "http://${proxyHost}:$($proxy.Port)"
        proxy_address = $proxyHost
        proxy_port = [int]$proxy.Port
    }
}

function Invoke-PdaLinkNetsh {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & netsh.exe @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "netsh failed: $($Arguments -join ' ')"
    }
}

function Test-PdaLinkWifiCredential {
    param(
        [Parameter(Mandatory = $true)][string]$Ssid,
        [Parameter(Mandatory = $true)][Security.SecureString]$Password,
        [ValidateSet("wpa2-psk", "wpa3-sae", "wpa2-wpa3")]
        [string]$Security = "wpa2-psk"
    )

    $ssidBytes = [Text.Encoding]::UTF8.GetByteCount($Ssid)
    if ($ssidBytes -lt 1 -or $ssidBytes -gt 32) {
        throw "SSID must be between 1 and 32 UTF-8 bytes"
    }
    if ($Password.Length -lt 8 -or $Password.Length -gt 63) {
        throw "Wi-Fi Direct password must be between 8 and 63 characters"
    }
}

function New-PdaLinkWifiProfile {
    param(
        [Parameter(Mandatory = $true)][string]$Ssid,
        [Parameter(Mandatory = $true)][Security.SecureString]$Password,
        [ValidateSet("wpa2-psk", "wpa3-sae", "wpa2-wpa3")]
        [string]$Security = "wpa2-psk",
        [string]$Interface = ""
    )

    Test-PdaLinkWifiCredential -Ssid $Ssid -Password $Password -Security $Security
    $profileName = "PdaLink-WiFi-Direct-$([Guid]::NewGuid().ToString('N'))"
    $tempPath = Join-Path ([IO.Path]::GetTempPath()) "$profileName.xml"
    $bstr = [IntPtr]::Zero
    $plainPassword = $null
    $writer = $null

    try {
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
        $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)

        $settings = [Xml.XmlWriterSettings]::new()
        $settings.Encoding = [Text.UTF8Encoding]::new($false)
        $settings.Indent = $true
        $settings.CloseOutput = $true
        $writer = [Xml.XmlWriter]::Create($tempPath, $settings)

        $namespace = "http://www.microsoft.com/networking/WLAN/profile/v1"
        $writer.WriteStartDocument()
        $writer.WriteStartElement("WLANProfile", $namespace)
        $writer.WriteElementString("name", $namespace, $profileName)
        $writer.WriteStartElement("SSIDConfig", $namespace)
        $writer.WriteStartElement("SSID", $namespace)
        $ssidHex = [BitConverter]::ToString([Text.Encoding]::UTF8.GetBytes($Ssid)).Replace("-", "")
        $writer.WriteElementString("hex", $namespace, $ssidHex)
        $writer.WriteElementString("name", $namespace, $Ssid)
        $writer.WriteEndElement()
        $writer.WriteElementString("nonBroadcast", $namespace, "false")
        $writer.WriteEndElement()
        $writer.WriteElementString("connectionType", $namespace, "ESS")
        $writer.WriteElementString("connectionMode", $namespace, "auto")
        $writer.WriteElementString("autoSwitch", $namespace, "false")
        $writer.WriteStartElement("MSM", $namespace)
        $writer.WriteStartElement("security", $namespace)
        $writer.WriteStartElement("authEncryption", $namespace)
        $authentication = if ($Security -eq "wpa3-sae") { "WPA3SAE" } else { "WPA2PSK" }
        $writer.WriteElementString("authentication", $namespace, $authentication)
        $writer.WriteElementString("encryption", $namespace, "AES")
        $writer.WriteElementString("useOneX", $namespace, "false")
        $writer.WriteEndElement()
        $writer.WriteStartElement("sharedKey", $namespace)
        $writer.WriteElementString("keyType", $namespace, "passPhrase")
        $writer.WriteElementString("protected", $namespace, "false")
        $writer.WriteElementString("keyMaterial", $namespace, $plainPassword)
        $writer.WriteEndElement()
        $writer.WriteEndElement()
        $writer.WriteEndElement()
        $writer.WriteEndElement()
        $writer.WriteEndDocument()
        $writer.Flush()
        $writer.Close()
        $writer = $null

        $arguments = @("wlan", "add", "profile", "filename=$tempPath", "user=current")
        if ($Interface) {
            $arguments += "interface=$Interface"
        }
        Invoke-PdaLinkNetsh -Arguments $arguments
    } finally {
        if ($null -ne $writer) {
            $writer.Dispose()
        }
        $plainPassword = $null
        if ($bstr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
        Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
    }

    return $profileName
}

function Connect-PdaLinkWifiDirect {
    param(
        [Parameter(Mandatory = $true)][string]$Ssid,
        [Parameter(Mandatory = $true)][Security.SecureString]$Password,
        [ValidateSet("wpa2-psk", "wpa3-sae", "wpa2-wpa3")]
        [string]$Security = "wpa2-psk",
        [string]$Interface = ""
    )

    $profileName = New-PdaLinkWifiProfile -Ssid $Ssid -Password $Password -Security $Security -Interface $Interface
    try {
        $arguments = @("wlan", "connect", "name=$profileName")
        if ($Interface) {
            $arguments += "interface=$Interface"
        }
        Invoke-PdaLinkNetsh -Arguments $arguments
    } catch {
        try {
            Remove-PdaLinkWifiProfile -ProfileName $profileName -Interface $Interface
        } catch {
            Write-Warning "Could not remove failed PdaLink WLAN profile $profileName"
        }
        throw
    }

    return [pscustomobject]@{
        profile_name = $profileName
        ssid = $Ssid
        requested_interface = $Interface
        created_profile = $true
    }
}

function Remove-PdaLinkWifiProfile {
    param(
        [Parameter(Mandatory = $true)][string]$ProfileName,
        [string]$Interface = ""
    )

    if ($ProfileName -notmatch '^PdaLink-WiFi-Direct-[0-9a-f]{32}$') {
        throw "refusing to delete WLAN profile not owned by PdaLink: $ProfileName"
    }
    $arguments = @("wlan", "delete", "profile", "name=$ProfileName")
    if ($Interface) {
        $arguments += "interface=$Interface"
    }
    Invoke-PdaLinkNetsh -Arguments $arguments
}

function Get-PdaLinkBestRoute {
    param([Parameter(Mandatory = $true)][string]$RemoteAddress)

    $result = @(Find-NetRoute -RemoteIPAddress $RemoteAddress -ErrorAction Stop)
    $address = @($result | Where-Object {
        $_.PSObject.Properties.Name -contains "IPAddress" -and
        $_.PSObject.Properties.Name -contains "InterfaceIndex"
    } | Select-Object -First 1)
    $route = @($result | Where-Object {
        $_.PSObject.Properties.Name -contains "DestinationPrefix" -and
        $_.PSObject.Properties.Name -contains "NextHop"
    } | Select-Object -First 1)
    if ($address.Count -ne 1 -or $route.Count -ne 1) {
        throw "could not resolve the selected Windows route to $RemoteAddress"
    }

    return [pscustomobject]@{
        source_address = [string]$address[0].IPAddress
        interface_index = [int]$route[0].InterfaceIndex
        interface_alias = [string]$route[0].InterfaceAlias
        destination_prefix = [string]$route[0].DestinationPrefix
        next_hop = [string]$route[0].NextHop
    }
}

function Wait-PdaLinkWifiDirectRoute {
    param(
        [string]$ProxyAddress = "192.168.49.1",
        [string]$Interface = "",
        [int]$TimeoutSeconds = 25
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastError = "route not ready"
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $route = Get-PdaLinkBestRoute -RemoteAddress $ProxyAddress
            [void](Assert-PdaLinkPrivateIpv4 -Address $ProxyAddress)
            $physical = @(Get-NetAdapter -IncludeHidden | Where-Object {
                $_.ifIndex -eq $route.interface_index -and $_.HardwareInterface
            })
            if ($physical.Count -ne 1) {
                $lastError = "proxy route is not on a physical adapter"
            } elseif ($Interface -and $route.interface_alias -ne $Interface) {
                $lastError = "selected interface $($route.interface_alias) is not requested interface $Interface"
            } elseif ($route.next_hop -notin @("0.0.0.0", "")) {
                $lastError = "selected route uses unexpected next hop $($route.next_hop)"
            } else {
                return $route
            }
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Wi-Fi Direct route to $ProxyAddress did not become ready: $lastError"
}

function Get-PdaLinkPacDirect {
    param(
        [string]$PacUrl = "http://192.168.49.1:8001/proxy.pac",
        [int]$TimeoutSeconds = 10
    )

    $output = @(& curl.exe --silent --show-error --fail --max-time $TimeoutSeconds --noproxy "*" $PacUrl 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "direct PAC request failed: $($output -join [Environment]::NewLine)"
    }
    return ($output -join [Environment]::NewLine)
}

function Assert-PdaLinkPac {
    param(
        [Parameter(Mandatory = $true)][string]$Pac,
        [string]$ExpectedEndpoint = "192.168.49.1:8001"
    )

    $expectedHost = $ExpectedEndpoint.Split(':')[0]
    $expected = @(
        "function FindProxyForURL(url, host) {",
        "  if (host == `"$expectedHost`") return `"DIRECT`";",
        "  return `"PROXY $ExpectedEndpoint; DIRECT`";",
        "}"
    ) -join "`n"
    $normalized = $Pac.Replace("`r`n", "`n").Replace("`r", "`n").TrimEnd("`n")
    if ($normalized -cne $expected) {
        throw "reachable PAC is untrusted: body does not match the canonical PdaLink PAC"
    }
}

function Test-PdaLinkWifiDirectEndpoint {
    param(
        [string]$ProxyAddress = "192.168.49.1",
        [int]$ProxyPort = 8001,
        [string]$Interface = "",
        [int]$TimeoutSeconds = 25
    )

    $route = Wait-PdaLinkWifiDirectRoute -ProxyAddress $ProxyAddress -Interface $Interface -TimeoutSeconds $TimeoutSeconds
    $pacUrl = "http://${ProxyAddress}:$ProxyPort/proxy.pac"
    $pac = Get-PdaLinkPacDirect -PacUrl $pacUrl
    Assert-PdaLinkPac -Pac $pac -ExpectedEndpoint "${ProxyAddress}:$ProxyPort"
    return [pscustomobject]@{
        route = $route
        pac_url = $pacUrl
        pac = $pac
    }
}
