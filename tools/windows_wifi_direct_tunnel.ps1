param(
    [ValidateSet("setup", "start", "stop", "status", "test")]
    [string]$Action = "start",
    [string]$Ssid = "",
    [Security.SecureString]$Password,
    [string]$Interface = "",
    [string]$Url = "https://ifconfig.me",
    [string]$Python = "",
    [string]$RuntimeRoot = "",
    [string]$StatePath = "",
    [string]$WintunManifest = "",
    [string[]]$DnsServers = @("1.1.1.1", "8.8.8.8"),
    [int]$Mtu = 1500,
    [switch]$KeepWifi,
    [switch]$Force,
    [switch]$VerboseAdapter
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "windows_wifi_direct_common.ps1")

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$ProxyAddress = "192.168.49.1"
$ProxyPort = 8001
$ProxyUrl = "http://${ProxyAddress}:$ProxyPort"
$PacUrl = "$ProxyUrl/proxy.pac"
$TunnelAddress = "10.66.0.2"
$TunnelPrefixLength = 24
$SplitRoutes = @("0.0.0.0/1", "128.0.0.0/1")
$HostRoute = "$ProxyAddress/32"
$RouteMetric = 5
$StateOwner = "pdalink-windows-wifi-direct-tunnel"
$WintunVersion = "0.14.1"
$PinnedArchiveUrl = "https://www.wintun.net/builds/wintun-$WintunVersion.zip"
$PinnedArchiveSha256 = "07c256185d6ee3652e09fa55c0b673e2624b565e02c4b9091c79ca7d2f24ef51"
$AdapterScript = (Resolve-Path (Join-Path $Root "windows\pdalink_wintun.py")).Path

if (-not $env:ProgramData) {
    throw "ProgramData is not available"
}
$ExpectedRuntimeRoot = Join-Path $env:ProgramData "PdaLink\runtime"
if (-not $RuntimeRoot) {
    $RuntimeRoot = $ExpectedRuntimeRoot
}
if (-not [IO.Path]::GetFullPath($RuntimeRoot).Equals(
    [IO.Path]::GetFullPath($ExpectedRuntimeRoot),
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "runtime root must be the protected ProgramData PdaLink path"
}
if (-not $StatePath) {
    $StatePath = Join-Path $RuntimeRoot "windows-wifi-direct-tunnel.json"
}
if ($Mtu -lt 1280 -or $Mtu -gt 9000) {
    throw "MTU must be between 1280 and 9000"
}
if ($DnsServers.Count -lt 1) {
    throw "at least one IPv4 DNS server is required"
}
foreach ($server in $DnsServers) {
    $parsedAddress = [Net.IPAddress]::None
    if (-not [Net.IPAddress]::TryParse($server, [ref]$parsedAddress) -or
        $parsedAddress.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        throw "DNS server must be an IPv4 address: $server"
    }
}
if ($Force -and $Action -ne "stop") {
    throw "-Force is accepted only with stop"
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Windows Wi-Fi Direct tunnel actions require an elevated PowerShell window"
    }
}

