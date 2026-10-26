# The SPB nightly harvest on a Windows laptop, a stand-in while GitHub Actions refuses jobs
# (billing failed 28 Sep - 1 Oct 2026, and again from 24 Oct 2026 ~13:00 UTC). It does what
# .github/workflows/daily-harvest.yml does:
#   pull the shared database -> run_daily.py -> push it back -> vercel deploy --prod
#   -> check the login gate -> mark the Melbourne day done in the bucket
# from <Root>\app, a clone (or detached worktree) of origin/main that nothing else edits, with
# its own Python 3.11 venv in <Root>\venv311. NOT Python 3.13 and NOT the Microsoft Store
# Python: on 3.13 rapidocr-onnxruntime stops at 1.2.3, which re-reads the picture price lists
# into different row identities (5 of 133 matched, against 168 of 172 with 1.4.4).
#
# SETUP.md (beside this file) sets a laptop up. README.txt says what is where.
#
#   nightly.ps1              scheduled: once per Melbourne day, from 09:00 Melbourne
#   nightly.ps1 -Now         run now whatever the hour (still skips a day already done)
#   nightly.ps1 -Force       run even if the day is done, the clocks disagree or a lock is held
#   nightly.ps1 -Check       the dry check: proves every login and tool, changes nothing shared
#   nightly.ps1 -SelfTest    tests this script's own clock rule, offline
#   -Root <folder>           the runner folder (default: the one holding app\ beside this script)
#
# ONE RUN PER MELBOURNE DAY, ACROSS LAPTOPS. A laptop that pushed and deployed writes
# ss:///spb-state/laptop-runs/<Melbourne day>.json (runner_state.py day-mark); every laptop reads
# it first and stands down when the day is done. A harvest still RUNNING elsewhere has no
# marker yet: the bucket lock covers that one. This script reads the lock first and, while it
# is younger than -LockWaitHours, stands down (exit 0) and lets its next trigger look again.
# Schedule the primary laptop at 03:00/04:00 Pakistan time and a backup later (06:00/08:00),
# so the backup only harvests on a day the primary missed.
#
# THE LAPTOP'S CLOCK. run_daily.py dates rows with this machine's local date. GitHub sets
# TZ=Australia/Melbourne; a laptop on Pakistan time (UTC+5) is 5 or 6 hours behind Melbourne,
# so a run started between 18:00 and 23:59 Pakistan time would date every row a day early,
# and one that crosses either midnight would date them two ways. So a run starts only when the
# two dates agree AND at least -MinHoursLeft hours remain before either calendar turns over.
# The machine's time zone is never changed.
param(
    [string]$Root = "",
    [switch]$Now,
    [switch]$Force,
    [switch]$Check,
    [switch]$SelfTest,
    [double]$MinHoursLeft = 6,
    [double]$LockWaitHours = 8
)

$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:GIT_TERMINAL_PROMPT = "0"                      # a git that wants a password fails, never waits
$Repo = "Jojo140893/spb-ai-property-research-agent"
$SpbRef = "dvivicgzphyxtxpkgvjn"                      # the SPB Supabase project
$VercelScope = "digitalx-solutions-projects"
# The dashboard's Vercel project, as vercel_site\.vercel\project.json names it on Ahsan's laptop.
# Identifiers, not secrets: without them `vercel deploy --yes` in a fresh vercel_site folder
# would LINK A NEW PROJECT named after the folder instead of deploying the real one.
$VercelProject = '{"projectId":"prj_4jr2ydXWUJaIRAqJnUUcpQjJICPf","orgId":"team_vKH7XnJUPbscDU5GYRNNLbMR","projectName":"vercel_site"}'
$SiteUrl = "https://app.smartpropertybuying.com.au"
$MelTz = [System.TimeZoneInfo]::FindSystemTimeZoneById("AUS Eastern Standard Time")

# =============================================================================================
# the clock rule (pure; -SelfTest exercises it)
# =============================================================================================
function Get-UtcOfLocalMidnight([datetime]$Day, [System.TimeZoneInfo]$Tz) {
    $d = [datetime]::SpecifyKind($Day.Date, [System.DateTimeKind]::Unspecified)
    for ($i = 0; $i -lt 3; $i++) {
        try { return [System.TimeZoneInfo]::ConvertTimeToUtc($d, $Tz) }
        catch { $d = $d.AddHours(1) }               # a midnight skipped by a DST change
    }
    return [System.TimeZoneInfo]::ConvertTimeToUtc($d, $Tz)
}

function Format-Hour([double]$h) {
    $m = [int][math]::Round($h * 60)
    return "{0:00}:{1:00}" -f [math]::Floor($m / 60), ($m % 60)
}

