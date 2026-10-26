# Setting up a second laptop for the SPB nightly

The SPB nightly harvest normally runs on GitHub Actions (`.github/workflows/daily-harvest.yml`).
Since 24 Oct 2026 GitHub has refused every job over billing, so the harvest runs on Ahsan's
laptop instead. This guide sets up a **second laptop as a backup** (Jazib's). It harvests only on
a Melbourne day that Ahsan's laptop missed.

The runner is the folder this file sits in (`ops/laptop-runner/`). `README.txt` says what is
where once it is installed.

## How the two laptops share one harvest a day

| | Primary (Ahsan) | Backup (Jazib) |
|---|---|---|
| Scheduled at (Pakistan time) | 03:00 and 04:00 | 06:00 and 08:00 |
| In Melbourne (AEDT, Oct to Apr) | 09:00 and 10:00 | 12:00 and 14:00 |

- After a run has **pushed** the database and **deployed** the dashboard, it writes a marker
  for that Melbourne day in the Supabase bucket: `ss:///spb-state/laptop-runs/<day>.json`. The
  marker records the machine, when it finished and the code commit. Every run reads the marker
  before it starts and stands down if any laptop has already done the day.
- **A run still in progress on the other laptop** has not written its marker yet. The bucket
  lock (`harvest.lock.json`) stops a second run in that case. The backup reads the lock first:
  if it is under 8 hours old, the backup logs `a harvest is running on another machine ...
  standing down` and exits cleanly. Its next trigger checks again, and by then the primary has
  usually finished and marked the day. If the backup reached the pull anyway, `state_sync.py`
  would refuse it (`!! locked by ...`) and nothing would be harmed.
- **If the primary dies mid-run** (laptop shut down), its lock stays in the bucket. Laptops treat
  a lock as abandoned only after 8 hours (`SPB_LOCK_STALE_HOURS=8`; GitHub's runner uses 4), so
  they never break the lock of a run that is merely slow (26 Oct took 4 h 15 min). That day the
  backup stands down at 06:00 and 08:00. To harvest anyway, run `nightly.ps1 -Now` on the
  backup between 11:00 and 12:00 Pakistan time, once the lock is 8 hours old.
- Each run also stands down on a day when **GitHub's own daily-harvest reached "Publish to
  Vercel"**, so both laptops go quiet by themselves once billing is fixed.

### The laptop clock

These laptops run on Pakistan time (UTC+5). Melbourne is UTC+11 on AEDT (4 Oct 2026 to
4 Apr 2027) and UTC+10 on AEST. The harvest dates its rows with **the laptop's own date**. A run
that starts between 18:00 and 23:59 Pakistan time would therefore date every row a day early,
and a run that crosses midnight would use two different dates. So `nightly.ps1` **refuses to
start** in either of these cases:

- the laptop's date is not Melbourne's date, or
- fewer than 6 hours remain before either midnight. A run takes up to about 4.5 hours.

On Pakistan time, that means a run may start between **00:00 and 12:00** (AEDT) or between
**00:00 and 13:00** (AEST). The refusal message says this, and `-Force` overrides it. The
script never changes the machine's time zone, and you should not change it either.

---

## Step 0. Before Jazib starts (Ahsan)

1. **Merge the `laptop-runner` branch into `main`.** The runner checks out `origin/main` every
   night, so the runner files and the builder-list fix in `state_sync.py` only take effect once
   they are on `main`.
2. **Move the primary laptop to this runner as well.** The 25 Oct runner in `D:\Coleen\nightly`
   neither writes nor reads the day marker. Until it is replaced, the backup cannot tell that
   the primary already ran, and both laptops would harvest every day. From a checkout of the
   merged `main`, run in Windows PowerShell:

   ```powershell
   Copy-Item -Force D:\Coleen\nightly\nightly.ps1 D:\Coleen\nightly\nightly.ps1.25oct
   foreach ($f in "nightly.ps1","runner_state.py","laptop_logins.py","register_task.ps1","install.ps1","README.txt","SETUP.md") { Copy-Item -Force "D:\Coleen\nightly\app\ops\laptop-runner\$f" "D:\Coleen\nightly\$f" }
   powershell -NoProfile -ExecutionPolicy Bypass -File D:\Coleen\nightly\nightly.ps1 -Check
   powershell -NoProfile -ExecutionPolicy Bypass -File D:\Coleen\nightly\register_task.ps1 -Role primary
   ```

   Run the copy after a nightly has refreshed `D:\Coleen\nightly\app` to the merged `main`, or
   first run `git -C D:\Coleen\nightly\app fetch origin` and then
   `git -C D:\Coleen\nightly\app checkout --detach --force origin/main`.

   The new `nightly.ps1` finds `app\`, `venv311\`, `logs\` and `skip-proxima.flag` in
   `D:\Coleen\nightly` by itself. Leave `whatsapp_scan.ps1` there as it is: the old one reads
   `D:\Coleen\make-tools\hook_secret.txt`. Re-registering also raises the task's time limit
   from 3 h to 7 h. A 3 h limit can kill a 4 h run before its push, which leaves the lock held.
3. **Give Jazib the access in the next section.**

## What Jazib needs: access and logins

| What | Why the runner needs it | Who provides it | Where it is kept on Jazib's laptop |
|---|---|---|---|
| **A GitHub account with read access** to `Jojo140893/spb-ai-property-research-agent` | Cloning and fetching the code each night. Reading Actions runs for the "GitHub already published today" check. | The repo owner (Jojo140893) adds Jazib as a collaborator. Read access is enough. | `gh auth login` (gh's own store) plus `gh auth setup-git` |
| **A Supabase access token** whose account can see project `dvivicgzphyxtxpkgvjn` | Pulling and pushing the shared database, sessions and builder list in bucket `spb-state`. Reading and writing the day marker and the lock. | Best: the org owner invites Jazib to the Supabase organisation, and Jazib creates his own token (Account > Access Tokens, named "SPB Jazib laptop"). This way it can be revoked on its own. Otherwise the owner creates the token and hands it over privately. | Credential Manager `SPB-PropertyAgent / supabase:access_token` |
| **A Vercel token** scoped to team `digitalx-solutions-projects` | Deploying the dashboard after the harvest. The team is on the **Hobby plan, which allows one member**, so Jazib cannot be invited. A Pro team would allow it. | The team's owner creates it at vercel.com/account/tokens: scope `digitalx-solutions-projects`, named "SPB Jazib laptop", with an expiry such as 90 days. | Credential Manager `vercel:token` |
| **The E-Agent username and password** (required) | Harvesting E-Agent. Without them the run stops before harvesting. | Ahsan or Coleen. It is the same login as GitHub's `E_AGENT_*` secrets. | Credential Manager `e_agent:*` |
| Proxima login: **leave it out** | Only used when the shared Proxima session in the bucket has expired. Each refused sign-in counts against the account. | | |
| digital@ inbox: **not on laptops yet** | `sources/email_inbox.py` does not verify Gmail's certificate until PR #42 merges. | | |
| WhatsApp scan key: **not needed** | Ahsan's laptop already runs the scan. Two scanners only double the calls. | | |

Jazib types every login into hidden prompts himself (step 4). **No one pastes a token into
chat, a file or an AI agent.**

The runner also puts sensitive data on Jazib's disk, in `C:\SPB-runner\app`:

- the database
- the portal sessions (`.sessions\`)
- the builder list, which includes the **builder portal passwords** (`drive_input\vendors.csv`)

His Windows account needs a password, and device encryption or BitLocker should be on.

The Supabase token gives full access to every project that account can see. The Vercel token
can deploy any project in the team. Revoke both when the backup is retired (step 10).

---

## The prompt to paste into Google Antigravity

Give Antigravity's agent this prompt on Jazib's laptop, after Step 0 and the access above are
in place. The agent installs the runner and stops wherever a human has to sign in or type a
login.

```text
You are setting up the "SPB nightly" BACKUP runner on this Windows laptop. The full guide is
ops/laptop-runner/SETUP.md in the GitHub repository Jojo140893/spb-ai-property-research-agent
(branch main). Follow its steps 1 to 6, in order, and nothing else.

Hard rules:
1. Never ask for, type, read, print, paste or store any password, token or key. When a step
   needs a sign-in or a login (gh auth login in step 2, laptop_logins.py in step 4), STOP, show
   me the exact command to run myself in my own PowerShell window, and wait until I say "done".
2. Use Windows PowerShell 5.1 (powershell.exe), not PowerShell 7 and not Git Bash. It has no
   && or ||: run commands one at a time, or join them with ";" and check $LASTEXITCODE.
   Script execution is disabled on this machine: call npm.cmd and vercel.cmd (never npm or
   vercel), and run every .ps1 only as
       powershell -NoProfile -ExecutionPolicy Bypass -File <path> <arguments>
   Never change the execution policy, the time zone, the power plan or any other system setting.
3. Python: only python.org Python 3.11 (winget id Python.Python.3.11). Never 3.12 or 3.13,
   never the Microsoft Store Python.
4. The only runner commands you may run are: install.ps1, nightly.ps1 -SelfTest,
   nightly.ps1 -Check, and register_task.ps1 -Role backup. Never run nightly.ps1 without -Check
   or -SelfTest, and never run run_daily.py, state_sync.py, portal_login.py, vercel deploy,
   or any "supabase storage" command yourself. They change the shared database or the live
   dashboard.
5. Install everything under C:\SPB-runner. Do not create or edit any other project files.
6. If any command fails or the dry check prints FAIL, stop and show me the output. Do not
   work around a failed check, and do not retry with -Force.

Steps:
A. Step 1 of SETUP.md: install Git, GitHub CLI, Node.js LTS, Python 3.11, the Vercel CLI and
   the Supabase CLI with the commands given there. Then open a new PowerShell window and show
   me the version check output.
B. Step 2: ask me to run "gh auth login" and then "gh auth setup-git" myself, and wait. Then run
   the access check from step 2 and show me its output.
C. Step 3: clone the repository to C:\SPB-runner\app and run install.ps1 -Root C:\SPB-runner.
   Show me the last 20 lines of its output.
D. Step 4: tell me to run the logins command myself, and wait until I say "done".
E. Step 5: run nightly.ps1 -SelfTest, then nightly.ps1 -Check. Show me every line that starts
   with FAIL or WARN, and the final DRY CHECK line.
F. Only if the dry check printed "DRY CHECK PASSED": step 6, register the scheduled task with
   register_task.ps1 -Role backup, and show me its output.
G. Finish with a short report: each tool's version, the dry check result, and the registered
   task's start times.
```

---

## Step 1. Install the tools

Run these in Windows PowerShell on Jazib's laptop:

```powershell
winget install -e --id Git.Git
winget install -e --id GitHub.cli
winget install -e --id OpenJS.NodeJS.LTS
winget install -e --id Python.Python.3.11
```

Close PowerShell and open a new window, so the new PATH applies. Then install the Vercel CLI
and the Supabase CLI. The Supabase CLI comes from its GitHub release, pinned to the version
GitHub's runner uses:

```powershell
npm.cmd install -g vercel
New-Item -ItemType Directory -Force "$env:USERPROFILE\bin" | Out-Null
New-Item -ItemType Directory -Force "$env:TEMP\supabase-cli" | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/supabase/cli/releases/download/v2.113.0/supabase_windows_amd64.tar.gz" -OutFile "$env:TEMP\supabase-cli\supabase.tar.gz"
tar.exe -xzf "$env:TEMP\supabase-cli\supabase.tar.gz" -C "$env:TEMP\supabase-cli"
Copy-Item -Force "$env:TEMP\supabase-cli\supabase.exe" "$env:USERPROFILE\bin\supabase.exe"
$p = [Environment]::GetEnvironmentVariable("Path", "User"); if ($p -notlike "*$env:USERPROFILE\bin*") { [Environment]::SetEnvironmentVariable("Path", "$p;$env:USERPROFILE\bin", "User") }
```

Open a new window again and check the versions:

```powershell
git --version; gh --version; node --version; vercel.cmd --version; supabase --version; py -3.11 --version
```

Python must report 3.11.x. Do not use Python 3.13: there the OCR library stops at 1.2.3, which
reads the picture price lists into different rows.

## Step 2. Sign in to GitHub (Jazib types this himself)

```powershell
gh auth login
gh auth setup-git
```

In `gh auth login`, choose GitHub.com, then HTTPS, then "Login with a web browser". Then check
that the account can see the repository and its Actions runs:

```powershell
gh repo view Jojo140893/spb-ai-property-research-agent --json name
gh run list --repo Jojo140893/spb-ai-property-research-agent --workflow daily-harvest.yml --limit 1
```

## Step 3. Install the runner

```powershell
git clone https://github.com/Jojo140893/spb-ai-property-research-agent.git C:\SPB-runner\app
powershell -NoProfile -ExecutionPolicy Bypass -File C:\SPB-runner\app\ops\laptop-runner\install.ps1 -Root C:\SPB-runner
```

`install.ps1` does the following:

1. Checks the tools.
2. Finds the python.org Python 3.11.
3. Detaches `app` at `origin/main`.
4. Builds `C:\SPB-runner\venv311`: requirements, `rapidocr-onnxruntime==1.4.4`, `tzdata` and
   Playwright's Chromium.
5. Copies the runner files into `C:\SPB-runner`.
6. Proves the venv works.

It ends with `INSTALLED`. It can be run again at any time.

## Step 4. The logins (Jazib types these himself)

```powershell
C:\SPB-runner\venv311\Scripts\python.exe C:\SPB-runner\laptop_logins.py supabase vercel e_agent
```

Every prompt is hidden. Each token is checked read-only before it is stored, and a rejected
token is not stored. The script ends with what is now stored and with "Ready" or "Not ready".
It never prints a value. To see the state again later, with no prompts, add `--status`.

## Step 5. The dry check (changes nothing shared)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\SPB-runner\nightly.ps1 -SelfTest
powershell -NoProfile -ExecutionPolicy Bypass -File C:\SPB-runner\nightly.ps1 -Check
```

`-SelfTest` checks the clock rule offline, and that the deploy command never contains the
Vercel token. It ends with `all self-tests passed`.

`-Check` proves each of the following, and prints `PASS`, `FAIL`, `WARN` or `INFO` for each:

| Check | What has to work |
|---|---|
| clock | Shows when a run may start on this laptop. |
| Python venv | Python 3.11 from python.org, `rapidocr-onnxruntime` 1.4.4, Melbourne time zone data, Credential Manager, and Chromium starting. |
| logins | The Supabase token (or the CLI's own login) can see the SPB project. The Vercel token (or login) can see `digitalx-solutions-projects/vercel_site`. The E-Agent login is stored. |
| Supabase | `app` is linked to the project (this writes a local file only), and bucket `spb-state` and its database are readable. It also reports today's day marker and the lock. |
| git and gh | The code can be fetched, and GitHub's daily-harvest runs can be read. |
| disk | At least 5 GB free. |
| task | Whether the scheduled task is registered. |

The check never takes the lock, writes to the bucket, pulls the database, harvests or deploys.
It ends with `DRY CHECK PASSED`, or with a count of problems. Its log is
`C:\SPB-runner\logs\check-*.log`.

| A FAIL line says | Fix |
|---|---|
| `Supabase: ... cannot see the SPB project` | The token belongs to the wrong account, or Jazib is not in the org. Redo step 4 with `supabase`. |
| `Vercel: ... cannot see the team` | The token is personal-scope. Make it again with the team scope, then rerun `laptop_logins.py vercel`. |
| `git: cannot reach` / `gh: not signed in` | Repeat step 2. Check that Jazib has been added to the repository. |
| `rapidocr-onnxruntime ...` / `Python ...` | The venv was not built on python.org 3.11. Delete `C:\SPB-runner\venv311` and run `install.ps1 -Python <path to python.exe>`. |

## Step 6. Register the scheduled task

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\SPB-runner\register_task.ps1 -Role backup
```

This registers "SPB Nightly (laptop)" for the logged-on user at **06:00 and 08:00**, on the
laptop's clock. It prints what each time is in Melbourne. The task has these settings:

- **Interactive**: it runs only while Jazib is logged on, because Credential Manager is locked
  otherwise. A locked screen is fine; signing out is not.
- Runs on battery.
- **StartWhenAvailable**: a time missed while the laptop was off runs as soon as it is on.
- **WakeToRun**.
- One instance at a time.
- A 7 h limit.

Other start times can be given with `-At 07:00,09:00`. Keep them between 00:00 and 12:00
Pakistan time; the script prints a warning for any time that would be refused. To let the task
wake the laptop from sleep, turn on "Allow wake timers" in the power plan (Control Panel >
Power Options > Change advanced power settings > Sleep). Do the same for "When plugged in"
and, if wanted, "On battery".

## Step 7. The first real run

Agree it with Ahsan first, then run it between 00:00 and 12:00 Pakistan time:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\SPB-runner\nightly.ps1 -Now
```

If the primary has already done the day, the run logs
`another run already finished Melbourne day ...` and stops. That is the shared marker working.
To harvest anyway, use `-Force` instead of `-Now`. A second harvest on the same day is safe:
it starts from the database the first run pushed, as a manual GitHub re-run would. A full run
takes 1.5 to 4.5 hours. Leave the laptop on and logged in.

## Step 8. Reading the result

```powershell
Get-Content C:\SPB-runner\logs\last_run.json
Get-ChildItem C:\SPB-runner\logs\nightly-*.log | Sort-Object LastWriteTime | Select-Object -Last 1 | Get-Content -Tail 40
Get-ScheduledTaskInfo -TaskName "SPB Nightly (laptop)"
```

A good run has these values in `last_run.json`: `run_daily_exit: 0`, `pushed: true`,
`deploy: "ok"`, `gate: "up (stock.json 401)"` and `day_marker: "written"`. Its log ends
`finished OK`. What other last lines mean:

| Last line of the log | Meaning |
|---|---|
| `another run already finished Melbourne day ...` / `GitHub's daily-harvest already published today` | Nothing to do. Correct for the backup on most days. |
| `a harvest is running on another machine ... standing down` / `the pull was refused: another run holds the bucket lock` | The primary is still running. The 08:00 trigger checks again. |
| `[REFUSED] not starting: ...` | It was started outside the clock window. The message says when to start it. |
| `[ABORT] ...` | A login or tool is missing. Run `nightly.ps1 -Check`. |
| `PARTIAL - channel(s) read nothing` | A source read 0 rows (often an expired portal session). The data still published. |
| `DEGRADED - step(s) failed` | An optional data step failed. The data still published. |
| `!! no 'lock released' line` | The push did not finish and the lock may still be held. Tell Ahsan. |
| `login gate: ALARM ...` | The catalogue is public. Tell Ahsan at once. |

A scheduled trigger that finds nothing to do (before 09:00 Melbourne, or the day already done
on this laptop) exits silently and writes no log.

## Step 9. Updating the runner

Every run refreshes `app` to `origin/main`, but the runner scripts in `C:\SPB-runner` are a
copy. When `main` changes them, the log says
`note: ops/laptop-runner/<file> on main differs from this runner's copy`. To update the copy:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\SPB-runner\app\ops\laptop-runner\install.ps1 -Root C:\SPB-runner
```

## Step 10. Turning it off (when GitHub billing is fixed)

Runs already stand down on days GitHub published, but switch the task off, and remove the
logins when the backup is retired:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\SPB-runner\register_task.ps1 -Disable
C:\SPB-runner\venv311\Scripts\python.exe C:\SPB-runner\laptop_logins.py --forget
```

Then revoke the "SPB Jazib laptop" tokens at supabase.com (Account > Access Tokens) and at
vercel.com/account/tokens. Deleting `C:\SPB-runner` also removes the database copy, the
sessions and the builder list from the disk.
