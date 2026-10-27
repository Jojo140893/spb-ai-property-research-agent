# Dashboard password reset: owner runbook

Written 23 Oct 2026. Once switched on, the dashboard (app.smartpropertybuying.com.au) shows a
**Forgot password?** link on its sign-in page. It emails a reset code and link to
digital@smartpropertybuying.com.au. Whoever reads that mailbox chooses a new password. Once GitHub has
applied it (usually a few minutes; never while GitHub is refusing jobs) only the new password works,
and everyone who was signed in is signed out.

Why it exists: on the 23 Oct 2026 call Colin said people have left his team and he wants to change
the dashboard password himself, without a developer.

How it works, in one paragraph: the reset page checks the code and turns the new password into a
signed, encrypted record. It starts the GitHub workflow `apply-dashboard-password.yml`, which first asks
the live site (`/api/reset_check`) to confirm the dashboard signed that record, and GitHub to confirm
that this is the first attempt of the run the dashboard started for it, then stores it in Vercel
as `SPB_PW_RECORD` and redeploys production. The sign-in check (`middleware.js`)
reads that env var from memory and makes no network call. Nothing is kept in Vercel Blob: the team is
on the Hobby plan, whose Blob quota (10,000 operations a month, shared with the hook) would have run
out, and the 30-day lock-out after that would have stopped everyone signing in. Design:
`api/_spb_auth.py` (header) and the FORGOT PASSWORD block in `build_web.py`. Tests:
`tests/test_forgot_password.py`.

**Where the tokens live.** The dashboard holds only `GITHUB_DISPATCH_TOKEN` (already there for the
Refresh button), which can start workflows and do nothing on Vercel. The token that writes the env var
and redeploys is the `VERCEL_TOKEN` GitHub secret the nightly already uses (team-scoped to
digitalx-solutions-projects). It never enters the dashboard.

---

## 1. Switch it on (owner, once, about 10 minutes)

Until all of this is done, the feature is OFF: the sign-in page is byte-for-byte what it was, there
is no link, and `/forgot` and `/reset` need a login like any other page.

Run these from `D:\Coleen\app\vercel_site` (linked to the `vercel_site` project). Use `vercel.cmd`,
not `vercel`: PowerShell's execution policy blocks the `.ps1` shim on this laptop.

1. **The signing secret.** This command generates and stores it in one go, so nobody ever sees it:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48), end='')" | vercel.cmd env add SPB_AUTH_SECRET --environment production --sensitive --yes
   ```

   Never change or remove it once a password has been reset: sign-in would answer 503 (section 5).
2. **A Google app password for digital@.** Sign in to the digital@ Google account, go to
   https://myaccount.google.com/apppasswords, name it `SPB dashboard reset`, and copy the 16
   letters. (This needs 2-Step Verification, which the account already has because the nightly
   harvest reads that inbox. Make a NEW one, so either can be revoked alone.) Store it at the hidden
   prompt:

   ```bash
   vercel.cmd env add SPB_SMTP_PASSWORD --environment production --sensitive
   ```

3. **Nothing else to create.** `GITHUB_DISPATCH_TOKEN` is already on the project, and the workflow uses
   the GitHub secrets the nightly already has (`VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`).
4. **Merge the branch to main.** The workflow has to be on main before GitHub will start it, and its
   job runs only for main (a run started on any other branch is skipped). The nightly run deploys main,
   and that deploy picks up the env vars above.

Optional settings (Production env, same way):

| Name | Default | What it does |
|---|---|---|
| `SPB_RESET_TO` | digital@smartpropertybuying.com.au | Where codes go. Set it to Colin's own address if only he should be able to reset. |
| `SPB_SMTP_USER` | digital@smartpropertybuying.com.au | The Gmail account that sends (must match the app password). |
| `SPB_SITE_URL` | https://app.smartpropertybuying.com.au | The site the emailed link points at. |

## 2. Check it works (5 minutes, on the live site)

1. Open https://app.smartpropertybuying.com.au/login. A **Forgot password?** link shows under the
   Sign in button. No link means a setting is missing or wrong: `SPB_AUTH_SECRET` (at least 32
   characters), `SPB_SMTP_PASSWORD` or `GITHUB_DISPATCH_TOKEN` is absent, or one of the optional
   settings is not a plain email address or `https://` site.