function Get-ClockVerdict {
    param([datetime]$UtcNow, [System.TimeZoneInfo]$LocalTz, [System.TimeZoneInfo]$MelTz, [double]$MinHoursLeft)
    $utc = [datetime]::SpecifyKind($UtcNow, [System.DateTimeKind]::Utc)
    $loc = [datetime]::SpecifyKind([System.TimeZoneInfo]::ConvertTimeFromUtc($utc, $LocalTz), [System.DateTimeKind]::Unspecified)
    $mel = [datetime]::SpecifyKind([System.TimeZoneInfo]::ConvertTimeFromUtc($utc, $MelTz), [System.DateTimeKind]::Unspecified)
    $ahead = ($MelTz.GetUtcOffset($utc) - $LocalTz.GetUtcOffset($utc)).TotalHours
    # The hours of THIS laptop's clock in which its date and Melbourne's agree.
    if ($ahead -ge 0) { $from = 0; $to = 24 - $ahead } else { $from = -$ahead; $to = 24 }
    $latest = $to - $MinHoursLeft
    $startText = if ($latest -ge $from) { "start it between {0} and {1} on this laptop's clock" -f (Format-Hour $from), (Format-Hour $latest) }
                 else { "no start time on this laptop's clock leaves $MinHoursLeft hours" }
    $nextLoc = Get-UtcOfLocalMidnight $loc.AddDays(1) $LocalTz
    $nextMel = Get-UtcOfLocalMidnight $mel.AddDays(1) $MelTz
    $toLoc = ($nextLoc - $utc).TotalHours
    $toMel = ($nextMel - $utc).TotalHours
    $left = [math]::Min($toLoc, $toMel)
    $which = if ($toMel -le $toLoc) { "Melbourne's midnight" } else { "this laptop's midnight" }
    $sameDay = $loc.Date -eq $mel.Date
    $ok = $sameDay -and ($left -ge $MinHoursLeft)
    $reason = ""
    if (-not $sameDay) {
        $reason = ("this laptop's date is {0:yyyy-MM-dd} ({0:HH:mm}, {1}) but Melbourne's is {2:yyyy-MM-dd} ({2:HH:mm}). " +
                   "The harvest dates every row by this laptop's clock, so they would all be a day wrong. " +
                   "The two dates agree from {3} to {4} here: {5}.") -f $loc, $LocalTz.Id, $mel, (Format-Hour $from), (Format-Hour $to), $startText
    } elseif ($left -lt $MinHoursLeft) {
        $reason = ("only {0} h left before {1}, and a run (up to ~4.5 h) must end on the date it started, " +
                   "here and in Melbourne, or it dates its rows two ways: {2}.") -f $left.ToString("0.0", [System.Globalization.CultureInfo]::InvariantCulture), $which, $startText
    }
    return [pscustomobject]@{ Ok = $ok; Local = $loc; Mel = $mel; HoursLeft = $left; Window = "{0}-{1}" -f (Format-Hour $from), (Format-Hour $to); StartText = $startText; Reason = $reason }
}

