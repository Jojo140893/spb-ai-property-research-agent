# Sets up the SPB laptop runner on this laptop (SETUP.md step 3). Safe to run again: it brings
# what is there up to date. It never reads or writes Credential Manager, the Supabase bucket,
# Vercel or the scheduled tasks.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File <checkout>\ops\laptop-runner\install.ps1 -Root C:\SPB-runner
#
#   -Root     the runner folder (default C:\SPB-runner; no spaces). It ends up holding:
#               app\        a clone of the repo, detached at origin/main, refreshed by every run
#               venv311\    Python 3.11 + requirements.txt + rapidocr-onnxruntime 1.4.4 + tzdata + Chromium
#               logs\       one log per run, last_run.json
#               nightly.ps1, runner_state.py, laptop_logins.py, whatsapp_scan.ps1,
#               register_task.ps1, install.ps1, README.txt, SETUP.md   (copied from this folder)
#   -Python   a python.org Python 3.11 python.exe (found by itself when installed the usual way)
param(
    [string]$Root = "C:\SPB-runner",
    [string]$Python = "",
    [string]$RepoUrl = "https://github.com/Jojo140893/spb-ai-property-research-agent.git"
)
$ErrorActionPreference = "Continue"
$env:GIT_TERMINAL_PROMPT = "0"
$env:PYTHONUTF8 = "1"
function Step($msg) { Write-Host ""; Write-Host "== $msg" -ForegroundColor Cyan }
function Stop-Install($msg) { Write-Host "[STOP] $msg" -ForegroundColor Red; exit 1 }

if ($Root -match "\s") { Stop-Install "the runner folder must not contain spaces: $Root" }
$Root = [System.IO.Path]::GetFullPath($Root)
$App = Join-Path $Root "app"
$Venv = Join-Path $Root "venv311"
$Py = Join-Path $Venv "Scripts\python.exe"

# Never under a running nightly: this checks app\ out with --force and pip-installs into the
# venv that run is using. The nightly holds this mutex for its whole run.
$mutex = New-Object System.Threading.Mutex($false, "Global\SPBNightlyLaptop")
try { $got = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $got = $true }
if (-not $got) { Stop-Install "a nightly run is in progress on this laptop - run install.ps1 again when it has finished" }

