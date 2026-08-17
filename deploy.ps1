# Rebuild the static stock dashboard from the current database and deploy it.
#
#   PS> D:\Coleen\app\deploy.ps1
#
# Runs from anywhere: it locates the app by its own path, so you do not have to be in
# the right directory. Windows PowerShell 5.1 has no '&&', hence the explicit exit-code
# checks rather than a one-line chain.

param([switch]$Force)

$ErrorActionPreference = 'Continue'
$Scope = 'digitalx-solutions-projects'
$Alias = 'spb-live-stock.vercel.app'
# The URL to hand a human. Kept separate from $Alias because they are not the same
# thing: spb-live-stock is SSO-protected (see the verification block at the bottom),
# and for ten days this script printed it as the live link every single night.
#
# The custom domain, not vercelsite-three-psi: both are production aliases of the same
# deployment, but this is the one Coleen has bookmarked, so it is the one whose failure
# would actually be noticed by her rather than by nobody.
$PublicUrl = 'app.smartpropertybuying.com.au'

$app = $PSScriptRoot
Set-Location $app

Write-Host "[1/3] building vercel_site from the live database..."
python -X utf8 build_web.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[abort] build_web.py failed - nothing deployed." -ForegroundColor Red
    exit 1
}

$site = Join-Path $app 'vercel_site'
if (-not (Test-Path (Join-Path $site 'stock.json'))) {
    Write-Host "[abort] $site\stock.json missing - nothing deployed." -ForegroundColor Red
    exit 1
}

# A file that EXISTS is not a file with stock in it. Both gates above pass on an empty
# or badly truncated buildings table, which would publish a 0-listing dashboard straight
# over the last good one -- and the alias moves, so there is nothing left to fall back
# to. A bad migration, a failed supersede pass or a crash mid-write all land here.
#
# Two checks: an absolute floor, and a drop from the last successful publish. The count
# is kept in .last_deploy_rows beside the script. Override with -Force when a large drop
# is genuinely intended.
$rows = python -X utf8 -c "import json;print(len(json.load(open(r'$site\stock.json',encoding='utf-8'))['rows']))"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[abort] could not read stock.json - nothing deployed." -ForegroundColor Red
    exit 1
}
$rows = [int]$rows
$floorFile = Join-Path $app '.last_deploy_rows'
$previous = if (Test-Path $floorFile) { [int](Get-Content $floorFile -Raw).Trim() } else { 0 }

if ($rows -lt 500) {
    Write-Host "[abort] stock.json holds only $rows listing(s) - refusing to publish." -ForegroundColor Red
    Write-Host "        The last good publish had $previous. Use -Force to override." -ForegroundColor Red
    if (-not $Force) { exit 1 }
}
if ($previous -gt 0 -and $rows -lt ($previous * 0.8)) {
    $pct = [math]::Round((1 - $rows / $previous) * 100)
    Write-Host "[abort] stock.json dropped $pct% ($previous -> $rows) - refusing to publish." -ForegroundColor Red
    Write-Host "        Use -Force if the drop is intended." -ForegroundColor Red
    if (-not $Force) { exit 1 }
}
Write-Host "        $rows listing(s) (last publish: $previous)"