# The deploy's command line. The Vercel token never appears in it: cmd.exe expands
# %SPB_VERCEL_TOKEN% from this run's environment, so neither this string nor the log holds it.
function Get-DeployCommand([bool]$WithToken, [string]$LogPath) {
    $tok = if ($WithToken) { " --token %SPB_VERCEL_TOKEN%" } else { "" }
    return "vercel deploy --prod --yes --scope $VercelScope$tok < nul >> `"$LogPath`" 2>&1"
}

function Invoke-SelfTest {
    $pk = [System.TimeZoneInfo]::FindSystemTimeZoneById("Pakistan Standard Time")
    $cases = @(
        @{ utc = "2026-10-26T22:00:00Z"; tz = $pk; ok = $true;  want = "";             why = "03:00 Pakistan = 09:00 Melbourne (AEDT): the primary's slot" },
        @{ utc = "2026-10-27T01:00:00Z"; tz = $pk; ok = $true;  want = "";             why = "06:00 Pakistan = 12:00 Melbourne: the backup's first slot" },
        @{ utc = "2026-10-27T03:00:00Z"; tz = $pk; ok = $true;  want = "";             why = "08:00 Pakistan = 14:00 Melbourne: the backup's second slot" },
        @{ utc = "2026-10-27T07:00:00Z"; tz = $pk; ok = $true;  want = "";             why = "12:00 Pakistan: exactly 6 h before Melbourne's midnight" },
        @{ utc = "2026-10-27T08:00:00Z"; tz = $pk; ok = $false; want = "only 5.0 h";   why = "13:00 Pakistan: 5 h left, a long run would cross Melbourne's midnight" },
        @{ utc = "2026-10-27T13:00:00Z"; tz = $pk; ok = $false; want = "a day wrong";  why = "18:00 Pakistan = 00:00 Melbourne next day (AEDT)" },
        @{ utc = "2026-10-27T18:59:00Z"; tz = $pk; ok = $false; want = "a day wrong";  why = "23:59 Pakistan = 05:59 Melbourne next day" },
        @{ utc = "2026-10-27T19:00:00Z"; tz = $pk; ok = $true;  want = "";             why = "00:00 Pakistan = 06:00 Melbourne: the clocks agree (the 09:00 gate holds a scheduled run)" },
        @{ utc = "2026-07-15T13:30:00Z"; tz = $pk; ok = $false; want = "only 0.5 h";   why = "18:30 Pakistan = 23:30 Melbourne (AEST): same date, 30 min left" },
        @{ utc = "2026-07-15T14:00:00Z"; tz = $pk; ok = $false; want = "a day wrong";  why = "19:00 Pakistan = 00:00 Melbourne next day (AEST)" },
        @{ utc = "2026-07-15T23:00:00Z"; tz = $pk; ok = $true;  want = "";             why = "04:00 Pakistan = 09:00 Melbourne (AEST)" },
        @{ utc = "2026-10-03T22:00:00Z"; tz = $pk; ok = $true;  want = "";             why = "4 Oct, the day AEDT starts: 03:00 Pakistan = 09:00 Melbourne" },
        @{ utc = "2026-10-26T22:00:00Z"; tz = $MelTz; ok = $true;  want = "";          why = "a laptop on Melbourne time at 09:00" },
        @{ utc = "2026-10-27T09:00:00Z"; tz = $MelTz; ok = $false; want = "only 4.0 h"; why = "a laptop on Melbourne time at 20:00: the run would cross midnight" }
    )
    $failed = 0
    foreach ($c in $cases) {
        $u = [datetime]::Parse($c.utc, [System.Globalization.CultureInfo]::InvariantCulture,
                               [System.Globalization.DateTimeStyles]::AdjustToUniversal)
        $v = Get-ClockVerdict -UtcNow $u -LocalTz $c.tz -MelTz $MelTz -MinHoursLeft 6
        $pass = ($v.Ok -eq $c.ok) -and ((-not $c.want) -or $v.Reason.Contains($c.want))
        if (-not $pass) { $failed++ }
        Write-Host ("{0}  {1}  -> {2}{3}" -f $(if ($pass) { "PASS" } else { "FAIL" }), $c.why,
                    $(if ($v.Ok) { "runs" } else { "refused" }), $(if ($pass) { "" } else { "  [$($v.Reason)]" }))
    }
    $pkWindow = (Get-ClockVerdict -UtcNow ([datetime]::Parse("2026-10-26T22:00:00Z", [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AdjustToUniversal)) -LocalTz $pk -MelTz $MelTz -MinHoursLeft 6).StartText
    $pass = $pkWindow -eq "start it between 00:00 and 12:00 on this laptop's clock"
    if (-not $pass) { $failed++ }
    Write-Host ("{0}  Pakistan in AEDT: '{1}'" -f $(if ($pass) { "PASS" } else { "FAIL" }), $pkWindow)

    $env:SPB_VERCEL_TOKEN = "selftest-not-a-real-token-0123456789"
    $line = Get-DeployCommand $true "C:\x\nightly.log"
    $pass = $line.Contains("%SPB_VERCEL_TOKEN%") -and -not $line.Contains($env:SPB_VERCEL_TOKEN) -and $line.Contains("< nul")
    Remove-Item Env:SPB_VERCEL_TOKEN -ErrorAction SilentlyContinue
    if (-not $pass) { $failed++ }
    Write-Host ("{0}  the deploy command carries %SPB_VERCEL_TOKEN%, never the token itself" -f $(if ($pass) { "PASS" } else { "FAIL" }))
    $line = Get-DeployCommand $false "C:\x\nightly.log"
    $pass = -not $line.Contains("--token")
    if (-not $pass) { $failed++ }
    Write-Host ("{0}  without a stored token the CLI's own login is used (no --token)" -f $(if ($pass) { "PASS" } else { "FAIL" }))

    Write-Host $(if ($failed) { "$failed self-test(s) FAILED" } else { "all self-tests passed" })
    return [int]($failed -gt 0)
}

if ($SelfTest) { exit (Invoke-SelfTest) }

# =============================================================================================
# where things are
# =============================================================================================
if (-not $Root) {
    if (Test-Path (Join-Path $PSScriptRoot "app")) { $Root = $PSScriptRoot }
    else {
        # run from inside a checkout: <Root>\app\ops\laptop-runner\nightly.ps1
        $up = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
        if ($up -and (Test-Path (Join-Path $up "app"))) { $Root = $up } else { $Root = $PSScriptRoot }
    }
}
$Root = [System.IO.Path]::GetFullPath($Root)
$App = Join-Path $Root "app"
$Py = Join-Path $Root "venv311\Scripts\python.exe"
$Logs = Join-Path $Root "logs"
$Helper = Join-Path $PSScriptRoot "runner_state.py"
$Logins = Join-Path $PSScriptRoot "laptop_logins.py"
$Machine = $env:COMPUTERNAME
New-Item -ItemType Directory -Force $Logs | Out-Null
$Log = Join-Path $Logs ("{0}-{1}.log" -f $(if ($Check) { "check" } else { "nightly" }), (Get-Date -Format "yyyyMMdd-HHmm"))
$Status = Join-Path $Logs "last_run.json"
$Done = Join-Path $Logs "last_success.txt"

function Say($msg) {
    $mel = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId((Get-Date), "AUS Eastern Standard Time")
    $line = "[{0} | Melbourne {1}] {2}" -f (Get-Date -Format "HH:mm:ss"), $mel.ToString("HH:mm"), $msg
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Host $line
}

$script:CheckFailed = 0
function Report([string]$State, [string]$Text) {
    if ($State -eq "FAIL") { $script:CheckFailed++ }
    Say ("{0,-4}  {1}" -f $State, $Text)
}

# The venv's Python with cmd.exe doing the redirection (PowerShell 5.1 turns a native
# program's stderr into error records). Output goes to the log; the exit code comes back.
function Invoke-Py([string]$ArgLine, [string]$WorkDir) {
    if (-not $WorkDir) { $WorkDir = $App }
    $tmp = Join-Path $Logs (".py-out-{0}.txt" -f $PID)
    Push-Location $WorkDir
    try {
        cmd /c "`"$Py`" -u $ArgLine > `"$tmp`" 2>&1 < nul"
        $code = $LASTEXITCODE
    } finally { Pop-Location }
    foreach ($l in @(Get-Content -Path $tmp -Encoding UTF8 -ErrorAction SilentlyContinue)) { Say "      $l" }
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    return $code
}