2. **Forgot password?** -> **Email me a code**. An email "Smart Property Buying dashboard reset code"
   reaches digital@ within a minute. If it lands in Sent but not the Inbox (Gmail can do that with
   mail from an address to itself), set `SPB_RESET_TO` to another inbox.
3. Only go further if you mean to change the password now. Open the link, choose the new password,
   press **Change password**. After up to about 15 seconds the page says "GitHub has started applying it" (or
   "has not started applying it yet"), and that the old password still works until the page says the
   new one is live. When the redeploy has finished (usually a few minutes) it says "Your new password
   is live". GitHub's Actions tab shows the run "Apply dashboard password" in green; its last step
   proves a wrong password gets 401 on the live site. **While GitHub is refusing jobs over billing (as
   on 25 Oct 2026), the page says instead "GitHub could not run the change just now ... Nothing
   changed", and the old password stays.** It stays that way after billing is fixed: GitHub emails
   each failed run, and pressing Re-run on one is refused (section 5).

Logs: Vercel -> `vercel_site` -> Logs, filter `[auth]`. You will see lines such as `forgot: code sent`,
`forgot: limited`, `forgot: this window's code was already sent`, `forgot: not sent
(SMTPAuthenticationError 535)`, `reset: new password handed to GitHub (run started)`, `reset: GitHub
refused the run; nothing changed`, `reset: GitHub stopped the run; nothing changed`, `reset: hand-off
limit reached`, `reset: GitHub did not accept the hand-off (401)`, `reset: change notice timed out`,
`check: record confirmed`, `check: record refused (not_signed)` and `check: record refused
(not_newer_than_live)`. They never contain the code or a password.

## 3. What Colin sees

1. Sign-in page -> **Forgot password?** -> **Email me a code**.
2. The page always answers "If this site is set up for it, a code has been emailed", whatever
   happened, so a stranger learns nothing. The email holds a code like `7KQ2M-XR4TD` and a link.
3. He opens the link, or enters the code at **Enter the code**, then types the new password twice ->
   **Change password**. The rules: 12 to 128 characters, not the email address, not the current
   password (nor the original one), and no space at either end.
4. The page waits up to 15 seconds while GitHub starts the change and its first step checks it. What it
   then says is never "saved" and never a time, because the change can still be refused after that (and
   whoever holds the dispatch token can cancel any run):
   * **"GitHub has started applying it. Your old password still works until this page says the new one
     is live."** The page keeps watching. When it is live the page says so and offers **Sign in with
     the new password**. At that moment everyone else is signed out, and he tells his team the new
     password. If it is not live after 10 minutes the page says so, and that the old one still works.
   * **"Sent to GitHub, which has not started applying it yet"**: GitHub had not started it within 15
     seconds. Nothing is promised. The page keeps watching, and says so if it goes live.
   * **"GitHub could not run the change just now ... Nothing changed"**: GitHub refused to run it, for
     example because the account's billing failed (as on 30 Sep and 25 Oct 2026). The old password
     still works and no "change requested" email is sent. The same code works again while it lasts,
     and a new one can be asked for after that. That run is never applied later: a re-run of it is
     refused, and so is its record started again in a new run (section 5).
   * **"GitHub stopped the change before saving it. Nothing changed"**: the workflow's own check refused
     it inside the 15 seconds (for example the daily limit below), or the run was cancelled. Old
     password, no email, the code still works. The same holds: that run is never applied later, with
     one exception in the first minute that only someone who can start the workflow (the dispatch
     token, or write access to the repo) can cause (section 7).

   The "password change requested" email goes out with the first two answers. It says the same: the new
   password is not in use yet, may not be, and the old one works until the reset page says it is live.

Limits. A code works for 15 minutes, and stops working as soon as any new password goes live. At most
one code email per 5 minutes per server copy. Everyone who asks inside those 5 minutes would get the
same code. So when the page says "a code has been emailed" and nothing new arrives, the code from the
last 5 minutes is the one to use, and it still has at least 15 minutes left. Per person (by IP
address, and for IPv6 the whole /64 block): one code email a minute, three an hour, and 20 wrong codes a
minute. A stranger hammering the page blocks only themselves and cannot use anything up for Colin. At
most 3 password changes an hour per server copy, and 10 a day in all, enforced by the workflow from
GitHub's own run history. Each change is a Vercel deployment, Hobby allows 100 a day, and running out
would stop the nightly publishing. The daily 10 counts only runs that got as far as storing a password:
runs GitHub refused (billing) and runs the check refused (a forged record) changed nothing and do not
count. If GitHub will not list the runs, the change is refused, not guessed.