function Set-PdaLinkAdminOnlyDirectoryAcl {
    param([Parameter(Mandatory = $true)][string]$Path)

    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    $propagation = [Security.AccessControl.PropagationFlags]::None
    $administrators = [Security.Principal.SecurityIdentifier]::new("S-1-5-32-544")
    $acl.SetOwner($administrators)
    foreach ($sid in @("S-1-5-18", "S-1-5-32-544")) {
        $identity = [Security.Principal.SecurityIdentifier]::new($sid)
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $identity,
            [Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            $propagation,
            [Security.AccessControl.AccessControlType]::Allow
        )
        [void]$acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Initialize-PdaLinkRuntimeRoot {
    $base = [IO.Path]::GetFullPath($env:ProgramData).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $fullLeaf = [IO.Path]::GetFullPath($RuntimeRoot)
    $prefix = $base + [IO.Path]::DirectorySeparatorChar
    if (-not $fullLeaf.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "protected runtime root must be below ProgramData"
    }
    $current = $base
    foreach ($segment in $fullLeaf.Substring($prefix.Length).Split([IO.Path]::DirectorySeparatorChar)) {
        $current = Join-Path $current $segment
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (-not $item.PSIsContainer -or
                (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
                throw "refusing unsafe or reparse directory in runtime path: $current"
            }
            $ownerSid = (Get-Acl -LiteralPath $current).GetOwner(
                [Security.Principal.SecurityIdentifier]
            ).Value
            $allowedOwners = @(
                "S-1-5-18",
                "S-1-5-32-544",
                [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
            )
            if ($ownerSid -notin $allowedOwners) {
                throw "refusing pre-existing runtime directory owned by an untrusted identity: $current"
            }
        } else {
            New-Item -ItemType Directory -Path $current | Out-Null
        }
        Set-PdaLinkAdminOnlyDirectoryAcl -Path $current
    }
}

function Assert-PdaLinkTrustedWintunSignature {
    param([Parameter(Mandatory = $true)][string]$DllPath)

    $signature = Get-AuthenticodeSignature -LiteralPath $DllPath
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
        $null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notmatch '(?i)WireGuard') {
        throw "wintun.dll does not have a valid WireGuard Authenticode signature"
    }
    return $signature.SignerCertificate.Thumbprint
}

function Get-WindowsArchitectureName {
    $architecture = $env:PROCESSOR_ARCHITEW6432
    if (-not $architecture) {
        $architecture = $env:PROCESSOR_ARCHITECTURE
    }
    switch ($architecture.ToUpperInvariant()) {
        "AMD64" { return "amd64" }
        "X86" { return "x86" }
        "ARM64" { return "arm64" }
        "ARM" { return "arm" }
        default { throw "unsupported Windows architecture: $architecture" }
    }
}

function Read-VerifiedWintunManifest {
    $architecture = Get-WindowsArchitectureName
    $expectedManifest = Join-Path $env:ProgramData "PdaLink\wintun\$WintunVersion\$architecture\manifest.json"
    if (-not $WintunManifest) {
        $script:WintunManifest = $expectedManifest
    }
    if (-not [IO.Path]::GetFullPath($WintunManifest).Equals(
        [IO.Path]::GetFullPath($expectedManifest),
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Wintun manifest must be the protected pinned ProgramData manifest"
    }
    if (-not (Test-Path -LiteralPath $WintunManifest)) {
        throw "verified Wintun manifest not found: $WintunManifest. Run this script with setup first."
    }
    $manifest = Get-Content -LiteralPath $WintunManifest -Raw | ConvertFrom-Json
    if ([int]$manifest.version -ne 1 -or [string]$manifest.owner -ne "pdalink-wintun-setup") {
        throw "invalid Wintun manifest owner or version"
    }
    if ([string]$manifest.wintun_version -ne $WintunVersion -or
        [string]$manifest.archive_url -ne $PinnedArchiveUrl -or
        [string]$manifest.archive_sha256 -ne $PinnedArchiveSha256) {
        throw "Wintun manifest does not match the pinned official signed archive"
    }
    if ([string]$manifest.architecture -ne (Get-WindowsArchitectureName)) {
        throw "Wintun manifest architecture does not match this Windows host"
    }
    $dllPath = Join-Path (Split-Path -Parent $WintunManifest) "wintun.dll"
    foreach ($path in @($WintunManifest, $dllPath)) {
        $item = Get-Item -LiteralPath $path -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "refusing reparse point in verified Wintun installation: $path"
        }
    }
    $dllHash = (Get-FileHash -LiteralPath $dllPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($dllHash -ne [string]$manifest.dll_sha256) {
        throw "installed wintun.dll hash does not match its verified manifest"
    }
    $signerThumbprint = Assert-PdaLinkTrustedWintunSignature -DllPath $dllPath
    if ([string]$manifest.signer_thumbprint -ne $signerThumbprint) {
        throw "installed wintun.dll signer does not match its verified manifest"
    }
    return [pscustomobject]@{
        dll_path = (Resolve-Path $dllPath).Path
        dll_sha256 = $dllHash
        manifest_path = (Resolve-Path $WintunManifest).Path
    }
}

function Get-PdaLinkPythonCommand {
    $candidates = @()
    if ($Python) {
        $candidates += [pscustomobject]@{ executable = $Python; prefix = @() }
    } else {
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($null -ne $pythonCommand) {
            $candidates += [pscustomobject]@{ executable = $pythonCommand.Source; prefix = @() }
        }
        $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($null -ne $pyCommand) {
            $candidates += [pscustomobject]@{ executable = $pyCommand.Source; prefix = @("-3") }
        }
    }
    foreach ($candidate in $candidates) {
        $versionOutput = @(& $candidate.executable @($candidate.prefix) --version 2>&1)
        if ($LASTEXITCODE -ne 0) {
            continue
        }
        $versionText = $versionOutput -join " "
        if ($versionText -match 'Python\s+(\d+)\.(\d+)') {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {
                $architectureOutput = @(& $candidate.executable @($candidate.prefix) -c `
                    "import platform,struct; print(f'{struct.calcsize(`"P`") * 8}:{platform.machine().lower()}')" 2>&1)
                if ($LASTEXITCODE -ne 0) {
                    continue
                }
                $architectureProbe = ($architectureOutput -join " ").Trim()
                $nativeArchitecture = Get-WindowsArchitectureName
                $architectureMatches = switch ($nativeArchitecture) {
                    "amd64" { $architectureProbe -match '^64:(amd64|x86_64)$' }
                    "x86" { $architectureProbe -match '^32:(x86|i[3-6]86)$' }
                    "arm64" { $architectureProbe -match '^64:(arm64|aarch64)$' }
                    "arm" { $architectureProbe -match '^32:(arm|armv7l)$' }
                    default { $false }
                }
                if ($architectureMatches) {
                    $candidate | Add-Member -NotePropertyName architecture -NotePropertyValue $nativeArchitecture
                    return $candidate
                }
            }
        }
    }
    throw "a native-architecture Python 3.10 or newer was not found; install it or pass -Python"
}

function ConvertTo-PdaLinkProcessArgument {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)

    if ($Value -match '[\r\n\"]') {
        throw "process argument contains unsupported quote or newline"
    }
    if ($Value -notmatch '\s') {
        return $Value
    }
    if ($Value.EndsWith("\")) {
        throw "quoted process argument may not end in a backslash"
    }
    return '"' + $Value + '"'
}

function Start-PdaLinkWintunProcess {
    param(
        [Parameter(Mandatory = $true)][object]$PythonCommand,
        [Parameter(Mandatory = $true)][object]$Wintun,
        [Parameter(Mandatory = $true)][int]$UpstreamInterfaceIndex,
        [Parameter(Mandatory = $true)][string]$AdapterName,
        [Parameter(Mandatory = $true)][string]$Token,
        [Parameter(Mandatory = $true)][string]$ReadyFile,
        [Parameter(Mandatory = $true)][string]$ActivationFile,
        [Parameter(Mandatory = $true)][string]$StopFile,
        [Parameter(Mandatory = $true)][string]$StdoutLog,
        [Parameter(Mandatory = $true)][string]$StderrLog
    )

    $arguments = @($PythonCommand.prefix) + @(
        $AdapterScript,
        "--dll", [string]$Wintun.dll_path,
        "--dll-sha256", [string]$Wintun.dll_sha256,
        "--adapter-name", $AdapterName,
        "--proxy-url", $ProxyUrl,
        "--upstream-interface-index", [string]$UpstreamInterfaceIndex,
        "--ready-file", $ReadyFile,
        "--activation-file", $ActivationFile,
        "--stop-file", $StopFile,
        "--token", $Token,
        "--mtu", [string]$Mtu,
        "--activation-timeout", "30"
    )
    if ($VerboseAdapter) {
        $arguments += "--verbose"
    }
    $argumentLine = (@($arguments | ForEach-Object { ConvertTo-PdaLinkProcessArgument -Value ([string]$_) })) -join " "
    return Start-Process -FilePath $PythonCommand.executable -ArgumentList $argumentLine `
        -WorkingDirectory $Root -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog `
        -WindowStyle Hidden -PassThru
}

function Wait-PdaLinkWintunReady {
    param(
        [Parameter(Mandatory = $true)][Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$ReadyFile,
        [Parameter(Mandatory = $true)][string]$Token,
        [Parameter(Mandatory = $true)][string]$AdapterName,
        [Parameter(Mandatory = $true)][string]$StderrLog
    )

    $deadline = [DateTime]::UtcNow.AddSeconds(25)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $ReadyFile) {
            try {
                $ready = Get-Content -LiteralPath $ReadyFile -Raw | ConvertFrom-Json
            } catch {
                if (-not $Process.HasExited) {
                    Start-Sleep -Milliseconds 100
                    continue
                }
                throw
            }
            if ([int]$ready.version -ne 1 -or [string]$ready.owner -ne "pdalink-wintun" -or
                [int]$ready.pid -ne $Process.Id -or [string]$ready.token -ne $Token -or
                [string]$ready.adapter_name -ne $AdapterName) {
                throw "Wintun readiness file failed ownership validation"
            }
            return $ready
        }
        if ($Process.HasExited) {
            $detail = if (Test-Path -LiteralPath $StderrLog) { Get-Content -LiteralPath $StderrLog -Raw } else { "" }
            throw "Wintun adapter exited before readiness: $detail"
        }
        Start-Sleep -Milliseconds 200
    }
    throw "Wintun adapter did not become ready before timeout"
}

function Wait-PdaLinkNetAdapter {
    param([Parameter(Mandatory = $true)][string]$AdapterName)

    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while ([DateTime]::UtcNow -lt $deadline) {
        $adapter = Get-NetAdapter -Name $AdapterName -ErrorAction SilentlyContinue
        if ($null -ne $adapter) {
            return $adapter
        }
        Start-Sleep -Milliseconds 200
    }
    throw "Windows did not publish Wintun adapter $AdapterName"
}

function Test-PdaLinkRouteConflicts {
    foreach ($prefix in $SplitRoutes) {
        $existing = @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix $prefix -PolicyStore ActiveStore -ErrorAction SilentlyContinue)
        if ($existing.Count -gt 0) {
            throw "refusing to replace existing split route ${prefix}: $($existing | Out-String)"
        }
    }
}