function Get-VaultValue([string]$Key) {
    $v = & $Py -c "import keyring,sys; sys.stdout.write(keyring.get_password('SPB-PropertyAgent', sys.argv[1]) or '')" $Key 2>$null
    if ($v) { return "$v".Trim() }
    return ""
}

function Test-SupabaseSeesSpb {
    $refs = cmd /c "supabase projects list --output json < nul 2>nul"
    return ("$refs" -match ('"ref":\s*"' + $SpbRef + '"'))
}

# The app folder must be linked to the SPB project: state_sync.py and runner_state.py use
# --linked. A local file only (supabase\.temp, gitignored); nothing in the project changes.
function Confirm-SupabaseLink {
    $refFile = Join-Path $App "supabase\.temp\project-ref"
    $linked = { (Test-Path $refFile) -and ((Get-Content $refFile -Raw).Trim() -eq $SpbRef) }
    if (& $linked) { return $true }
    Say "linking $App to the SPB Supabase project ($SpbRef) - a local file only"
    Push-Location $App
    try { cmd /c "supabase link --project-ref $SpbRef < nul >> `"$Log`" 2>&1" } finally { Pop-Location }
    return (& $linked)
}

# The strict gate probe from deploy.ps1: no redirects followed (a hop to the Vercel SSO wall
# answers 200 and once read as "up" for ten days), status and Location both readable.
function Invoke-Probe([string]$Url) {
    try {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor [System.Net.SecurityProtocolType]::Tls12
        $req = [System.Net.HttpWebRequest]::Create($Url)
        $req.AllowAutoRedirect = $false
        $req.Timeout = 30000
        $req.UserAgent = "spb-laptop-nightly"
        $resp = $req.GetResponse()
    } catch [System.Net.WebException] {
        if ($_.Exception.Response) {
            $r2 = $_.Exception.Response
            $b = (New-Object System.IO.StreamReader($r2.GetResponseStream())).ReadToEnd()
            return @{ code = [int]$r2.StatusCode; body = $b; location = $r2.Headers["Location"] }
        }
        return @{ code = 0; body = ""; location = ""; why = $_.Exception.Message }
    } catch {
        return @{ code = 0; body = ""; location = ""; why = $_.Exception.Message }
    }
    $b = (New-Object System.IO.StreamReader($resp.GetResponseStream())).ReadToEnd()
    $out = @{ code = [int]$resp.StatusCode; body = $b; location = $resp.Headers["Location"] }
    $resp.Close()
    return $out
}

function Get-GateState {
    $stock = Invoke-Probe "$SiteUrl/stock.json"
    if ($stock.code -eq 0) { return "DOWN? stock.json unreachable: $($stock.why)" }
    if ("$($stock.location)" -match "vercel\.com/sso") { return "DOWN? stock.json redirects to the Vercel SSO wall" }
    if ($stock.code -eq 200) { return "ALARM: stock.json is served to ANYONE - the login gate is NOT running" }
    if ($stock.code -eq 503 -and $stock.body -match "SITE_PASSWORD is not set") { return "DOWN? SITE_PASSWORD is not set on the Vercel project" }
    if ($stock.code -ne 401) { return "DOWN? stock.json answered $($stock.code), expected 401" }
    $login = Invoke-Probe "$SiteUrl/login"
    if ("$($login.location)" -match "vercel\.com/sso") { return "DOWN? /login redirects to the Vercel SSO wall" }
    if ($login.code -ne 200) { return "DOWN? /login answered $($login.code), expected 200" }
    if ($login.body -notmatch 'name="password"') { return "DOWN? /login has no sign-in form" }
    return "up (stock.json 401)"
}

# Did GitHub PUBLISH this Melbourne day? Judged by its "Publish to Vercel" step, never by the
# run's conclusion: the gated sibling cron run is "success" with its harvest skipped (1 Oct),
# and a run that published but went red on a dead channel (Hermitage, 24 Oct) is "failure".
function Get-GitHubPublished([string]$Day) {
    try {
        $runs = gh run list --repo $Repo --workflow daily-harvest.yml --limit 10 --json databaseId,createdAt 2>$null | ConvertFrom-Json
        foreach ($r in $runs) {
            $at = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId(([datetime]$r.createdAt).ToUniversalTime(), "AUS Eastern Standard Time")
            if ($at.ToString("yyyy-MM-dd") -ne $Day) { continue }
            $jobs = (gh run view $r.databaseId --repo $Repo --json jobs 2>$null | ConvertFrom-Json).jobs
            $pub = $jobs | Where-Object { $_.name -eq "harvest" } | ForEach-Object { $_.steps } |
                   Where-Object { $_.name -eq "Publish to Vercel" -and $_.conclusion -eq "success" }
            if ($pub) { return "run $($r.databaseId), $($r.createdAt)" }
        }
    } catch { }
    return ""
}

# =============================================================================================
# the layout this script needs
# =============================================================================================
$missing = @()
if (-not (Test-Path (Join-Path $App ".git"))) { $missing += "$App (a checkout of origin/main)" }
if (-not (Test-Path $Py)) { $missing += "$Py (the Python 3.11 venv)" }
if (-not (Test-Path $Helper)) { $missing += "$Helper" }
if ($missing) {
    Say "[ABORT] the runner folder $Root is incomplete - missing: $($missing -join '; '). Run install.ps1 (SETUP.md step 3) or pass -Root."
    exit 1
}

$clock = Get-ClockVerdict -UtcNow ([datetime]::UtcNow) -LocalTz ([System.TimeZoneInfo]::Local) -MelTz $MelTz -MinHoursLeft $MinHoursLeft
$mel = $clock.Mel
$today = $mel.ToString("yyyy-MM-dd")

# =============================================================================================
# -Check: prove everything a run needs, change nothing shared
# =============================================================================================
if ($Check) {
    Say "SPB laptop runner - dry check on $Machine, runner folder $Root"
    Say "Nothing here takes the bucket lock, writes to the bucket, pulls the database, harvests or deploys."
    # 1. the clock
    if ($clock.Ok) { Report "PASS" ("clock: this laptop and Melbourne agree on {0}; {1:N1} h before the next midnight" -f $today, $clock.HoursLeft) }
    else { Report "INFO" "clock: a run started NOW would be refused - $($clock.Reason)" }
    Report "INFO" ("clock: this laptop is {0} ({1}); a run may start {2}" -f [System.TimeZoneInfo]::Local.Id, (Get-Date -Format "HH:mm"), ($clock.StartText -replace "^start it ", ""))
    # 2. Python, OCR, time zones, Credential Manager, Chromium
    $rc = Invoke-Py "`"$Helper`" env-check"
    if ($rc -eq 0) { Report "PASS" "Python 3.11 venv: OCR 1.4.4, tzdata, Credential Manager and Chromium all work" }
    else { Report "FAIL" "the venv cannot run the harvest as the runner does (lines above) - re-run install.ps1" }
    # 3. logins: Supabase, Vercel, E-Agent (presence, and tokens checked read-only)
    $tok = Get-VaultValue "supabase:access_token"
    if ($tok) { $env:SUPABASE_ACCESS_TOKEN = $tok }
    Remove-Variable tok
    $rc = Invoke-Py "`"$Logins`" --status"
    if ($rc -eq 0) { Report "PASS" "logins: Supabase, Vercel and E-Agent are ready" }
    else { Report "FAIL" "logins: not ready (lines above) - run laptop_logins.py yourself (SETUP.md step 4)" }
    # 4. the shared bucket, read-only
    if (Confirm-SupabaseLink) {
        Report "PASS" "app folder linked to the SPB Supabase project"
        $rc = Invoke-Py "`"$Helper`" --app `"$App`" bucket-check"
        if ($rc -eq 0) { Report "PASS" "bucket spb-state readable: the shared database is there" } else { Report "FAIL" "bucket spb-state not readable (lines above)" }
        $rc = Invoke-Py "`"$Helper`" --app `"$App`" day-status --day $today"
        Report "INFO" $(if ($rc -eq 3) { "Melbourne day $today is already done (above): a run today would stand down" } elseif ($rc -eq 0) { "Melbourne day $today is not done yet" } else { "could not read the day marker" })
        $rc = Invoke-Py "`"$Helper`" --app `"$App`" lock-status --wait-hours $LockWaitHours"
        Report "INFO" $(if ($rc -eq 0) { "bucket lock: free" } elseif ($rc -eq 3) { "bucket lock: a harvest is running somewhere (above)" } else { "bucket lock: see above" })
    } else { Report "FAIL" "could not link $App to the SPB project - the Supabase login cannot see $SpbRef" }
    # 5. GitHub: the code, and the skip check
    $null = git -C $App ls-remote --exit-code origin refs/heads/main 2>$null
    if ($LASTEXITCODE -eq 0) { Report "PASS" "git: can fetch $Repo" } else { Report "FAIL" "git: cannot reach $Repo (repo access? run: gh auth setup-git)" }
    $null = gh auth status 2>$null
    if ($LASTEXITCODE -ne 0) { Report "FAIL" "gh: not signed in (gh auth login) - the GitHub 'already published today' skip cannot work" }
    else {
        $last = $null
        try { $last = gh run list --repo $Repo --workflow daily-harvest.yml --limit 1 --json databaseId,conclusion,createdAt 2>$null | ConvertFrom-Json } catch { }
        if ($last) { Report "PASS" ("gh: reads GitHub's daily-harvest runs (latest {0}: {1})" -f $last[0].createdAt, $(if ($last[0].conclusion) { $last[0].conclusion } else { "running" })) }
        else { Report "FAIL" "gh: signed in but cannot read $Repo's Actions runs" }
        $pub = Get-GitHubPublished $today
        if ($pub) { Report "INFO" "GitHub already published Melbourne day $today ($pub): a run today would stand down" }
    }
    # 6. the disk (9 Oct: C: filled to 0 bytes under agent scratch)
    $drive = (Get-Item $Root).PSDrive
    $freeGb = [math]::Round($drive.Free / 1GB, 1)
    if ($freeGb -ge 5) { Report "PASS" "disk ${drive}: $freeGb GB free" }
    elseif ($freeGb -ge 2) { Report "WARN" "disk ${drive}: only $freeGb GB free (a run keeps ~35 MB database copies a day, Chromium needs room)" }
    else { Report "FAIL" "disk ${drive}: only $freeGb GB free" }
    # 7. the schedule
    $task = Get-ScheduledTask -TaskName "SPB Nightly (laptop)" -ErrorAction SilentlyContinue
    if ($task) { Report "INFO" ("scheduled task 'SPB Nightly (laptop)': {0}, at {1}" -f $task.State, (($task.Triggers | ForEach-Object { ([datetime]$_.StartBoundary).ToString("HH:mm") }) -join ", ")) }
    else { Report "INFO" "scheduled task 'SPB Nightly (laptop)' is not registered yet (register_task.ps1)" }
    Remove-Item Env:SUPABASE_ACCESS_TOKEN -ErrorAction SilentlyContinue
    if ($script:CheckFailed) { Say "DRY CHECK: $($script:CheckFailed) problem(s) - fix the FAIL lines before the first run. Log: $Log"; exit 1 }
    Say "DRY CHECK PASSED - this laptop can run the nightly. Log: $Log"
    exit 0
}

