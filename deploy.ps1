# Rebuild the static stock dashboard from the current database and deploy it.
#
#   PS> D:\Coleen\app\deploy.ps1
#
# Runs from anywhere: it locates the app by its own path, so you do not have to be in
# the right directory. Windows PowerShell 5.1 has no '&&', hence the explicit exit-code
# checks rather than a one-line chain.

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
Write-Host ""
Write-Host "Live: https://$Alias" -ForegroundColor Green