function Get-PdaLinkHostRouteDisposition {
    param([Parameter(Mandatory = $true)][object]$WifiRoute)

    $existing = @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix $HostRoute -PolicyStore ActiveStore -ErrorAction SilentlyContinue)
    if ($existing.Count -eq 0) {
        return [pscustomobject]@{ create = $true; route = $null }
    }
    $matching = @($existing | Where-Object {
        [int]$_.InterfaceIndex -eq [int]$WifiRoute.interface_index -and
        [string]$_.NextHop -eq [string]$WifiRoute.next_hop
    })
    if ($matching.Count -ne 1 -or $existing.Count -ne 1) {
        throw "existing $HostRoute route is not the verified Wi-Fi Direct path; refusing mutation"
    }
    return [pscustomobject]@{ create = $false; route = $matching[0] }
}

function Write-PdaLinkTunnelStateExclusive {
    param([Parameter(Mandatory = $true)][object]$State)

    $json = $State | ConvertTo-Json -Depth 10
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json)
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

function Update-PdaLinkTunnelState {
    param([Parameter(Mandatory = $true)][object]$State)

    $temp = "$StatePath.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText($temp, ($State | ConvertTo-Json -Depth 10), [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temp -Destination $StatePath -Force
    } finally {
        Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
    }
}