# =============================================================================================
# the run
# =============================================================================================
# --- one at a time on this machine (the bucket lock covers other machines) ----------------
# The same name as the 25 Oct runner's, so the old and new scripts exclude each other too.
$mutex = New-Object System.Threading.Mutex($false, "Global\SPBNightlyLaptop")
try { $got = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $got = $true }
if (-not $got) { exit 0 }

# --- gates, cheapest first ---------------------------------------------------------------
if (-not $Force) {
    if ((Test-Path $Done) -and ((Get-Content $Done -Raw).Trim() -eq $today)) { exit 0 }
    if (-not $Now -and $mel.Hour -lt 9) { exit 0 }
    if (-not $clock.Ok) {
        Say "[REFUSED] not starting: $($clock.Reason) (-Force overrides this.)"
        exit 1
    }
}

Say "SPB nightly (laptop $Machine) starting for Melbourne day $today - runner folder $Root"

# --- the shared database: the token is set for this run's processes only, never logged ----
$tok = Get-VaultValue "supabase:access_token"
if ($tok) { $env:SUPABASE_ACCESS_TOKEN = $tok }
Remove-Variable tok
if (-not (Test-SupabaseSeesSpb)) {
    Say "[ABORT] the Supabase login cannot see the SPB project ($SpbRef): store a token with laptop_logins.py, or 'supabase login' with its owner's account. Not harvesting."
    exit 1
}
if (-not (Confirm-SupabaseLink)) {
    Say "[ABORT] could not link $App to the SPB project. Not harvesting."
    exit 1
}

