# The WhatsApp lead scan on this laptop, while GitHub Actions is refusing jobs (billing).
# Does what .github/workflows/whatsapp-scan.yml does every 15 minutes: POST the hook's
# /api/whatsapp-scan with HOOK_SECRET. The scan is read-only against Kommo and idempotent
# (a lead whose record exists and whose fields have not changed is skipped), so a doubled
# or skipped run costs nothing.
#
# OPTIONAL on a second laptop: one laptop scanning is enough, and two only double the calls.
#
# The key comes from Credential Manager (SPB-PropertyAgent / hook:secret, stored with
# laptop_logins.py hook), else from -SecretFile (default <Root>\hook_secret.txt), and is never
# written anywhere: the log carries the status code and the error count, not the URL.
#   whatsapp_scan.ps1 [-Root <runner folder>] [-SecretFile <file holding HOOK_SECRET>]
param([string]$Root = "", [string]$SecretFile = "")
$ErrorActionPreference = "Continue"
if (-not $Root) { $Root = $PSScriptRoot }
$Logs = Join-Path $Root "logs"
$Log = Join-Path $Logs "whatsapp-scan.log"
New-Item -ItemType Directory -Force $Logs | Out-Null
function Say($msg) { Add-Content -Path $Log -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg) -Encoding UTF8 }

$secret = ""
$py = Join-Path $Root "venv311\Scripts\python.exe"
if (Test-Path $py) {
    $secret = & $py -c "import keyring,sys; sys.stdout.write(keyring.get_password('SPB-PropertyAgent','hook:secret') or '')" 2>$null
}
if (-not "$secret".Trim()) {
    if (-not $SecretFile) { $SecretFile = Join-Path $Root "hook_secret.txt" }
    $secret = (Get-Content $SecretFile -Raw -ErrorAction SilentlyContinue)
}
if (-not $secret -or -not "$secret".Trim()) { Say "HOOK_SECRET not in Credential Manager (hook:secret) nor in $SecretFile - no scan"; exit 1 }
$url = "https://vapi-kommo-hook.vercel.app/api/whatsapp-scan?key=" + [uri]::EscapeDataString("$secret".Trim())
Remove-Variable secret

$code = 0; $body = ""
for ($try = 1; $try -le 3; $try++) {
    try {
        $r = Invoke-WebRequest -Uri $url -Method Post -ContentType "application/json" `
             -Body '{"source":"laptop"}' -UseBasicParsing -TimeoutSec 120 -ErrorAction Stop
        $code = $r.StatusCode; $body = $r.Content; break
    } catch {
        $resp = $_.Exception.Response
        if ($resp) { $code = [int]$resp.StatusCode; break }
        $code = 0; Start-Sleep -Seconds 10
    }
}

if ($code -ge 200 -and $code -lt 300) {
    try { $j = $body | ConvertFrom-Json } catch { $j = $null }
    if (-not $j) { Say "POST /api/whatsapp-scan -> $code but the body is not JSON"; exit 1 }
    $n = @($j.errors).Where({ $_ }).Count
    $summary = ($j.PSObject.Properties | Where-Object { $_.Name -ne "errors" -and $_.Value -isnot [array] -and $_.Value -isnot [psobject] } |
                ForEach-Object { "$($_.Name)=$($_.Value)" }) -join " "
    if ($n -gt 0) {
        Say "POST /api/whatsapp-scan -> $code, $n lead(s) failed: $((@($j.errors) | Select-Object -First 3) -join ' | ')"
        exit 1
    }
    Say "POST /api/whatsapp-scan -> $code ok $summary"
    exit 0
}
$hint = switch ($code) {
    0 { "could not reach the hook (DNS, TLS or timeout) after 3 tries" }
    401 { "the hook refused the key - the stored key and the hook's HOOK_SECRET differ" }
    404 { "no scan endpoint at /api/whatsapp-scan" }
    500 { "the hook's own environment is probably incomplete" }
    default { "unexpected answer" }
}
Say "POST /api/whatsapp-scan -> $code - $hint"
exit 1