# IS THIS DATABASE THE CURRENT ONE?
#
# The nightly harvest runs on GitHub and pushes the database to Supabase; this script
# builds from the LOCAL copy. Those diverge the moment the laptop misses a pull, and
# nothing said so: on 17 Aug every deploy from this machine republished a 12 Aug database
# over the nightly's, quietly dropping 45 listings each time.
#
# It also makes the new Settings tab lie in the worst direction. That tab reports how
# recently each source sent stock, so publishing a stale copy paints EVERY source as
# "signed out" — crying wolf about a real alarm is how the alarm stops being read.
# Read it from the BUILT snapshot, not the database file: the build honours
# SPB_DATABASE_PATH, so a check against a hard-coded path would report on a database the
# build never opened. stock.json is definitionally what is about to be published.
$newest = python -X utf8 -c "import json;d=json.load(open(r'$site\stock.json',encoding='utf-8'));i=d['keys'].index('date_checked');s=d['keys'].index('superseded_by');v=[r[i] for r in d['rows'] if not r[s] and r[i]];print(max((x[6:10]+x[3:5]+x[0:2]) for x in v) if v else '')" 2>$null
if ($LASTEXITCODE -eq 0 -and $newest -match '^\d{8}$') {
    $newestDate = [datetime]::ParseExact($newest, 'yyyyMMdd', $null)
    $ageDays = [int]((Get-Date).Date - $newestDate.Date).TotalDays
    if ($ageDays -gt 2) {
        Write-Host "[warn] the newest stock in the LOCAL database is $ageDays days old ($($newestDate.ToString('dd/MM/yyyy')))." -ForegroundColor Yellow
        Write-Host "       The nightly harvest may have newer data in Supabase. Publishing this would" -ForegroundColor Yellow
        Write-Host "       overwrite it and make every source read as 'signed out' in Settings." -ForegroundColor Yellow
        Write-Host "       Refresh first:  python state_sync.py pull   (then re-run this script)" -ForegroundColor Yellow
        if (-not $Force) {
            Write-Host "[abort] use -Force to publish this copy anyway." -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "        newest stock: $($newestDate.ToString('dd/MM/yyyy')) ($ageDays day(s) old)"
    }
}

Set-Location $site

Write-Host "[2/3] deploying to Vercel production..."
$out = vercel deploy --prod --yes --scope $Scope 2>&1 | Out-String
Write-Host $out
if ($LASTEXITCODE -ne 0) {
    Write-Host "[abort] deploy failed." -ForegroundColor Red
    Set-Location $app
    exit 1
}

# 'vercel deploy --prod' mints a NEW deployment URL each time. The friendly alias was
# pinned to one deployment, so without re-pointing it the nice URL keeps serving the
# previous build.
$m = [regex]::Match($out, 'https://[a-z0-9\-]+\.vercel\.app')
if ($m.Success) {
    Write-Host "[3/3] pointing $Alias at $($m.Value)..."
    vercel alias set $m.Value $Alias --scope $Scope
} else {
    Write-Host "[warn] could not read the new deployment URL; alias NOT moved." -ForegroundColor Yellow
}

Set-Location $app
Set-Content -Path (Join-Path $app '.last_deploy_rows') -Value $rows -Encoding ascii

# Confirm the site is actually SERVED, not merely that something answered.
#
# WHAT THIS USED TO DO, AND WHY IT LIED FOR TEN DAYS. It sent a HEAD to
# https://spb-live-stock.vercel.app/stock.json and accepted StatusCode 200.
# Invoke-WebRequest FOLLOWS REDIRECTS by default, and that host answers
#
#     302 -> https://vercel.com/sso-api?url=... -> 200 (the Vercel login page)
#
# so the probe saw 200, and every nightly run printed "(11257 listings, verified)"
# while the URL served a login wall to anyone outside the Vercel team. Verified on
# 2026-08-08 by fetching it: Content-Type text/html, <title>Login - Vercel</title>,
# and /stock.json returning HTML instead of JSON.
#
# So the probe now (a) refuses to follow redirects, (b) rejects any hop to vercel.com,
# and (c) PARSES the payload and checks the listing count, because the only proof that
# a dashboard is being served is the dashboard's own data coming back.
Write-Host ""

function Invoke-Probe {
    # HttpWebRequest rather than Invoke-WebRequest: on PowerShell 5.1, -MaximumRedirection 0
    # raises InvalidOperationException ("Operation is not valid due to the current state of
    # the object") and hides the response, so the run reports "unreachable" for a host that
    # is answering perfectly well with a redirect to a login page. This way the status line
    # and the Location header are both readable, and the message names the real cause.
    param([string]$Url)
    try {
        $req = [System.Net.HttpWebRequest]::Create($Url)
        $req.AllowAutoRedirect = $false
        $req.Timeout = 30000
        $req.UserAgent = 'spb-deploy-verify'
        $resp = $req.GetResponse()
    } catch [System.Net.WebException] {
        if ($_.Exception.Response) {
            $r2 = $_.Exception.Response
            $b = (New-Object System.IO.StreamReader($r2.GetResponseStream())).ReadToEnd()
            return @{ code = [int]$r2.StatusCode; body = $b; location = $r2.Headers['Location'] }
        }
        return @{ code = 0; why = "unreachable: $($_.Exception.Message)" }
    } catch {
        return @{ code = 0; why = "unreachable: $($_.Exception.Message)" }
    }
    $b = (New-Object System.IO.StreamReader($resp.GetResponseStream())).ReadToEnd()
    $code = [int]$resp.StatusCode
    $loc = $resp.Headers['Location']
    $resp.Close()
    return @{ code = $code; body = $b; location = $loc }
}

# WHAT "WORKING" MEANS CHANGED ON 14 AUG, AND SO DID THIS CHECK.
#
# Until the login gate, proof of a good publish was /stock.json answering 200 with rows in
# it. That is now the DEFINITION OF FAILURE: the catalogue must not be readable without
# signing in. The old assertion would have failed every deploy from here on and read as
# "the deploy broke" -- or, far worse, would have been "fixed" by deleting it, leaving
# nothing watching whether the gate is actually up.
#
# So it is inverted rather than removed. Two things are asserted, and between them they
# distinguish all three states that matter:
#   /stock.json -> 401          the gate is up            (200 = CATALOGUE IS PUBLIC)
#   /login      -> 200 + form   the app is being served   (vercel.com/sso = SSO wall)
function Test-GateIsUp {
    param([string]$Host_)

    $stock = Invoke-Probe "https://$Host_/stock.json"
    if ($stock.code -eq 0) { return @{ ok = $false; why = $stock.why } }
    if ($stock.location -match 'vercel\.com/sso') {
        return @{ ok = $false; why = 'redirects to the Vercel SSO login (Deployment Protection is on)' }
    }
    if ($stock.code -eq 200) {
        return @{ ok = $false; ALARM = $true
                  why = 'stock.json is being served to ANYONE - the login gate is NOT running' }
    }
    if ($stock.code -eq 503 -and $stock.body -match 'SITE_PASSWORD is not set') {
        return @{ ok = $false; why = 'SITE_PASSWORD is not set on the Vercel project (site is refusing to serve)' }
    }
    if ($stock.code -ne 401) { return @{ ok = $false; why = "stock.json answered HTTP $($stock.code), expected 401" } }

    $login = Invoke-Probe "https://$Host_/login"
    if ($login.location -match 'vercel\.com/sso') {
        return @{ ok = $false; why = 'the login page redirects to the Vercel SSO wall' }
    }
    if ($login.code -ne 200) { return @{ ok = $false; why = "/login answered HTTP $($login.code), expected 200" } }
    if ($login.body -notmatch 'name="password"') { return @{ ok = $false; why = '/login served no sign-in form' } }

    return @{ ok = $true }
}

$pub = Test-GateIsUp $PublicUrl
$ali = Test-GateIsUp $Alias

if ($pub.ok) {
    Write-Host "Live: https://$PublicUrl  ($rows listings published, sign-in required)" -ForegroundColor Green
} else {
    if ($pub.ALARM) {
        Write-Host "[ALARM] https://$PublicUrl - $($pub.why)" -ForegroundColor Red
        Write-Host "        Check that build_web.py still writes middleware.js into vercel_site." -ForegroundColor Red
    } else {
        Write-Host "[abort] https://$PublicUrl is not serving correctly - $($pub.why)" -ForegroundColor Red
    }
    exit 1
}

if ($ali.ok) {
    Write-Host "Also:  https://$Alias  (sign-in required)" -ForegroundColor Green
} else {
    # Not fatal: the public URL above is verified and is the link to hand out. But it
    # must be SAID, every run, because a silent failure here is what hid this for ten days.
    Write-Host "[warn] https://$Alias is not serving correctly - $($ali.why)" -ForegroundColor Yellow
    Write-Host "       Do not give this link to the client; hand out the URL above." -ForegroundColor Yellow
}
