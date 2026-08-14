# Builder-portal passwords were in public git history

**Status (2026-08-14): the history is scrubbed and the repo is private. One portal
password — Bathla — is STILL the exposed value and must be rotated on Bathla's portal.**
Paramount Living and Hermitage Homes were already rotated.

Update log:
- **2026-08-14** — the repo is now **private** (anonymous `raw.githubusercontent.com` access
  to dangling objects returns 404). The local clone's dangling object was pruned
  (`git reflog expire --expire=now --all && git gc --prune=now` after deleting the
  filter-repo leftover `refs/original/refs/heads/main`); `git show e0b3a76:"Book1(Builders) List.csv"`
  now fails locally. Reachable history is clean on `main` and `origin/main` (synced).
  A full-history sweep found **no other committed secret** — no keys, tokens, or `.env`
  with real values. Bathla confirmed still live by digest. **Rotation is the one open item.**
- **2026-08-07** — first documented; scrub run and force-pushed (see below).

## What is exposed

`Book1(Builders) List.csv` was committed on 22 Jul and removed from `HEAD` on 31 Jul.
Removing a file from `HEAD` does not remove it from history, and the commits that still
contain it are on the **public** remote:

| commit | on `origin/main`? | notes |
|---|---|---|
| `e0b3a76` (22 Jul, initial commit) | **yes — public** | full file |
| `0130fb7` (31 Jul) | **yes — public** | full file |
| `e79d10c` (31 Jul) | local only | the removal |

Remote: `https://github.com/Jojo140893/spb-ai-property-research-agent`

Anyone who can read the repository can run one command and read the file. No exploit and
no special access is needed:

```bash
git show e0b3a76:"Book1(Builders) List.csv"
```

**Contents of that file version:** 236 rows, a `PASSWORD` column with **3 populated
passwords** — Paramount Living, Hermitage Homes, Bathla — plus builder-rep phone numbers
and email addresses.

The file is now correctly listed in `.gitignore` (`*Builders* List.csv`), so this cannot
recur. That protects the future, not the past.

## Which of them are still live (checked 2026-08-07)

Compared the exposed value against what is in the OS vault today, by hash, so neither
value was printed:

| builder | exposed password still the current one? |
|---|---|
| Paramount Living | no — already rotated |
| Hermitage Homes | no — already rotated |
| **Bathla** | **YES — still live** |

So this is **one portal, not three**. Repository state at the same check: public,
**0 forks**, last pushed 5 Aug.

Two rotated passwords are still worth treating as burned for anything they were reused
on, but they no longer open those two portals.

## What to do, in this order

### 1. Rotate Bathla — the one that is still live

Do this before anything else, and treat all three as already compromised — the repository
was public from 22 July to 13 Aug, and a proxy vendor's prospecting email on 14 Aug
(automated GitHub scraping) is direct evidence outsiders were watching it while public.
History rewriting does not undo a password that has already been read.

**The Bathla password lives in FOUR places, and run-time precedence is vault → env → CSV**
(`sources/builder_portals.py:86`). Rotate in this order so the harvest is never left
authenticating with a dead password:

1. Change it on **Bathla's own portal** (a human at a keyboard; an agent must not type it).
2. **GitHub Actions secret** `PORTAL_BATHLA_PASSWORD` — this is what the nightly runner
   reads, because a Linux runner has no OS vault. Without this the harvest keeps using the
   old password tonight:
   ```bash
   gh secret set PORTAL_BATHLA_PASSWORD -R Jojo140893/spb-ai-property-research-agent
   ```
3. **OS vault** — only used when the harvest runs on the laptop:
   ```bash
   python setup_credentials.py portal_bathla
   ```
4. **The vendor CSVs** — `drive_input/vendors.csv` (active) and `Book1(Builders) List.csv`
   (fallback) both still hold the old value in their `PASSWORD` column. Once 2–3 above
   carry the new value, blank Bathla's `PASSWORD` cell in both so no stale secret sits on
   disk and nothing silently falls back to a dead password.

Then confirm — it reads the exposed value (now from the on-disk CSV, since the git object
is pruned) and compares by digest, printing nothing:

```bash
python check_exposed_credentials.py     # Bathla should flip to "already rotated"
```

Paramount Living and Hermitage Homes have already been rotated and need nothing. If you
want to re-check that at any time:

```bash
python check_exposed_credentials.py
```

Each prompts for the password with the input hidden. It goes straight into the Windows
credential vault — never into the repo, a log, or an environment variable. **This step
needs a human at a keyboard; an agent must not type a password.**

### 2. The history scrub is DONE — and it did not close the exposure

Run on 2026-08-07: `git filter-repo` removed the file from all 125 commits and the
rewritten history was force-pushed. Locally the file is gone; `git show e0b3a76` now
answers "invalid object name".

**GitHub still serves it.** Verified anonymously, after the force-push:

```
https://raw.githubusercontent.com/Jojo140893/spb-ai-property-research-agent/e0b3a76/Book1(Builders)%20List.csv
-> HTTP 200, 19,097 bytes
```

A force-push does not delete anything on GitHub. Unreachable commits stay in the
repository and remain fetchable **by SHA** — and the SHA is in this document, in the
commit log, and in anyone's existing clone. GitHub garbage-collects them on its own
schedule, if ever.

So the rewrite was necessary and is not sufficient. What actually closes it, in order of
how fast it works:

1. **Rotate the password.** — **OUTSTANDING (Bathla).** Still the only step that ends the
   risk on your side of the wire. See section 1 above for all four storage locations.
2. **Make the repository private** — **DONE (by 14 Aug).** Anonymous access to dangling
   objects now returns 404. This is what neutralised the "GitHub still serves it by SHA"
   problem below, short of a GC.
3. **Ask GitHub Support to garbage-collect** the unreachable objects, citing SHAs
   `e0b3a76` and `0130fb7` — *optional now the repo is private.* There is no API for it;
   it is a support request. Worth doing only if the repo may go public again.

### 3. If you want the old advice anyway

Only after rotation, because this is the slow and disruptive half and it does not reduce
risk on its own.

```bash
git filter-repo --path "Book1(Builders) List.csv" --invert-paths --force
```

Then force-push. **This rewrites public history**: every clone and fork must be re-cloned,
and any open pull request will need recreating. Decide deliberately, and tell anyone else
working on the repo before you do it.

There are currently **40 local commits that have never been pushed**. Doing the scrub
before pushing them is easier — there is less public history to rewrite. That is the
reason they have been left unpushed.

### 3. Consider whether the repository needs to be public at all

It contains a client's commercial stock data and integration logic. If it does not need to
be public, making it private removes this entire class of problem and takes a minute.

## What was already done

- The file is untracked and gitignored, so no future commit can carry it.
- `build_web.py` refuses to export any field whose name matches `password`, `token`,
  `secret`, `login_email` or `session`, so the deployed site cannot serve them.
- Two builder-rep email addresses that had reached the deployed `NOTES` field were
  scrubbed.
- Folder-level share links are no longer published (see `_assert_no_folder_capability_urls`).

None of that addresses the git history. Only steps 1 and 2 above do.