function Assert-PdaLinkRuntimePath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $rootPath = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $candidate = [IO.Path]::GetFullPath($Path)
    if (-not $candidate.StartsWith($rootPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "invalid tunnel state path outside protected runtime root: $candidate"
    }
}

function Assert-PdaLinkTunnelState {
    param([Parameter(Mandatory = $true)][object]$State)

    if ([int]$State.version -ne 1 -or [string]$State.owner -ne $StateOwner) {
        throw "invalid PdaLink tunnel state owner or version"
    }
    if ([string]$State.phase -notin @("starting", "active")) {
        throw "invalid PdaLink tunnel phase"
    }
    if ([string]$State.token -notmatch '^[0-9a-f]{32}$' -or
        [string]$State.adapter_name -notmatch '^PdaLink-[0-9a-f]{12}$') {
        throw "invalid PdaLink tunnel identity"
    }
    if ([int]$State.process_id -le 0 -or [long]$State.process_start_ticks -le 0 -or
        [int]$State.tunnel_interface_index -le 0 -or [int]$State.wifi_interface_index -le 0) {
        throw "invalid PdaLink process or interface identity"
    }
    if ([string]$State.proxy_url -ne $ProxyUrl -or [string]$State.host_route_prefix -ne $HostRoute) {
        throw "invalid PdaLink tunnel endpoint"
    }
    if ([string]$State.tunnel_address -ne $TunnelAddress -or [int]$State.tunnel_prefix_length -ne $TunnelPrefixLength) {
        throw "invalid PdaLink tunnel address"
    }
    if (@($State.split_routes).Count -ne 2 -or
        [string]$State.split_routes[0] -ne $SplitRoutes[0] -or
        [string]$State.split_routes[1] -ne $SplitRoutes[1]) {
        throw "invalid PdaLink split-route state"
    }
    foreach ($path in @($State.ready_file, $State.activation_file, $State.stop_file, $State.stdout_log, $State.stderr_log)) {
        Assert-PdaLinkRuntimePath -Path ([string]$path)
    }
    $savedAdapterScript = [IO.Path]::GetFullPath([string]$State.adapter_script)
    if (-not $savedAdapterScript.Equals([IO.Path]::GetFullPath($AdapterScript), [StringComparison]::OrdinalIgnoreCase)) {
        throw "tunnel state references an unexpected adapter script"
    }
}

function Read-PdaLinkTunnelState {
    if (-not (Test-Path -LiteralPath $StatePath)) {
        throw "PdaLink tunnel state not found: $StatePath"
    }
    try {
        $utf8 = [Text.UTF8Encoding]::new($false, $true)
        $state = [IO.File]::ReadAllText($StatePath, $utf8) | ConvertFrom-Json
    } catch {
        throw "invalid PdaLink tunnel state ${StatePath}: $($_.Exception.Message)"
    }
    Assert-PdaLinkTunnelState -State $state
    return $state
}