if (-not $Force) {
    # Another laptop already finished this Melbourne day?
    $rc = Invoke-Py "`"$Helper`" --app `"$App`" day-status --day $today"
    if ($rc -eq 3) {
        Say "another run already finished Melbourne day $today (machine above) - nothing to do."
        Set-Content -Path $Done -Value $today -Encoding ASCII
        exit 0
    }
    if ($rc -ne 0) { Say "!! could not read the day marker - carrying on (the bucket lock still stops two runs at once)" }
    # GitHub published it?
    $pub = Get-GitHubPublished $today
    if ($pub) {
        Say "GitHub's daily-harvest already published today ($pub) - nothing to do."
        Set-Content -Path $Done -Value $today -Encoding ASCII
        exit 0
    }
    # A harvest RUNNING elsewhere: stand down now, rather than fail on the pull, and let the
    # next trigger look again (by then that run has usually finished and marked the day).
    $rc = Invoke-Py "`"$Helper`" --app `"$App`" lock-status --wait-hours $LockWaitHours"
    if ($rc -eq 3) {
        Say "a harvest is running on another machine (the lock holder above) - standing down; the next trigger looks again."
        exit 0
    }
    if ($rc -eq 4) { Say "the bucket lock is older than $LockWaitHours h - the pull will break it as abandoned" }
}

# FAIL EARLY, as daily-harvest.yml does: without the E-Agent login the harvest skips E-Agent,
# still prints SUCCESS and publishes its ~5,000 listings frozen. Prints 0/1 per key, never a value.
$have = & $Py -c "import keyring; print(''.join('1' if keyring.get_password('SPB-PropertyAgent', k) else '0' for k in ('e_agent:username', 'e_agent:password')))" 2>$null
if ("$have".Trim() -ne "11") {
    Say "[ABORT] no E-Agent login in Credential Manager - run laptop_logins.py first. Not harvesting."
    exit 1
}

