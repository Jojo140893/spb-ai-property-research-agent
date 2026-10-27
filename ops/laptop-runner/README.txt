SPB automations on a Windows laptop -- a stand-in while GitHub Actions refuses jobs
(billing failed: 28 Sep - 1 Oct 2026, and again from 24 Oct 2026 ~13:00 UTC).
First set up on Ahsan's laptop 30 Sep 2026; made portable 26 Oct 2026 so a second laptop
can be the backup. SETUP.md says how to set a laptop up. This file says what is where.

WHAT RUNS
  SPB Nightly (laptop)        Task Scheduler. ONE harvest per Melbourne day across all
                              laptops, from 09:00 Melbourne: pull the database from Supabase
                              -> run_daily.py -> push it back -> vercel deploy --prod -> check
                              the login gate -> mark the day done in the bucket
                              (laptop-runs/<day>.json). Primary laptop 03:00 and 04:00
                              Pakistan time; backup laptop 06:00 and 08:00.
                              Before starting it stands down when:
                                - this laptop already finished the day   (logs\last_success.txt)
                                - another laptop finished it              (the bucket's day marker)
                                - GitHub's daily-harvest published it     ("Publish to Vercel")
                                - a harvest is running somewhere          (the bucket lock, < 8 h old)
                              and REFUSES to start when this laptop's date is not Melbourne's,
                              or less than 6 h remain before either midnight (on Pakistan
                              time: start between 00:00 and 12:00 while Melbourne is on AEDT,
                              00:00 and 13:00 on AEST). Runs only while the user is logged on:
                              Credential Manager is locked otherwise.
  SPB WhatsApp scan (laptop)  optional, every 15 minutes: POST the hook's /api/whatsapp-scan
                              (read-only against Kommo). One laptop running it is enough.

WHERE (the runner folder, e.g. C:\SPB-runner, or D:\Coleen\nightly on Ahsan's laptop)
  nightly.ps1                 the nightly (-Check = dry check, -SelfTest, -Now, -Force, -IgnoreClock)
  runner_state.py             reads the bucket's day marker and lock, writes the marker,
                              checks the venv (env-check)
  laptop_logins.py            puts the logins into Credential Manager (the owner runs it)
  install.ps1                 sets the folder up / brings it up to date
  register_task.ps1           registers the scheduled task(s); -Disable switches them off
  whatsapp_scan.ps1           the WhatsApp scan
  app\                        a clone of the repo, detached at origin/main, refreshed each run.
                              Nobody edits it. It holds the database, the portal sessions
                              (.sessions\) and the builder list WITH builder portal passwords
                              (drive_input\vendors.csv) -- this folder is sensitive.
  venv311\                    Python 3.11 + requirements.txt + rapidocr-onnxruntime 1.4.4 +
                              tzdata + Chromium. NOT Python 3.13, NOT the Microsoft Store
                              Python: there the OCR library stops at 1.2.3 and reads the
                              picture price lists into different rows.
  logs\last_run.json          the last run in one place: exit code, push, deploy, gate, day
                              marker, PARTIAL / DEGRADED
  logs\nightly-*.log          the full run;  logs\check-*.log  the dry checks
  logs\whatsapp-scan.log      one line per scan
  session-backups\pre-pull-*  the local sessions set aside before each pull
  skip-proxima.flag           (optional) while it exists, Proxima is not signed in to at all

LOGINS (Credential Manager, service SPB-PropertyAgent; laptop_logins.py)
  supabase:access_token   REQUIRED unless the laptop's own `supabase login` can see the SPB
                          project (dvivicgzphyxtxpkgvjn). Set for the run only, never logged.
  vercel:token            REQUIRED unless the laptop's own `vercel login` can deploy. The team
                          digitalx-solutions-projects is on Vercel Hobby (one member), so a
                          second laptop uses a team-scoped token. Passed as --token through
                          an environment variable that cmd.exe expands: never in a log.
  e_agent:*               REQUIRED. Without it the run stops before harvesting.
  portal_proxima:*        optional. Used only when the bucket's shared session has expired.
  hook:secret             optional, for the WhatsApp scan.
  email_inbox:*           NOT SET, on purpose: sources/email_inbox.py does not check Google's
                          certificate until PR #42 merges (then flip INBOX_ENABLED in
                          laptop_logins.py). The inbox reads nothing on a laptop until then.

BY HAND (Windows PowerShell)
  Dry check:    powershell -NoProfile -ExecutionPolicy Bypass -File <Root>\nightly.ps1 -Check
  Run now:      powershell -NoProfile -ExecutionPolicy Bypass -File <Root>\nightly.ps1 -Now
  Run again:    ... nightly.ps1 -Force   (even if the day is done; a second harvest the same
                day is safe: it starts from the database the first one pushed). -Force still
                keeps the clock rule; only -IgnoreClock overrides that, and dates rows wrong.

TURN IT OFF when GitHub billing is fixed
  The nightly already stands down on a day GitHub reached "Publish to Vercel", but switch it off:
      powershell -NoProfile -ExecutionPolicy Bypass -File <Root>\register_task.ps1 -Disable
  Do NOT use the old "SPB Daily Harvest" task (D:\Coleen\app, no Supabase sync): it would
  publish the laptop's own stale database.