function Get-ExactPdaLinkRoute {
    param(
        [Parameter(Mandatory = $true)][string]$Prefix,
        [Parameter(Mandatory = $true)][int]$InterfaceIndex,
        [Parameter(Mandatory = $true)][string]$NextHop,
        [int]$ExpectedMetric = -1
    )

    return @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix $Prefix -InterfaceIndex $InterfaceIndex `
        -PolicyStore ActiveStore -ErrorAction SilentlyContinue | Where-Object {
            [string]$_.NextHop -eq $NextHop -and ($ExpectedMetric -lt 0 -or [int]$_.RouteMetric -eq $ExpectedMetric)
        })
}

function Add-PdaLinkTunnelRoutes {
    param(
        [Parameter(Mandatory = $true)][object]$State,
        [Parameter(Mandatory = $true)][bool]$CreateHostRoute
    )

    if ($CreateHostRoute) {
        New-NetRoute -AddressFamily IPv4 -DestinationPrefix $HostRoute `
            -InterfaceIndex ([int]$State.wifi_interface_index) -NextHop ([string]$State.wifi_next_hop) `
            -RouteMetric 1 -PolicyStore ActiveStore | Out-Null
    }
    $afterHostRoute = Get-PdaLinkBestRoute -RemoteAddress $ProxyAddress
    if ([int]$afterHostRoute.interface_index -ne [int]$State.wifi_interface_index) {
        throw "proxy host route no longer selects the verified Wi-Fi Direct interface"
    }

    New-NetIPAddress -AddressFamily IPv4 -InterfaceIndex ([int]$State.tunnel_interface_index) `
        -IPAddress $TunnelAddress -PrefixLength $TunnelPrefixLength -PolicyStore ActiveStore | Out-Null
    Set-NetIPInterface -AddressFamily IPv4 -InterfaceIndex ([int]$State.tunnel_interface_index) `
        -NlMtuBytes $Mtu -AutomaticMetric Disabled -InterfaceMetric $RouteMetric
    Set-DnsClientServerAddress -InterfaceIndex ([int]$State.tunnel_interface_index) -ServerAddresses $DnsServers

    foreach ($prefix in $SplitRoutes) {
        New-NetRoute -AddressFamily IPv4 -DestinationPrefix $prefix `
            -InterfaceIndex ([int]$State.tunnel_interface_index) -NextHop "0.0.0.0" `
            -RouteMetric $RouteMetric -PolicyStore ActiveStore | Out-Null
    }
    $proxyRoute = Get-PdaLinkBestRoute -RemoteAddress $ProxyAddress
    if ([int]$proxyRoute.interface_index -ne [int]$State.wifi_interface_index) {
        throw "split routes captured the Android proxy connection; refusing activation"
    }
    $internetRoute = Get-PdaLinkBestRoute -RemoteAddress $DnsServers[0]
    if ([int]$internetRoute.interface_index -ne [int]$State.tunnel_interface_index) {
        throw "split routes did not select the PdaLink Wintun adapter"
    }
}

function Test-PdaLinkTransparentInternet {
    param([Parameter(Mandatory = $true)][int]$ExpectedTunnelInterfaceIndex)

    Write-Host "Testing IPv4 TCP without a process or system proxy..."
    & curl.exe --silent --show-error --fail --max-time 30 --noproxy "*" -4 $Url | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "transparent IPv4 TCP test failed for $Url"
    }
    Write-Host "Testing Windows DNS through UDP/53 conversion..."
    $dnsRoute = Get-PdaLinkBestRoute -RemoteAddress $DnsServers[0]
    if ([int]$dnsRoute.interface_index -ne $ExpectedTunnelInterfaceIndex) {
        throw "configured DNS server route does not select the PdaLink Wintun adapter"
    }
    Resolve-DnsName -Name "example.com" -Type A -Server $DnsServers[0] -DnsOnly -NoHostsFile `
        -ErrorAction Stop | Select-Object -First 3 | Out-Host
}

function Test-PdaLinkProcessIdentity {
    param([Parameter(Mandatory = $true)][object]$State)

    $process = Get-Process -Id ([int]$State.process_id) -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $null
    }
    try {
        $startTicks = $process.StartTime.ToUniversalTime().Ticks
    } catch {
        return $null
    }
    if ($startTicks -ne [long]$State.process_start_ticks) {
        return $null
    }
    try {
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$State.process_id)" -ErrorAction Stop
    } catch {
        return $null
    }
    if ($null -eq $cim -or [string]$cim.CommandLine -notlike "*$($State.token)*" -or
        [string]$cim.CommandLine -notlike "*pdalink_wintun.py*") {
        return $null
    }
    return $process
}

