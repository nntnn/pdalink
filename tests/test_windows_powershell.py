import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")


@unittest.skipUnless(POWERSHELL, "PowerShell is not installed on this host")
class WindowsPowerShellParserTest(unittest.TestCase):
    def test_all_windows_helpers_parse(self):
        command = r"""
$failed = $false
Get-ChildItem -LiteralPath ./tools -Filter windows_*.ps1 | ForEach-Object {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $_.FullName,
        [ref]$tokens,
        [ref]$errors
    )
    if ($errors.Count -gt 0) {
        $failed = $true
        $errors | ForEach-Object { Write-Error "$($_.Extent.File): $($_.Message)" }
    }
}
if ($failed) { exit 1 }
"""
        result = subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-Command", command],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_route_collections_keep_empty_single_and_multiple_shapes(self):
        command = r"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $PWD 'tools/windows_wifi_direct_tunnel.ps1'), [ref]$tokens, [ref]$errors)
$definition = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Get-ExactPdaLinkRoute'
}, $true)
Invoke-Expression $definition.Extent.Text
function Get-NetRoute { $script:fakeRoutes }
foreach ($count in @(0,1,2)) {
    $script:fakeRoutes = @(for ($i = 0; $i -lt $count; $i++) {
        [pscustomobject]@{ NextHop = '0.0.0.0'; RouteMetric = 5 }
    })
    $routes = Get-ExactPdaLinkRoute -Prefix '0.0.0.0/1' -InterfaceIndex 7 -NextHop '0.0.0.0'
    if ($routes.Count -ne $count) { throw 'route collection shape changed' }
    foreach ($route in $routes) {
        if ($route.NextHop -ne '0.0.0.0') { throw 'nested route collection' }
    }
}
"""
        result = subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-Command", command], cwd=ROOT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_route_readiness_retries_transient_1232(self):
        command = r"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. ./tools/windows_wifi_direct_common.ps1
$script:attempts = 0
function Start-Sleep { }
function Find-NetRoute {
    $script:attempts++
    if ($script:attempts -lt 3) { throw 'Windows System Error 1232' }
    [pscustomobject]@{ IPAddress = '192.168.49.2'; InterfaceIndex = 7 }
    [pscustomobject]@{ DestinationPrefix = '192.168.49.0/24'; NextHop = '0.0.0.0'; InterfaceIndex = 7; InterfaceAlias = 'Wi-Fi' }
}
$route = Get-PdaLinkBestRoute -RemoteAddress '192.168.49.1'
if ($script:attempts -ne 3 -or $route.interface_index -ne 7) { throw 'retry failed' }
"""
        result = subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-Command", command], cwd=ROOT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_connection_uri_parser_accepts_local_hotspot(self):
        command = r"""
. ./tools/windows_wifi_direct_common.ps1
$uri = 'pdalink://connect?v=1&transport=local-hotspot&ssid=Pda+Link%2B1&security=wpa2-psk&password=pass%26word%3D123&proxy=http%3A%2F%2F10.111.107.245%3A8001'
$bundle = ConvertFrom-PdaLinkConnectionUri -ConnectionUri $uri
if ($bundle.ssid -ne 'Pda Link+1' -or $bundle.proxy_address -ne '10.111.107.245' -or $bundle.proxy_port -ne 8001) {
    throw 'connection URI parser returned unexpected values'
}
"""
        result = subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-Command", command],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_connection_uri_parser_rejects_public_proxy(self):
        command = r"""
. ./tools/windows_wifi_direct_common.ps1
$uri = 'pdalink://connect?v=1&transport=local-hotspot&ssid=PdaLink&security=wpa2-psk&password=password123&proxy=http%3A%2F%2F8.8.8.8%3A8001'
try {
    [void](ConvertFrom-PdaLinkConnectionUri -ConnectionUri $uri)
    exit 1
} catch {
    if ($_.Exception.Message -notmatch 'private IPv4') { throw }
}
"""
        result = subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-Command", command],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