# --- the code: exactly origin/main, as a fresh GitHub checkout would be --------------------
cmd /c "git -C `"$App`" fetch -q origin >> `"$Log`" 2>&1"
if ($LASTEXITCODE -ne 0) { Say "!! git fetch failed - running the code already checked out" }
cmd /c "git -C `"$App`" checkout -q --detach --force origin/main >> `"$Log`" 2>&1"
$head = (git -C $App log --oneline -1) 2>$null
Say "code: $head"
# This script is a COPY in the runner folder; say when main's copy has moved on.
$mainCopy = Join-Path $App "ops\laptop-runner"
if ((Test-Path $mainCopy) -and ($PSScriptRoot -ne $mainCopy)) {
    foreach ($f in "nightly.ps1", "runner_state.py", "laptop_logins.py", "whatsapp_scan.ps1") {
        $a = Join-Path $PSScriptRoot $f; $b = Join-Path $mainCopy $f
        if ((Test-Path $a) -and (Test-Path $b) -and ((Get-FileHash $a).Hash -ne (Get-FileHash $b).Hash)) {
            Say "note: ops/laptop-runner/$f on main differs from this runner's copy - see SETUP.md 'Updating the runner'"
        }
    }
}
cmd /c "`"$Py`" -m pip install -q -r `"$App\requirements.txt`" >> `"$Log`" 2>&1"
# The OCR library is pinned here, not by requirements.txt (>=1.3.0): another version reads the
# picture price lists into different rows. tzdata: Windows has no zoneinfo database. The
# browser: a playwright upgrade from requirements.txt would otherwise leave no Chromium.
cmd /c "`"$Py`" -m pip install -q rapidocr-onnxruntime==1.4.4 tzdata >> `"$Log`" 2>&1"
cmd /c "`"$Py`" -m playwright install chromium >> `"$Log`" 2>&1"
if ((Invoke-Py "`"$Helper`" env-check") -ne 0) {
    Say "[ABORT] this venv cannot run the harvest the way the runner does (above). Not harvesting."
    exit 1
}

# --- the environment the workflow sets (credentials come from Windows Credential Manager) --
$env:SPB_STATUS_PASS = "1"
$env:IMAP_HOST = "imap.gmail.com"
$env:IMAP_PORT = "993"
$env:SPB_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
# A laptop run is slower than GitHub's (4 h 15 min on 26 Oct), so a laptop treats a lock as
# abandoned only after LockWaitHours, not state_sync's default 4: it must never break the lock
# of a run that is merely slow.
$env:SPB_LOCK_STALE_HOURS = [string][int][math]::Ceiling($LockWaitHours)

# Left over from the last run: they would be reported as tonight's.
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $App "_harvest_partial"), (Join-Path $App "_run_degraded")
# Set the local sessions aside before the pull: a session the pull does not bring back is
# simply missing tonight, rather than an old one pushed back over the bucket's good one.
$sess = Join-Path $App ".sessions"
if (Test-Path $sess) {
    $aside = Join-Path $Root ("session-backups\pre-pull-{0}" -f (Get-Date -Format "yyyyMMdd-HHmm"))
    New-Item -ItemType Directory -Force $aside | Out-Null
    Get-ChildItem $sess -Filter "*.json" | Move-Item -Destination $aside -Force
}

# --- harvest ------------------------------------------------------------------------------
# PROXIMA IS SKIPPED while <Root>\skip-proxima.flag exists (30 Sep 2026: a stored Proxima
# password was refused, and every nightly would add one more failed sign-in). run_daily has no
# per-channel switch, so this wrapper does the pull and the push itself and sets Proxima's
# session file aside in between: with no session file ProximaSource.search() returns before
# opening a browser. The push copies local *.json only, so the bucket's Proxima session is left
# exactly as it was. Delete the flag file to harvest Proxima again.
$skipProxima = Test-Path (Join-Path $Root "skip-proxima.flag")
Push-Location $App
if (-not $skipProxima) {
    $env:SPB_STATE_SYNC = "1"
    cmd /c "`"$Py`" -u run_daily.py --state-sync --no-deploy --email-days 7 >> `"$Log`" 2>&1"
    $rc = $LASTEXITCODE
    # run_daily treats the push as optional, so a failed upload (bucket keeps the old database,
    # lock still held) would otherwise end "finished OK". Its success line is "lock released".
    $pushed = Select-String -Path $Log -Pattern "^\s+lock released" -Quiet
    if (-not $pushed) { Say "!! no 'lock released' line: the push did not finish - the bucket lock may still be held" }
} else {
    $pushed = $false
    $env:SPB_STATE_SYNC = "0"
    Say "Proxima SKIPPED (skip-proxima.flag) - no Proxima sign-in will be attempted"
    cmd /c "`"$Py`" -u state_sync.py pull >> `"$Log`" 2>&1"
    if ($LASTEXITCODE -ne 0) {
        Say "[ABORT] could not pull the shared database (locked by another run, or unreachable) - not harvesting"
        $rc = 1
    } else {
        $prox = Join-Path $App ".sessions\portal_proxima.json"
        if (Test-Path $prox) { Move-Item -Force $prox "$prox.skipped" }
        try {
            cmd /c "`"$Py`" -u run_daily.py --no-deploy --email-days 7 >> `"$Log`" 2>&1"
            $rc = $LASTEXITCODE
        } finally {
            # ALWAYS: the push is what releases the bucket lock.
            cmd /c "`"$Py`" -u state_sync.py push >> `"$Log`" 2>&1"
            if ($LASTEXITCODE -ne 0) { Say "!! state_sync push FAILED ($LASTEXITCODE) - the lock may still be held"; $rc = 1 }
            $pushed = Select-String -Path $Log -Pattern "^\s+lock released" -Quiet
        }
    }
}
Pop-Location
# Another machine took the lock between the check above and the pull: nothing was pulled,
# harvested or pushed here, and that run owns the day. Stand down, as the check would have.
if ($rc -ne 0 -and (Select-String -Path $Log -Pattern "^\s*!! locked by" -Quiet)) {
    Say "the pull was refused: another run holds the bucket lock (above) - nothing done here; the next trigger looks again."
    exit 0
}
# Each pull keeps a ~35 MB copy of the previous local database; a week of them is plenty.
Get-ChildItem $App -Filter "spb_research_audit.before-pull-*.db" -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } | Remove-Item -Force -ErrorAction SilentlyContinue
Say "run_daily.py exited $rc"