function Remove-PdaLinkTunnelOwnedState {
    param([Parameter(Mandatory = $true)][object]$State)

    $errors = [Collections.Generic.List[string]]::new()
    $ownedAdapters = @(Get-NetAdapter -Name ([string]$State.adapter_name) -ErrorAction SilentlyContinue)
    $adapterIdentityMatches = $ownedAdapters.Count -eq 1 -and
        [int]$ownedAdapters[0].ifIndex -eq [int]$State.tunnel_interface_index
    if ($ownedAdapters.Count -gt 0 -and -not $adapterIdentityMatches) {
        $errors.Add("saved Wintun adapter name no longer matches its interface index; refusing route deletion")
    }

    foreach ($prefix in @($State.split_routes)) {
        try {
            $routes = Get-ExactPdaLinkRoute -Prefix ([string]$prefix) `
                -InterfaceIndex ([int]$State.tunnel_interface_index) -NextHop "0.0.0.0"
            if ($routes.Count -gt 0 -and -not $adapterIdentityMatches) {
                $errors.Add("owned route $prefix remains on an interface index that may have been reused")
            } else {
                foreach ($route in $routes) {
                    Remove-NetRoute -InputObject $route -Confirm:$false
                }
                $remaining = Get-ExactPdaLinkRoute -Prefix ([string]$prefix) `
                    -InterfaceIndex ([int]$State.tunnel_interface_index) -NextHop "0.0.0.0"
                if ($remaining.Count -gt 0) {
                    throw "owned split route still exists after removal"
                }
            }
        } catch {
            $errors.Add("remove split route ${prefix}: $($_.Exception.Message)")
        }
    }

    try {
        if ($adapterIdentityMatches) {
            Set-DnsClientServerAddress -InterfaceIndex ([int]$State.tunnel_interface_index) -ResetServerAddresses
            $address = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex ([int]$State.tunnel_interface_index) `
                -IPAddress $TunnelAddress -ErrorAction SilentlyContinue
            if ($null -ne $address) {
                Remove-NetIPAddress -InputObject $address -Confirm:$false
            }
        }
    } catch {
        $errors.Add("reset owned Wintun address/DNS: $($_.Exception.Message)")
    }

    if ([bool]$State.host_route_created) {
        try {
            $routes = Get-ExactPdaLinkRoute -Prefix $HostRoute -InterfaceIndex ([int]$State.wifi_interface_index) `
                -NextHop ([string]$State.wifi_next_hop)
            foreach ($route in $routes) {
                Remove-NetRoute -InputObject $route -Confirm:$false
            }
            $remaining = Get-ExactPdaLinkRoute -Prefix $HostRoute -InterfaceIndex ([int]$State.wifi_interface_index) `
                -NextHop ([string]$State.wifi_next_hop)
            if ($remaining.Count -gt 0) {
                throw "created proxy host route still exists after removal"
            }
        } catch {
            $errors.Add("remove Wi-Fi Direct proxy host route: $($_.Exception.Message)")
        }
    }

    if ($errors.Count -gt 0) {
        throw "PdaLink tunnel cleanup incomplete; state retained at ${StatePath}: $($errors -join '; ')"
    }

    try {
        [IO.File]::WriteAllText([string]$State.stop_file, "stop", [Text.UTF8Encoding]::new($false))
        $process = Test-PdaLinkProcessIdentity -State $State
        if ($null -ne $process) {
            $process | Wait-Process -Timeout 6 -ErrorAction SilentlyContinue
            $process.Refresh()
            if (-not $process.HasExited) {
                $process = Test-PdaLinkProcessIdentity -State $State
                if ($null -ne $process) {
                    Stop-Process -InputObject $process -Force
                    $process | Wait-Process -Timeout 5 -ErrorAction SilentlyContinue
                }
            }
        }
        $adapterDeadline = [DateTime]::UtcNow.AddSeconds(5)
        while ([DateTime]::UtcNow -lt $adapterDeadline -and
            $null -ne (Get-NetAdapter -Name ([string]$State.adapter_name) -ErrorAction SilentlyContinue)) {
            Start-Sleep -Milliseconds 200
        }
        if ($null -ne (Get-NetAdapter -Name ([string]$State.adapter_name) -ErrorAction SilentlyContinue)) {
            throw "owned Wintun adapter still exists after process shutdown"
        }
    } catch {
        $errors.Add("stop owned Wintun process: $($_.Exception.Message)")
    }

    if ($errors.Count -gt 0) {
        throw "PdaLink tunnel cleanup incomplete; state retained at ${StatePath}: $($errors -join '; ')"
    }

    if (-not $KeepWifi) {
        try {
            Remove-PdaLinkWifiProfile -ProfileName ([string]$State.profile_name) `
                -Interface ([string]$State.requested_interface)
        } catch {
            if ($Force) {
                Write-Warning "Ignoring WLAN profile cleanup failure during forced recovery: $($_.Exception.Message)"
            } else {
                $errors.Add("remove PdaLink WLAN profile: $($_.Exception.Message)")
            }
        }
    }

    if ($errors.Count -gt 0) {
        throw "PdaLink tunnel cleanup incomplete; state retained at ${StatePath}: $($errors -join '; ')"
    }

    foreach ($path in @($State.ready_file, $State.activation_file, $State.stop_file, $State.stdout_log, $State.stderr_log)) {
        Remove-Item -LiteralPath ([string]$path) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $StatePath -Force
}