Two resets made at the same moment get the same password number. Whichever goes live first wins, and
the other is refused (a record is applied only over an older number); its page says "A password made at
the same moment as yours went live instead".

## 4. Who can reset: anyone who can read the digital@ mailbox

That is the whole security model. If people who left can still read digital@ (a shared login, a
phone still signed in, a forwarding rule), they can reset the dashboard too. Two ways to close that:

* Set `SPB_RESET_TO` to an address only Colin reads (section 1), or
* Change the digital@ Google password and remove old devices and forwarding rules.
  **Changing a Google password revokes every app password on that account.** That stops the nightly
  harvest reading the inbox (GitHub secret `EMAIL_INBOX_PASSWORD`) AND this feature
  (`SPB_SMTP_PASSWORD`). Make two new app passwords and update both the same day:
  `gh secret set EMAIL_INBOX_PASSWORD --repo Jojo140893/spb-ai-property-research-agent`,
  and step 2 of section 1.

## 5. Break-glass

Env var changes only take effect at the next deploy: Vercel dashboard -> `vercel_site` ->
**Deployments** -> the latest Production one -> ... -> **Redeploy**, or wait for the nightly run.

* **Colin is locked out** (forgot the new password, email not working): set `SITE_PASSWORD` to a NEW
  value and redeploy. The stored password was made under the old `SITE_PASSWORD`, so it is ignored
  from then on and the new `SITE_PASSWORD` signs in.
* **Someone else reset it**: the same, and also delete `SPB_PW_RECORD`. Then close their access to
  the mailbox (section 4).
* **Sign-in says "Sign-in is not working right now ... SPB_PW_RECORD"** (503): the stored password
  cannot be checked. Either it was hand-edited in Vercel, or `SPB_AUTH_SECRET` was removed or changed.
  The workflow cannot cause it: it stores only a record the live site confirms the dashboard signed
  (`/api/reset_check`). The page refuses rather than quietly bringing back the old password. **A new
  `SITE_PASSWORD` alone does not fix this one** (the record is checked before `SITE_PASSWORD` is).
  Fix: delete `SPB_PW_RECORD`, set a new `SITE_PASSWORD`, and redeploy (or put the old
  `SPB_AUTH_SECRET` back).
* **Never set `SITE_PASSWORD` back to a value it had before a reset** while `SPB_PW_RECORD` is still
  there: that record would match again. To drop a reset for good, delete `SPB_PW_RECORD`.
