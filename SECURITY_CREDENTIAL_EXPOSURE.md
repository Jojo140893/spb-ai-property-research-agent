# Builder-portal passwords are in public git history

**Status: one password still live (Bathla). Two of the three are already rotated.**
Verified 2026-08-07 against this repository.

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
has been public since 22 July, and you cannot know whether anyone cloned it. History
rewriting does not undo a password that has already been read.

Change them on each builder's own portal, then store the new ones in the OS vault:

```bash
python setup_credentials.py portal_bathla
```

Paramount Living and Hermitage Homes have already been rotated and need nothing. If you
want to re-check that at any time:

```bash
python check_exposed_credentials.py
```

Each prompts for the password with the input hidden. It goes straight into the Windows
credential vault — never into the repo, a log, or an environment variable. **This step
needs a human at a keyboard; an agent must not type a password.**

### 2. Then scrub the history

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
