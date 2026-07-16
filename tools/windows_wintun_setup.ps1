param(
    [ValidateSet("install", "status")]
    [string]$Action = "install",
    [string]$InstallRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$WintunVersion = "0.14.1"
$ArchiveUrl = "https://www.wintun.net/builds/wintun-$WintunVersion.zip"
$ArchiveSha256 = "07c256185d6ee3652e09fa55c0b673e2624b565e02c4b9091c79ca7d2f24ef51"

if (-not $env:ProgramData) {
    throw "ProgramData is not available"
}
$ExpectedInstallRoot = Join-Path $env:ProgramData "PdaLink\wintun\$WintunVersion"
if (-not $InstallRoot) {
    $InstallRoot = $ExpectedInstallRoot
}
if (-not [IO.Path]::GetFullPath($InstallRoot).Equals(
    [IO.Path]::GetFullPath($ExpectedInstallRoot),
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "Wintun install root must be the protected ProgramData PdaLink path"
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Wintun setup must run from an elevated PowerShell window"
    }
}

function Get-WintunArchitecture {
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

function Set-PdaLinkAdminOnlyDirectoryAcl {
    param([Parameter(Mandatory = $true)][string]$Path)

    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    $propagation = [Security.AccessControl.PropagationFlags]::None
    $system = [Security.Principal.SecurityIdentifier]::new("S-1-5-18")
    $administrators = [Security.Principal.SecurityIdentifier]::new("S-1-5-32-544")
    $acl.SetOwner($administrators)
    foreach ($identity in @($system, $administrators)) {
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

function Set-PdaLinkAdminOnlyFileAcl {
    param([Parameter(Mandatory = $true)][string]$Path)

    $acl = [Security.AccessControl.FileSecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $system = [Security.Principal.SecurityIdentifier]::new("S-1-5-18")
    $administrators = [Security.Principal.SecurityIdentifier]::new("S-1-5-32-544")
    $acl.SetOwner($administrators)
    foreach ($identity in @($system, $administrators)) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $identity,
            [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow
        )
        [void]$acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Initialize-PdaLinkSecureDirectoryTree {
    param([Parameter(Mandatory = $true)][string]$Leaf)

    $base = [IO.Path]::GetFullPath($env:ProgramData).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $fullLeaf = [IO.Path]::GetFullPath($Leaf)
    $prefix = $base + [IO.Path]::DirectorySeparatorChar
    if (-not $fullLeaf.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "secure PdaLink directory must be below ProgramData"
    }
    $current = $base
    foreach ($segment in $fullLeaf.Substring($prefix.Length).Split([IO.Path]::DirectorySeparatorChar)) {
        $current = Join-Path $current $segment
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (-not $item.PSIsContainer -or
                (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
                throw "refusing unsafe or reparse directory in Wintun install path: $current"
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
                throw "refusing pre-existing Wintun directory owned by an untrusted identity: $current"
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

function Get-WintunManifestPath {
    $architecture = Get-WintunArchitecture
    return Join-Path (Join-Path $InstallRoot $architecture) "manifest.json"
}

function Read-VerifiedWintunManifest {
    $manifestPath = Get-WintunManifestPath
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        throw "Wintun manifest not found: $manifestPath"
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ([int]$manifest.version -ne 1 -or [string]$manifest.owner -ne "pdalink-wintun-setup" -or
        [string]$manifest.wintun_version -ne $WintunVersion) {
        throw "unsupported Wintun manifest version"
    }
    if ([string]$manifest.archive_url -ne $ArchiveUrl -or [string]$manifest.archive_sha256 -ne $ArchiveSha256) {
        throw "Wintun manifest does not match the pinned official archive"
    }
    if ([string]$manifest.architecture -ne (Get-WintunArchitecture)) {
        throw "Wintun manifest architecture does not match this Windows host"
    }
    $dllPath = Join-Path (Split-Path -Parent $manifestPath) "wintun.dll"
    foreach ($path in @($manifestPath, $dllPath)) {
        $item = Get-Item -LiteralPath $path -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "refusing reparse point in verified Wintun installation: $path"
        }
    }
    $actual = (Get-FileHash -LiteralPath $dllPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne [string]$manifest.dll_sha256) {
        throw "installed wintun.dll hash does not match its verified manifest"
    }
    $signerThumbprint = Assert-PdaLinkTrustedWintunSignature -DllPath $dllPath
    if ([string]$manifest.signer_thumbprint -ne $signerThumbprint) {
        throw "installed wintun.dll signer does not match its verified manifest"
    }
    return [pscustomobject]@{
        manifest_path = $manifestPath
        dll_path = $dllPath
        dll_sha256 = $actual
        architecture = [string]$manifest.architecture
        signer_thumbprint = $signerThumbprint
    }
}

function Install-PinnedWintun {
    Assert-Administrator
    $architecture = Get-WintunArchitecture
    $token = [Guid]::NewGuid().ToString("N")
    $archivePath = Join-Path ([IO.Path]::GetTempPath()) "pdalink-wintun-$token.zip"
    $extractPath = Join-Path ([IO.Path]::GetTempPath()) "pdalink-wintun-$token"
    try {
        Write-Host "Downloading official signed Wintun $WintunVersion archive..."
        Invoke-WebRequest -UseBasicParsing -Uri $ArchiveUrl -OutFile $archivePath
        $actualArchiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualArchiveHash -ne $ArchiveSha256) {
            throw "Wintun archive SHA-256 mismatch: expected $ArchiveSha256, got $actualArchiveHash"
        }

        Expand-Archive -LiteralPath $archivePath -DestinationPath $extractPath
        $sourceDirectory = Join-Path $extractPath "wintun\bin\$architecture"
        $sourceDll = Join-Path $sourceDirectory "wintun.dll"
        if (-not (Test-Path -LiteralPath $sourceDll)) {
            throw "official Wintun archive does not contain $architecture\wintun.dll"
        }

        $targetDirectory = Join-Path $InstallRoot $architecture
        Initialize-PdaLinkSecureDirectoryTree -Leaf $targetDirectory
        $targetDll = Join-Path $targetDirectory "wintun.dll"
        foreach ($target in @($targetDll, (Join-Path $targetDirectory "manifest.json"))) {
            if (Test-Path -LiteralPath $target) {
                $targetItem = Get-Item -LiteralPath $target -Force
                if (($targetItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "refusing reparse point in Wintun install target: $target"
                }
                Remove-Item -LiteralPath $target -Force
            }
        }
        Copy-Item -LiteralPath $sourceDll -Destination $targetDll -Force
        Set-PdaLinkAdminOnlyFileAcl -Path $targetDll
        $dllHash = (Get-FileHash -LiteralPath $targetDll -Algorithm SHA256).Hash.ToLowerInvariant()
        $signerThumbprint = Assert-PdaLinkTrustedWintunSignature -DllPath $targetDll

        $license = Join-Path $extractPath "wintun\LICENSE.txt"
        if (Test-Path -LiteralPath $license) {
            Copy-Item -LiteralPath $license -Destination (Join-Path $targetDirectory "LICENSE.txt") -Force
        }
        $manifest = [pscustomobject]@{
            version = 1
            owner = "pdalink-wintun-setup"
            installed_at = [DateTime]::UtcNow.ToString("o")
            wintun_version = $WintunVersion
            architecture = $architecture
            archive_url = $ArchiveUrl
            archive_sha256 = $ArchiveSha256
            dll_sha256 = $dllHash
            signer_thumbprint = $signerThumbprint
        }
        $manifestPath = Join-Path $targetDirectory "manifest.json"
        $json = $manifest | ConvertTo-Json -Depth 4
        [IO.File]::WriteAllText($manifestPath, $json, [Text.UTF8Encoding]::new($false))
        Set-PdaLinkAdminOnlyFileAcl -Path $manifestPath
        [void](Read-VerifiedWintunManifest)
        Write-Host "Installed verified Wintun DLL: $targetDll"
        Write-Host "Manifest: $manifestPath"
    } finally {
        Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $extractPath -Recurse -Force -ErrorAction SilentlyContinue
    }
}

switch ($Action) {
    "install" { Install-PinnedWintun }
    "status" {
        $verified = Read-VerifiedWintunManifest
        Write-Host "Wintun: verified"
        Write-Host "architecture: $($verified.architecture)"
        Write-Host "DLL: $($verified.dll_path)"
        Write-Host "SHA-256: $($verified.dll_sha256)"
    }
}