# --- publish, only on success (0 includes PARTIAL and DEGRADED, as on GitHub) ---------------
$deploy = "skipped"
if ($rc -eq 0) {
    $site = Join-Path $App "vercel_site"
    $link = Join-Path $site ".vercel\project.json"
    $want = $VercelProject | ConvertFrom-Json
    if (-not (Test-Path $link)) {
        New-Item -ItemType Directory -Force (Split-Path $link) | Out-Null
        Set-Content -Path $link -Value $VercelProject -Encoding ASCII
        Say "vercel_site linked to the dashboard's Vercel project ($($want.projectId))"
    }
    $cur = Get-Content $link -Raw | ConvertFrom-Json
    if ($cur.projectId -ne $want.projectId -or $cur.orgId -ne $want.orgId) {
        $deploy = "REFUSED (vercel_site is linked to project $($cur.projectId), not the dashboard's)"
    } else {
        # A token from Credential Manager when there is one (the Hobby team has ONE member, so a
        # second laptop cannot use its own login), else this laptop's own `vercel login`.
        # cmd.exe expands the token into the command line, so only a plain token may go there.
        $vtok = Get-VaultValue "vercel:token"
        if ($vtok -and ($vtok -notmatch '^[A-Za-z0-9_.\-]{20,300}$')) {
            $deploy = "REFUSED (the stored vercel:token is not a plain token - store it again with laptop_logins.py vercel)"
        } else {
            if ($vtok) { $env:SPB_VERCEL_TOKEN = $vtok; Say "deploying with the Vercel token from Credential Manager" }
            else { Say "deploying with this laptop's own Vercel login" }
            Push-Location $site
            try {
                cmd /c (Get-DeployCommand ([bool]$env:SPB_VERCEL_TOKEN) $Log)
                $deploy = if ($LASTEXITCODE -eq 0) { "ok" } else { "FAILED ($LASTEXITCODE)" }
            } finally {
                Pop-Location
                Remove-Item Env:SPB_VERCEL_TOKEN -ErrorAction SilentlyContinue
            }
        }
        Remove-Variable vtok
    }
    Say "deploy: $deploy"
}

# --- the login gate: stock.json must answer 401, /login must serve the form ------------------
$gate = "not checked"
if ($deploy -eq "ok") {
    $gate = Get-GateState
    Say "login gate: $gate"
}

# --- the day is done for every laptop: written only after a push AND a deploy ---------------
$marked = "not written"
if ($rc -eq 0 -and $pushed -and $deploy -eq "ok") {
    $gateOk = [int]($gate -like "up*")
    $marked = if ((Invoke-Py "`"$Helper`" --app `"$App`" day-mark --day $today --gate-ok $gateOk") -eq 0) { "written" } else { "FAILED" }
}
Remove-Item Env:SUPABASE_ACCESS_TOKEN -ErrorAction SilentlyContinue

$partial = if (Test-Path (Join-Path $App "_harvest_partial")) { (Get-Content (Join-Path $App "_harvest_partial")) -join "; " } else { "" }
$degraded = if (Test-Path (Join-Path $App "_run_degraded")) { (Get-Content (Join-Path $App "_run_degraded")) -join "; " } else { "" }
if ($partial) { Say "PARTIAL - channel(s) read nothing: $partial" }
if ($degraded) { Say "DEGRADED - step(s) failed: $degraded" }

[ordered]@{
    melbourne_day = $today; machine = $Machine; finished_local = (Get-Date -Format "yyyy-MM-dd HH:mm"); code = $head
    run_daily_exit = $rc; pushed = [bool]$pushed; deploy = $deploy; gate = $gate; day_marker = $marked
    partial = $partial; degraded = $degraded; log = $Log
} | ConvertTo-Json | Set-Content -Path $Status -Encoding UTF8

if ($rc -eq 0 -and $deploy -eq "ok" -and $gate -like "up*" -and $pushed) {
    Set-Content -Path $Done -Value $today -Encoding ASCII
    Say "finished OK"
    exit 0
}
Say "finished with a problem - see above"
exit 1