* **GitHub's "Re-run" on an "Apply dashboard password" run is always refused**, whatever the run did and
  however soon it is pressed: the first step stops any attempt but the first ("This is a re-run
  (attempt 2)"), before it asks anyone anything. A record copied out of a run's log and started again as
  a new run is refused too, unless GitHub created that run within a minute of the record ("This run is
  not confirmed as the one the dashboard started for this record"). Either way nothing changes. A new
  password needs a new reset from the dashboard.
* **The reset page never says "live"** (after 10 minutes it says the new password is not live yet and
  the old one still works): look at the run in GitHub's Actions tab.
  * Failed before "Store it in Vercel": nothing changed, the old password still works. Start a new reset.
    "did not confirm that the dashboard signed this record" in the first step means someone started
    the workflow with a record the dashboard never made: nothing was stored, but find out who
    (`GITHUB_DISPATCH_TOKEN` or write access to the repo) and replace that token. A run that was
    **cancelled** without anyone here pressing Cancel points the same way, and so does "This run is not
    confirmed as the one the dashboard started for this record" when the line above it says "started
    with a copy of the record". "This is a re-run" (or "GitHub says this is attempt 2") means someone
    pressed Re-run: nothing changed. "Could not count this workflow's recent password changes" or
    "could not read this run from GitHub" means GitHub would not answer: start a new reset later.
  * Failed after that step: the record is saved, and the next deploy puts it live. That signs
    everyone out at a moment nobody announced, so tell the team, or delete `SPB_PW_RECORD` to cancel.
  * GitHub Actions down or refusing jobs (it was on 30 Sep and 25 Oct 2026, over billing): a reset made
    then is **dropped, not queued**. GitHub fails the run within seconds ("The job was not started
    because recent account payments have failed") and never retries it. The reset page normally sees
    this and says nothing changed. Either way the old password still works. GitHub emails each failed
    run to the token's owner; pressing Re-run on one is refused (above). Start a new reset once Actions
    runs jobs again.

## 6. Turn it off / roll back

* **Off:** remove `SPB_SMTP_PASSWORD` and redeploy. The link and the pages disappear. A password
  already set this way **stays in force**: switching the feature off does not bring back the old
  `SITE_PASSWORD`. Leave `SPB_AUTH_SECRET` alone while `SPB_PW_RECORD` exists, or sign-in answers 503.
* **Drop a reset as well:** delete `SPB_PW_RECORD` and set a new `SITE_PASSWORD`, then redeploy.
* **Remove the code:** set a new `SITE_PASSWORD` first, then revert the merge commit on main; the next
  nightly deploys it. Code from before this feature ignores `SPB_PW_RECORD` and accepts only
  `SITE_PASSWORD`, and the old value is the one the people who left know.

## 7. Where things live, and what is left

| What | Where |
|---|---|
| The live password | Vercel env `SPB_PW_RECORD` (Production, Secret). PBKDF2-SHA256, 210,000 rounds, salted, then encrypted and signed with keys from `SPB_AUTH_SECRET`. It is also the input of each "Apply dashboard password" run, so GitHub shows it to repo readers, but only as ciphertext. |
| Codes | Nowhere. A code is computed from `SPB_AUTH_SECRET`, the 5-minute time window and the live password, and checked the same way. |
| Sign-in check | `middleware.js`, generated by `build_web.py`. It reads env vars only. `/login` reports the live password's number and id (`x-spb-auth-epoch`, `x-spb-auth-id`); neither helps anyone sign in. |
| The three endpoints | `api/reset_code.py`, `api/reset_password.py`, and `api/reset_check.py`, which the workflow asks before it stores a record (logic in `api/_spb_auth.py`) |
| Putting a password live | `.github/workflows/apply-dashboard-password.yml` |

Known and accepted:

* **Old deployment URLs keep the env they were built with.** Only the production domains (and the
  aliases that follow production) switch to the new password. Each old `vercelsite-<hash>` URL, and
  `spb-live-stock` (aliased by hand), still accepts the password of its day. Vercel's own login page
  (Deployment Protection) stands in front of those URLs, which is what `spb-live-stock` shows.
* **A deploy that starts in the same few seconds as a password change** could still finish in the wrong
  order. The workflow waits until no production deploy is building before it stores and redeploys,
  which closes the window except for those few seconds. The next nightly would put things right.
* **`VERCEL_TOKEN` and other branches.** The job runs only for main, and only the three steps that call
  Vercel get the token. But GitHub runs a branch's own copy of the workflow when it is started on that
  branch. The copies in c8d1695, 153bdd2 and 1ba2844 have neither the guard nor the `/api/reset_check`
  question (b82f599 has the question, not the guard), so never push those commits as a branch or tag.
  While `VERCEL_TOKEN`
  is a repository secret, a workflow on any pushed branch can read it. To make this structural (owner,
  optional, with a small code change): GitHub -> repo Settings -> Environments -> a new environment
  limited to the `main` branch, move `VERCEL_TOKEN` from the repository secrets into it, and add
  `environment: <its name>` to every job that uses the token (this workflow, the nightly's deploy, the
  token check).
* **Whoever holds `GITHUB_DISPATCH_TOKEN` can stop resets, and cannot choose a password.** They can
  cancel any run (the page then says "GitHub stopped the change", or after 10 minutes that it is not
  live), or start so many forged runs that GitHub will not list them all (over 1,000 a day) or its API
  limit for the run's token runs out, and then the daily count fails and resets are refused until it
  passes. They cannot use up the daily 10, nothing they make up is stored, and a re-run is refused. One
  thing they (or anyone with write access to the repo) can do: in the first minute after a reset,
  cancel its run (the page then says "GitHub
  stopped the change ... Nothing changed") and start the same record again as a new run. That puts the
  password Colin typed live after all, with no "change requested" email and no page watching, so
  everyone else is signed out. After that minute the copy is refused. So if the page says "GitHub
  stopped the change" and nobody here cancelled anything, look in the Actions tab for an "Apply
  dashboard password" run started just after it, tell the team if it went live, and replace the token.

The Leads-tab contact password (`SPB_CONTACT_PASSWORD`) is separate, and this feature does not touch it.
