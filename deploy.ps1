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

# The alias is the URL an operator should open; the per-deployment URL sits behind
# Vercel SSO and reads as broken. Confirm the alias actually serves the site rather than
# just printing it -- a failed alias move otherwise looks identical to a good publish.
Write-Host ""
try {
    $probe = Invoke-WebRequest -Uri "https://$Alias/stock.json" -Method Head -TimeoutSec 30 -UseBasicParsing
    if ($probe.StatusCode -eq 200) {
        Write-Host "Live: https://$Alias  ($rows listings, verified)" -ForegroundColor Green
    } else {
        Write-Host "[warn] https://$Alias answered $($probe.StatusCode) - check the alias." -ForegroundColor Yellow
        exit 1
    }
} catch {
    Write-Host "[warn] could not reach https://$Alias - the alias may not have moved." -ForegroundColor Yellow
    exit 1
}
