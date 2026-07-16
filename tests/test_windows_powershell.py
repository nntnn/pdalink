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


if __name__ == "__main__":
    unittest.main()
