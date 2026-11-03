param(
    [ValidateSet("connect", "internet", "show")]
    [string]$Action = "internet",
    [string]$ConnectionUri = "",
    [string]$Image = "",
    [switch]$Camera,
    [int]$CameraIndex = 0,
    [int]$ScanTimeout = 60,
    [string]$Interface = "",
    [string]$Python = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "windows_wifi_direct_common.ps1")

function Get-PdaLinkPython {
    if ($Python) {
        return [pscustomobject]@{ executable = $Python; prefix = @() }
    }
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) {
        return [pscustomobject]@{ executable = $pythonCommand.Source; prefix = @() }
    }
    $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -ne $pyCommand) {
        return [pscustomobject]@{ executable = $pyCommand.Source; prefix = @("-3") }
    }
    throw "Python 3 was not found; install Python and opencv-python for camera or image QR input"
}

function Read-PdaLinkQrWithPython {
    $pythonCommand = Get-PdaLinkPython
    $scanner = Join-Path $PSScriptRoot "scan_connection_qr.py"
    $arguments = @($pythonCommand.prefix) + @($scanner)
    if ($Image) {
        $arguments += @("--image", $Image)
    } else {
        $arguments += @("--camera", [string]$CameraIndex)
    }
    $arguments += @("--timeout", [string]$ScanTimeout)
    $output = @(& $pythonCommand.executable @arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "QR scan failed: $($output -join [Environment]::NewLine)"
    }
    return ($output -join "").Trim()
}

function Read-PdaLinkConnectionInput {
    $sourceCount = [int][bool]$ConnectionUri + [int][bool]$Image + [int][bool]$Camera
    if ($sourceCount -gt 1) {
        throw "choose only one of -ConnectionUri, -Image, or -Camera"
    }
    if ($ConnectionUri) {
        return $ConnectionUri
    }
    if ($Image -or $Camera) {
        return Read-PdaLinkQrWithPython
    }
    try {
        $clipboard = (Get-Clipboard -Raw -ErrorAction Stop).Trim()
        if ($clipboard.StartsWith("pdalink://connect?", [StringComparison]::OrdinalIgnoreCase)) {
            return $clipboard
        }
    } catch {
    }
    return Read-Host "Paste the PdaLink connection code"
}

$bundle = ConvertFrom-PdaLinkConnectionUri -ConnectionUri (Read-PdaLinkConnectionInput)
Write-Host "transport: $($bundle.transport)"
Write-Host "network: $($bundle.ssid)"
Write-Host "security: $($bundle.security)"
Write-Host "proxy: $($bundle.proxy_url)"
if ($Action -eq "show") {
    exit 0
}

$securePassword = ConvertTo-SecureString -String ([string]$bundle.password) -AsPlainText -Force
if ($Action -eq "internet") {
    $internetScript = Join-Path $PSScriptRoot "windows_wifi_direct_internet.ps1"
    $internetArguments = @{
        Ssid = [string]$bundle.ssid
        Password = $securePassword
        Security = [string]$bundle.security
        Transport = [string]$bundle.transport
        ProxyAddress = [string]$bundle.proxy_address
        ProxyPort = [int]$bundle.proxy_port
        Interface = $Interface
    }
    & $internetScript start @internetArguments
    exit 0
}

$wifi = $null
try {
    $wifiArguments = @{
        Ssid = [string]$bundle.ssid
        Password = $securePassword
        Security = [string]$bundle.security
        Interface = $Interface
    }
    $wifi = Connect-PdaLinkWifiDirect @wifiArguments
    $endpointArguments = @{
        ProxyAddress = [string]$bundle.proxy_address
        ProxyPort = [int]$bundle.proxy_port
        Interface = $Interface
    }
    [void](Test-PdaLinkWifiDirectEndpoint @endpointArguments)
    Write-Host "PdaLink Wi-Fi and proxy endpoint are ready."
} catch {
    if ($null -ne $wifi) {
        try {
            Remove-PdaLinkWifiProfile -ProfileName ([string]$wifi.profile_name) -Interface $Interface
        } catch {
            Write-Warning "Could not remove the failed PdaLink WLAN profile"
        }
    }
    throw
}
