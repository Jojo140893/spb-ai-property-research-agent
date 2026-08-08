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
$PublicUrl = 'vercelsite-three-psi.vercel.app'

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

function Test-ServesDashboard {
    param([string]$Host_)
    # HttpWebRequest rather than Invoke-WebRequest: on PowerShell 5.1, -MaximumRedirection 0
    # raises InvalidOperationException ("Operation is not valid due to the current state of
    # the object") and hides the response, so the run reports "unreachable" for a host that
    # is answering perfectly well with a redirect to a login page. This way the status line
    # and the Location header are both readable, and the message names the real cause.
    $u = "https://$Host_/stock.json"
    try {
        $req = [System.Net.HttpWebRequest]::Create($u)
        $req.AllowAutoRedirect = $false
        $req.Timeout = 30000
        $req.UserAgent = 'spb-deploy-verify'
        $resp = $req.GetResponse()
    } catch [System.Net.WebException] {
        if ($_.Exception.Response) {
            $r2 = $_.Exception.Response
            return @{ ok = $false; why = "HTTP $([int]$r2.StatusCode)" }
        }
        return @{ ok = $false; why = "unreachable: $($_.Exception.Message)" }
    } catch {
        return @{ ok = $false; why = "unreachable: $($_.Exception.Message)" }
    }

    $code = [int]$resp.StatusCode
    if ($code -in 301, 302, 303, 307, 308) {
        $loc = $resp.Headers['Location']
        $resp.Close()
        $where = if ($loc -match 'vercel\.com/sso') { 'the Vercel SSO login (Deployment Protection is on)' } else { $loc }
        return @{ ok = $false; why = "redirects ($code) to $where" }
    }
    $body = (New-Object System.IO.StreamReader($resp.GetResponseStream())).ReadToEnd()
    $resp.Close()
    if ($code -ne 200) { return @{ ok = $false; why = "HTTP $code" } }
    if ($body -match '^\s*<') { return @{ ok = $false; why = 'served HTML, not JSON (a login wall answers 200)' } }
    try { $j = $body | ConvertFrom-Json } catch { return @{ ok = $false; why = 'body is not valid JSON' } }
    if (-not $j.rows) { return @{ ok = $false; why = 'JSON carries no rows' } }
    return @{ ok = $true; count = $j.rows.Count }
}

$pub = Test-ServesDashboard $PublicUrl
$ali = Test-ServesDashboard $Alias

if ($pub.ok) {
    Write-Host "Live: https://$PublicUrl  ($($pub.count) listings, content verified)" -ForegroundColor Green
} else {
    Write-Host "[abort] https://$PublicUrl does not serve the dashboard - $($pub.why)" -ForegroundColor Red
    exit 1
}

if ($ali.ok) {
    Write-Host "Also:  https://$Alias  ($($ali.count) listings)" -ForegroundColor Green
} else {
    # Not fatal: the public URL above is verified and is the link to hand out. But it
    # must be SAID, every run, because a silent failure here is what hid this for ten days.
    Write-Host "[warn] https://$Alias does NOT serve the dashboard - $($ali.why)" -ForegroundColor Yellow
    Write-Host "       Do not give this link to the client. Turn off Deployment Protection" -ForegroundColor Yellow
    Write-Host "       for that domain in the Vercel project settings, or hand out the URL above." -ForegroundColor Yellow
}