function Start-PdaLinkWifiDirectTunnel {
    Initialize-PdaLinkRuntimeRoot
    if (Test-Path -LiteralPath $StatePath) {
        throw "PdaLink tunnel state already exists: $StatePath. Run stop first."
    }
    if ($env:LOCALAPPDATA) {
        $phase1State = Join-Path $env:LOCALAPPDATA "PdaLink\runtime\windows-wifi-direct-internet.json"
        if (Test-Path -LiteralPath $phase1State) {
            throw "phase-1 WinINET PAC state is active; stop windows_wifi_direct_internet.ps1 before starting the tunnel"
        }
    }
    if (-not $Ssid) {
        throw "start requires -Ssid from the Android app"
    }
    if ($null -eq $Password) {
        $script:Password = Read-Host "Wi-Fi Direct password shown by Android" -AsSecureString
    }

    $wifi = $null
    $process = $null
    $state = $null
    $stateWritten = $false
    try {
        Write-PdaLinkStep "Connecting Windows to Android Wi-Fi Direct"
        $wifi = Connect-PdaLinkWifiDirect -Ssid $Ssid -Password $Password -Interface $Interface

        Write-PdaLinkStep "Verifying Wi-Fi Direct route, PAC, and explicit proxy"
        $endpoint = Test-PdaLinkWifiDirectEndpoint -Interface $Interface
        # Windows PowerShell drops empty native arguments; override NO_PROXY with a nonmatching name.
        & curl.exe --silent --show-error --fail --max-time 30 --proxy $ProxyUrl --noproxy "pdalink-no-bypass.invalid" $Url | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "explicit Android proxy test failed before route mutation"
        }
        Test-PdaLinkRouteConflicts
        $hostDisposition = Get-PdaLinkHostRouteDisposition -WifiRoute $endpoint.route
        $wintun = Read-VerifiedWintunManifest
        $pythonCommand = Get-PdaLinkPythonCommand

        $token = [Guid]::NewGuid().ToString("N")
        $adapterName = "PdaLink-$($token.Substring(0, 12))"
        $readyFile = Join-Path $RuntimeRoot "wintun-$token.ready.json"
        $activationFile = Join-Path $RuntimeRoot "wintun-$token.activate"
        $stopFile = Join-Path $RuntimeRoot "wintun-$token.stop"
        $stdoutLog = Join-Path $RuntimeRoot "wintun-$token.stdout.log"
        $stderrLog = Join-Path $RuntimeRoot "wintun-$token.stderr.log"

        Write-PdaLinkStep "Starting verified Wintun packet adapter"
        $process = Start-PdaLinkWintunProcess -PythonCommand $pythonCommand -Wintun $wintun `
            -UpstreamInterfaceIndex ([int]$endpoint.route.interface_index) `
            -AdapterName $adapterName -Token $token -ReadyFile $readyFile -ActivationFile $activationFile `
            -StopFile $stopFile -StdoutLog $stdoutLog -StderrLog $stderrLog
        [void](Wait-PdaLinkWintunReady -Process $process -ReadyFile $readyFile -Token $token `
            -AdapterName $adapterName -StderrLog $stderrLog)
        $netAdapter = Wait-PdaLinkNetAdapter -AdapterName $adapterName

        $state = [pscustomobject]@{
            version = 1
            owner = $StateOwner
            phase = "starting"
            created_at = [DateTime]::UtcNow.ToString("o")
            token = $token
            process_id = $process.Id
            process_start_ticks = $process.StartTime.ToUniversalTime().Ticks
            adapter_name = $adapterName
            adapter_script = $AdapterScript
            tunnel_interface_index = [int]$netAdapter.ifIndex
            tunnel_address = $TunnelAddress
            tunnel_prefix_length = $TunnelPrefixLength
            split_routes = $SplitRoutes
            route_metric = $RouteMetric
            dns_servers = $DnsServers
            proxy_url = $ProxyUrl
            pac_url = $PacUrl
            wifi_interface_index = [int]$endpoint.route.interface_index
            wifi_interface_alias = [string]$endpoint.route.interface_alias
            wifi_source_address = [string]$endpoint.route.source_address
            wifi_next_hop = [string]$endpoint.route.next_hop
            host_route_prefix = $HostRoute
            host_route_created = [bool]$hostDisposition.create
            ssid = $Ssid
            profile_name = [string]$wifi.profile_name
            requested_interface = $Interface
            wintun_manifest = [string]$wintun.manifest_path
            wintun_dll = [string]$wintun.dll_path
            wintun_dll_sha256 = [string]$wintun.dll_sha256
            ready_file = $readyFile
            activation_file = $activationFile
            stop_file = $stopFile
            stdout_log = $stdoutLog
            stderr_log = $stderrLog
        }
        Write-PdaLinkTunnelStateExclusive -State $state
        $stateWritten = $true

        Write-PdaLinkStep "Applying owned host, split-default, and DNS state"
        Add-PdaLinkTunnelRoutes -State $state -CreateHostRoute ([bool]$hostDisposition.create)
        [IO.File]::WriteAllText($activationFile, "active", [Text.UTF8Encoding]::new($false))
        $state.phase = "active"
        Update-PdaLinkTunnelState -State $state

        Write-PdaLinkStep "Testing transparent Windows IPv4 internet"
        Test-PdaLinkTransparentInternet -ExpectedTunnelInterfaceIndex ([int]$state.tunnel_interface_index)
        Write-Host ""
        Write-Host "PdaLink experimental Windows IPv4 tunnel is active."
        Write-Host "Supported: IPv4 TCP and UDP/53 DNS. Unsupported: IPv6, ICMP, arbitrary UDP/QUIC."
        Write-Host "Stop with: powershell -ExecutionPolicy Bypass -File .\tools\windows_wifi_direct_tunnel.ps1 stop"
    } catch {
        Write-PdaLinkStep "Rolling back failed Windows tunnel start"
        if ($stateWritten -and $null -ne $state) {
            try {
                Remove-PdaLinkTunnelOwnedState -State $state
            } catch {
                Write-Warning $_.Exception.Message
            }
        } else {
            if ($null -ne $process) {
                try { Stop-Process -InputObject $process -Force -ErrorAction SilentlyContinue } catch { Write-Warning $_ }
            }
            if ($null -ne $wifi) {
                try {
                    Remove-PdaLinkWifiProfile -ProfileName ([string]$wifi.profile_name) -Interface $Interface
                } catch {
                    Write-Warning "Could not remove failed PdaLink WLAN profile: $($_.Exception.Message)"
                }
            }
        }
        throw
    }
}

