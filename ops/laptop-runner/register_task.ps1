# Registers the laptop runner's scheduled task for the user who is logged on (SETUP.md step 6).
#
#   register_task.ps1 -Role primary          03:00 and 04:00 on this laptop's clock (Pakistan time)
#   register_task.ps1 -Role backup           06:00 and 08:00: harvests only on a day the primary missed
#   register_task.ps1 -Role backup -At 07:00,09:00    other times, on this laptop's clock
#   register_task.ps1 -WhatsApp              also the WhatsApp scan every 15 min (ONE laptop is enough)
#   register_task.ps1 -Disable               switch the runner's tasks off (GitHub billing fixed)
#   register_task.ps1 -DryRun                say what would be registered, register nothing
#
# Why two times: nightly.ps1 runs at most once per Melbourne day, from 09:00 Melbourne, so the
# second trigger is a retry for a run that could not start (Wi-Fi down, Supabase busy). In AEST
# (April - October) 03:00 Pakistan is 08:00 Melbourne and that trigger just exits: the 04:00
# one runs. The backup's 06:00 / 08:00 come after the primary's run has usually finished and
# marked the day; if the primary is still harvesting, the backup sees its lock and stands down.
#
# The task: Interactive (only while this user is logged on: Credential Manager is locked
# otherwise), runs on battery, starts as soon as it can after a missed time (StartWhenAvailable),
# wakes the laptop from sleep (WakeToRun: needs "Allow wake timers" on in the power plan), one
# instance at a time, stopped after 7 h (a run takes 1.5 - 4.5 h; the bucket lock is treated as
# abandoned only after 8 h, so a killed run's lock is broken by the next run, never a live one's).
param(
    [ValidateSet("primary", "backup")][string]$Role = "backup",
    [string[]]$At = @(),
    [string]$Root = "",
    [switch]$WhatsApp,
    [switch]$Disable,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$Nightly = "SPB Nightly (laptop)"
$Scan = "SPB WhatsApp scan (laptop)"
if (-not $Root) { $Root = $PSScriptRoot }
$Root = [System.IO.Path]::GetFullPath($Root)

if ($Disable) {
    foreach ($t in $Nightly, $Scan) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            if ($DryRun) { Write-Host "would disable: $t" } else { Disable-ScheduledTask -TaskName $t | Out-Null; Write-Host "disabled: $t" }
        }
    }
    exit 0
}

if (-not (Test-Path (Join-Path $Root "nightly.ps1"))) { throw "no nightly.ps1 in $Root - pass -Root <runner folder>" }
if ($Root -match "\s") { throw "the runner folder must not contain spaces: $Root" }
# Through `powershell -File`, "-At 07:00,09:00" arrives as ONE string: split it here.
$At = @($At | ForEach-Object { "$_" -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if (-not $At) { $At = if ($Role -eq "primary") { @("03:00", "04:00") } else { @("06:00", "08:00") } }

# What each time means in Melbourne, today and in the other half of the year.
$mel = [System.TimeZoneInfo]::FindSystemTimeZoneById("AUS Eastern Standard Time")
foreach ($t in $At) {
    $local = [datetime]::ParseExact($t, "HH:mm", [System.Globalization.CultureInfo]::InvariantCulture)
    $notes = foreach ($probe in (Get-Date -Month 1 -Day 15), (Get-Date -Month 7 -Day 15)) {
        $when = $probe.Date.Add($local.TimeOfDay)
        $m = [System.TimeZoneInfo]::ConvertTime($when, $mel)
        $left = [math]::Min(24 - $m.TimeOfDay.TotalHours, 24 - $when.TimeOfDay.TotalHours)
        $state = if ($m.Date -ne $when.Date) { "REFUSED: the dates differ" }
                 elseif ($m.Hour -lt 9) { "waits: before 09:00 Melbourne" }
                 elseif ($left -lt 6) { "REFUSED: under 6 h before midnight" }
                 else { "runs" }
        "{0} Melbourne in {1} ({2})" -f $m.ToString("HH:mm"), $(if ($probe.Month -eq 1) { "AEDT" } else { "AEST" }), $state
    }
    Write-Host ("  {0} here = {1}" -f $t, ($notes -join "; "))
}

$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 7)
$triggers = @($At | ForEach-Object { New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($_, "HH:mm", [System.Globalization.CultureInfo]::InvariantCulture)) })
$action = New-ScheduledTaskAction -Execute "conhost.exe" -WorkingDirectory $Root `
          -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Root\nightly.ps1"
$desc = "SPB nightly harvest ($Role laptop) while GitHub Actions is blocked. $Root\README.txt"

if ($DryRun) {
    Write-Host "would register '$Nightly' for $user at $($At -join ', '): conhost.exe $($action.Arguments)"
} else {
    Register-ScheduledTask -TaskName $Nightly -Action $action -Trigger $triggers -Settings $settings `
        -Principal $principal -Description $desc -Force | Out-Null
    Write-Host "registered '$Nightly' for $user at $($At -join ', ') (this laptop's clock)"
}

if ($WhatsApp) {
    $scanTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 15)
    $scanSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
                    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
    $scanAction = New-ScheduledTaskAction -Execute "conhost.exe" -WorkingDirectory $Root `
                  -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Root\whatsapp_scan.ps1 -Root $Root"
    if ($DryRun) { Write-Host "would register '$Scan' every 15 min: conhost.exe $($scanAction.Arguments)" }
    else {
        Register-ScheduledTask -TaskName $Scan -Action $scanAction -Trigger $scanTrigger -Settings $scanSettings `
            -Principal $principal -Description "SPB WhatsApp lead scan while GitHub Actions is blocked. $Root\README.txt" -Force | Out-Null
        Write-Host "registered '$Scan' every 15 minutes"
    }
}
