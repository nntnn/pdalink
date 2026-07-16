function Write-PdaLinkStep {
    param([Parameter(Mandatory = $true)][string]$Message)

    Write-Host ""
    Write-Host "== $Message =="
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
        [Parameter(Mandatory = $true)][Security.SecureString]$Password
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
        [string]$Interface = ""
    )

    Test-PdaLinkWifiCredential -Ssid $Ssid -Password $Password
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
        $writer.WriteElementString("connectionMode", $namespace, "manual")
        $writer.WriteElementString("autoSwitch", $namespace, "false")
        $writer.WriteStartElement("MSM", $namespace)
        $writer.WriteStartElement("security", $namespace)
        $writer.WriteStartElement("authEncryption", $namespace)
        $writer.WriteElementString("authentication", $namespace, "WPA2PSK")
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
        [string]$Interface = ""
    )

    $profileName = New-PdaLinkWifiProfile -Ssid $Ssid -Password $Password -Interface $Interface
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
            if ($route.source_address -notlike "192.168.49.*") {
                $lastError = "selected source $($route.source_address) is outside the Wi-Fi Direct subnet"
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