# --- 1. the tools ----------------------------------------------------------------------------
Step "1/6 tools"
$need = [ordered]@{
    "git.exe"      = "winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements"
    "gh.exe"       = "winget install -e --id GitHub.cli --accept-source-agreements --accept-package-agreements"
    "node.exe"     = "winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements"
    "vercel.cmd"   = "npm.cmd install -g vercel     (after Node; then open a new PowerShell window)"
    "supabase.exe" = "see SETUP.md step 1 (the Supabase CLI release download)"
}
$missing = @()
foreach ($exe in $need.Keys) {
    $c = Get-Command $exe -ErrorAction SilentlyContinue
    if ($c) {
        $v = (cmd /c "`"$($c.Source)`" --version < nul 2>&1" | Select-Object -First 1)
        Write-Host ("  ok   {0,-13} {1}" -f $exe, "$v".Trim())
    } else {
        Write-Host ("  MISSING {0,-10} install: {1}" -f $exe, $need[$exe]) -ForegroundColor Red
        $missing += $exe
    }
}
if ($missing) { Stop-Install "install the missing tool(s), open a NEW PowerShell window, and run this again." }

# --- 2. Python 3.11 from python.org ------------------------------------------------------------
Step "2/6 Python 3.11 (python.org, not the Microsoft Store)"
$cands = @()
if ($Python) { $cands += $Python }
$fromPy = cmd /c "py -3.11 -c `"import sys; print(sys.executable)`" 2>nul"
if ($fromPy) { $cands += "$fromPy".Trim() }
$cands += (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"), "C:\Program Files\Python311\python.exe"
$base = $null
foreach ($c in $cands) {
    if (-not $c -or -not (Test-Path $c) -or $c -match "WindowsApps") { continue }
    $ver = & $c -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ("$ver".Trim() -eq "3.11") { $base = $c; break }
}
if (-not $base) {
    Stop-Install ("no python.org Python 3.11 found. Install it: winget install -e --id Python.Python.3.11 --accept-source-agreements --accept-package-agreements " +
                  "(NOT 3.12/3.13, NOT the Microsoft Store one), then run this again, or pass -Python <path to python.exe>.")
}
Write-Host "  using $base"

# --- 3. the code ------------------------------------------------------------------------------
Step "3/6 the code: $App at origin/main"
New-Item -ItemType Directory -Force $Root | Out-Null
if (-not (Test-Path (Join-Path $App ".git"))) {
    cmd /c "git clone -q `"$RepoUrl`" `"$App`""
    if ($LASTEXITCODE -ne 0) { Stop-Install "git clone failed. Has Jazib's GitHub account been given access, and did 'gh auth login' then 'gh auth setup-git' run?" }
}
cmd /c "git -C `"$App`" fetch -q origin"
if ($LASTEXITCODE -ne 0) { Stop-Install "git fetch failed (GitHub access?)" }
cmd /c "git -C `"$App`" checkout -q --detach --force origin/main"
Write-Host ("  " + (git -C $App log --oneline -1))

# --- 4. the venv --------------------------------------------------------------------------------
Step "4/6 the venv: $Venv"
if (-not (Test-Path $Py)) {
    & $base -m venv $Venv
    if (-not (Test-Path $Py)) { Stop-Install "could not create the venv" }
}
cmd /c "`"$Py`" -m pip install -q --upgrade pip"
cmd /c "`"$Py`" -m pip install -q -r `"$App\requirements.txt`""
if ($LASTEXITCODE -ne 0) { Stop-Install "pip install -r requirements.txt failed (output above)" }
cmd /c "`"$Py`" -m pip install -q rapidocr-onnxruntime==1.4.4 tzdata"
if ($LASTEXITCODE -ne 0) { Stop-Install "rapidocr-onnxruntime 1.4.4 would not install: is the venv really Python 3.11?" }
cmd /c "`"$Py`" -m playwright install chromium"
if ($LASTEXITCODE -ne 0) { Stop-Install "playwright could not install Chromium" }

# --- 5. the runner's own files --------------------------------------------------------------------
Step "5/6 the runner's files into $Root"
$files = "nightly.ps1", "runner_state.py", "laptop_logins.py", "whatsapp_scan.ps1", "register_task.ps1", "install.ps1", "README.txt", "SETUP.md"
$src = $PSScriptRoot
if ((Test-Path (Join-Path $App "ops\laptop-runner\nightly.ps1"))) { $src = Join-Path $App "ops\laptop-runner" }
if ([System.IO.Path]::GetFullPath($src) -ne $Root) {
    foreach ($f in $files) {
        if (Test-Path (Join-Path $src $f)) { Copy-Item -Force (Join-Path $src $f) (Join-Path $Root $f); Write-Host "  $f" }
        else { Write-Host "  (not in ${src}: $f)" -ForegroundColor Yellow }
    }
}
New-Item -ItemType Directory -Force (Join-Path $Root "logs") | Out-Null

# --- 6. prove it ------------------------------------------------------------------------------------
Step "6/6 can this venv run the harvest the way the runner does?"
cmd /c "`"$Py`" `"$(Join-Path $Root 'runner_state.py')`" env-check"
if ($LASTEXITCODE -ne 0) { Stop-Install "the venv is not right (FAIL lines above)" }

Write-Host ""
Write-Host "INSTALLED in $Root. Next (SETUP.md):" -ForegroundColor Green
Write-Host "  step 4  the logins - the laptop's owner types them himself, nobody else:"
Write-Host "          $Py $Root\laptop_logins.py"
Write-Host "  step 5  the dry check:"
Write-Host "          powershell -NoProfile -ExecutionPolicy Bypass -File $Root\nightly.ps1 -Check"
exit 0
