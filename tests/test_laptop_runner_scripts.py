"""The laptop runner's PowerShell: portable, parseable by Windows PowerShell 5.1, self-testing.

Windows PowerShell 5.1 reads a .ps1 without a byte-order mark as the ANSI code page, so one
em dash in a string can break the script on the laptop that runs it at 3 am. And a runner that
names D:/Coleen cannot run on a second laptop. The PowerShell tests run only where
powershell.exe exists (they skip on GitHub's Linux runner).
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parent.parent / "ops" / "laptop-runner"
SCRIPTS = sorted(RUNNER.glob("*.ps1"))
CODE = SCRIPTS + [RUNNER / "runner_state.py", RUNNER / "laptop_logins.py"]
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")
needs_powershell = pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell is not installed here")


def test_the_runner_has_its_scripts():
    names = {p.name for p in SCRIPTS}
    assert {"nightly.ps1", "install.ps1", "register_task.ps1", "whatsapp_scan.ps1"} <= names


@pytest.mark.parametrize("path", CODE, ids=lambda p: p.name)
def test_no_path_of_ahsans_laptop_is_hard_coded(path):
    assert not re.search(r"[A-Za-z]:[\\/]+Coleen", path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_powershell_files_are_plain_ascii(path):
    bad = [n for n, line in enumerate(path.read_bytes().splitlines(), 1) if any(b > 127 for b in line)]
    assert not bad, f"non-ASCII on line(s) {bad[:5]}"


def test_no_secret_variable_is_ever_written_to_the_log():
    text = (RUNNER / "nightly.ps1").read_text(encoding="utf-8")
    assert not re.search(r"\bSay\b[^\n]*\$v?tok\b", text)
    assert not re.search(r"\bSay\b[^\n]*SUPABASE_ACCESS_TOKEN\b", text)


@needs_powershell
@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_powershell_parses_it(path):
    cmd = ("$t=$null; $e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
           f"'{path}', [ref]$t, [ref]$e); $e | ForEach-Object {{ \"$($_.Extent.StartLineNumber): $($_.Message)\" }}; "
           "exit $e.Count")
    p = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", cmd],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr


@needs_powershell
def test_nightly_self_test_passes():
    p = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-File", str(RUNNER / "nightly.ps1"), "-SelfTest"],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0 and "all self-tests passed" in p.stdout, p.stdout + p.stderr