function Get-PdaLinkTunnelLifecycleStatus {
    if (-not (Test-Path -LiteralPath $StatePath)) {
        return [pscustomobject]@{ state = "inactive"; detail = "no tunnel state"; payload = $null }
    }
    try {
        $state = Read-PdaLinkTunnelState
    } catch {
        return [pscustomobject]@{ state = "invalid"; detail = $_.Exception.Message; payload = $null }
    }
    try {
        $process = Test-PdaLinkProcessIdentity -State $state
        if ($null -eq $process) {
            return [pscustomobject]@{ state = "stale"; detail = "owned Wintun process is not running"; payload = $state }
        }
        $proxyRoute = Get-PdaLinkBestRoute -RemoteAddress $ProxyAddress
        if ([int]$proxyRoute.interface_index -ne [int]$state.wifi_interface_index) {
            return [pscustomobject]@{ state = "drifted"; detail = "Android proxy route left the verified Wi-Fi interface"; payload = $state }
        }
        foreach ($prefix in @($state.split_routes)) {
            $route = @(Get-ExactPdaLinkRoute -Prefix ([string]$prefix) `
                -InterfaceIndex ([int]$state.tunnel_interface_index) -NextHop "0.0.0.0" -ExpectedMetric $RouteMetric
            )
            if ($route.Count -ne 1) {
                return [pscustomobject]@{ state = "drifted"; detail = "owned route $prefix is missing or changed"; payload = $state }
            }
        }
        return [pscustomobject]@{ state = "active"; detail = "process and exact routes verified"; payload = $state }
    } catch {
        return [pscustomobject]@{ state = "unreachable"; detail = $_.Exception.Message; payload = $state }
    }
}

function Show-PdaLinkWifiDirectTunnelStatus {
    $status = Get-PdaLinkTunnelLifecycleStatus
    Write-Host "PdaLink experimental Windows Wi-Fi Direct IPv4 tunnel"
    Write-Host "state: $($status.state)"
    Write-Host "detail: $($status.detail)"
    Write-Host "state path: $StatePath"
    if ($null -ne $status.payload) {
        Write-Host "SSID: $($status.payload.ssid)"
        Write-Host "Wi-Fi interface: $($status.payload.wifi_interface_alias) ($($status.payload.wifi_interface_index))"
        Write-Host "Wintun: $($status.payload.adapter_name) ($($status.payload.tunnel_interface_index))"
        Write-Host "PID: $($status.payload.process_id)"
        Write-Host "routes: $($status.payload.split_routes -join ', ')"
        Write-Host "DNS: $($status.payload.dns_servers -join ', ')"
        Write-Host "adapter log: $($status.payload.stderr_log)"
    }
    Write-Host "limits: IPv4 TCP + UDP/53 only; no IPv6, ICMP, or arbitrary UDP/QUIC"
}

function Test-PdaLinkWifiDirectTunnel {
    $status = Get-PdaLinkTunnelLifecycleStatus
    if ($status.state -ne "active") {
        throw "PdaLink tunnel is not active: $($status.state) - $($status.detail)"
    }
    Test-PdaLinkTransparentInternet -ExpectedTunnelInterfaceIndex ([int]$status.payload.tunnel_interface_index)
}

function Stop-PdaLinkWifiDirectTunnel {
    $state = Read-PdaLinkTunnelState
    Write-PdaLinkStep "Removing only PdaLink-owned Windows tunnel state"
    Remove-PdaLinkTunnelOwnedState -State $state
    Write-Host "PdaLink experimental Windows IPv4 tunnel is stopped."
}

Assert-Administrator
Assert-PdaLinkRuntimePath -Path $StatePath
switch ($Action) {
    "setup" {
        & (Join-Path $PSScriptRoot "windows_wintun_setup.ps1") -Action install
    }
    "start" { Start-PdaLinkWifiDirectTunnel }
    "stop" { Stop-PdaLinkWifiDirectTunnel }
    "status" { Show-PdaLinkWifiDirectTunnelStatus }
    "test" { Test-PdaLinkWifiDirectTunnel }
}
