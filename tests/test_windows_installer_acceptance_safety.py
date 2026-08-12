from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "scripts" / "verify_windows_install.ps1"
BOOTSTRAP = ROOT / "scripts" / "offline_installer_bootstrap.cs"


def test_verifier_is_version_aware_proxy_free_and_reparse_safe() -> None:
    source = VERIFIER.read_text(encoding="utf-8-sig")

    assert '[string]$ExpectedVersion = ""' in source
    assert "$ExpectedVersion = $registeredVersion" in source
    assert "$request.Proxy = $null" in source
    assert "$request.AllowAutoRedirect = $false" in source
    assert '$target.Host -ne "127.0.0.1"' in source
    assert "function Test-SafeDirectoryTree" in source
    assert "function Test-SafeLeaf" in source
    assert "Install directory has no links" in source
    assert ".FileVersion" in source
    assert ".ProductVersion" in source
    assert "function ConvertTo-ThreePartVersion" in source
    assert "$productCanonicalVersion -eq $expectedCanonicalVersion" in source
    assert "Test-PathInsideRoot" in source
    assert "_.ExecutablePath.StartsWith" not in source


def test_bootstrap_uses_no_clobber_extraction_and_non_recursive_cleanup() -> None:
    source = BOOTSTRAP.read_text(encoding="utf-8-sig")

    assert "FileMode.CreateNew" in source
    assert "FileShare.None" in source
    assert "target.Flush(true)" in source
    assert "EnsureSafeExistingPath(temporaryRoot)" in source
    assert "DeleteTemporaryRootSafely(temporaryRoot)" in source
    assert "Directory.Delete(temporaryRoot, true)" not in source
    assert "Task<string> outputTask" in source
    assert "Task<string> errorTask" in source
    assert '"安装失败：" + error.Message' not in source
    assert 'GetEnvironmentVariable("SystemRoot")' not in source
    assert "Environment.SpecialFolder.Windows" in source
    assert '@"Local\\VideoInsight-Installer"' in source
    assert "installerMutex.WaitOne(0, false)" in source
    assert "installerMutex.ReleaseMutex()" in source
    assert "InstallProgressForm" in source
    assert "while (!installer.WaitForExit(200))" in source
    assert "程序位置：" in source
    assert "数据位置：" in source
    assert "卸载 VideoInsight" in source


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell version parser")
def test_verifier_normalizes_only_optional_zero_revision() -> None:
    source = VERIFIER.read_text(encoding="utf-8-sig")
    function_source = source.split("function ConvertTo-ThreePartVersion", 1)[1]
    function_source = (
        "function ConvertTo-ThreePartVersion"
        + function_source.split("function Invoke-LocalHttp", 1)[0]
    )
    command = (
        "$ErrorActionPreference='Stop';"
        + function_source
        + ";@('0.2.0','0.2.0.0','0.2.0.1','0.2') | "
        "ForEach-Object { '[' + (ConvertTo-ThreePartVersion -Value $_) + ']' }"
    )

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["[0.2.0]", "[0.2.0]", "[]", "[]"]


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap compiler")
def test_bootstrap_compiles_with_windows_powershell() -> None:
    command = (
        "$ErrorActionPreference='Stop';"
        f"$source=Get-Content -LiteralPath '{str(BOOTSTRAP).replace("'", "''")}' "
        "-Raw -Encoding UTF8;"
        "Add-Type -TypeDefinition $source "
        "-ReferencedAssemblies 'System.Windows.Forms.dll' -Language CSharp"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
