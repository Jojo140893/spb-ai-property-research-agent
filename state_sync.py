"""Move the database between this machine and Supabase Storage, so the harvest can run
somewhere Ahsan's laptop is not.

WHY THIS AND NOT A PORTGRES MIGRATION. The blocker on running the harvest anywhere else
was never the portals — Proxima's session was proved portable on 12 Aug. It is that
`spb_research_audit.db` IS the state of the system: 11,319 rows, identity by content_hash,
the supersede chain, the promotion flags. It is gitignored and exists only on the laptop,
so a cloud runner starts empty and publishes an empty dashboard.

Porting database.py to Postgres would be a week of risk against a mature, SQLite-coupled
pipeline with a 44-test suite pinned to its behaviour, and it would not make the harvest
one minute more reachable. So the file stays SQLite and Supabase Storage just makes it
reachable: pull, run the existing pipeline untouched, push. 13 MB each way.

THE LOCK IS THE POINT. Two runners harvesting at once is not a slow day, it is data loss:
both pull the same file, both write, and whoever pushes last silently discards the other's
entire run. So a pull takes a lock object in the bucket and a push releases it. A lock
older than LOCK_STALE_HOURS is assumed abandoned (a runner that crashed mid-harvest) and
can be broken, because the alternative is a dead lock nobody can clear at 4am.

    python state_sync.py pull          # bucket -> local, taking the lock
    python state_sync.py push          # local -> bucket, releasing it
    python state_sync.py status        # who holds the lock, and how the two copies compare
    python state_sync.py pull --force  # break a stale lock

AUTH. Uses the Supabase CLI, which is already installed and logged in here. On a runner,
set SUPABASE_ACCESS_TOKEN and it works non-interactively with no other change. No key is
read, stored or printed by this file, and nothing it writes belongs in the repo.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent
DB = APP / "spb_research_audit.db"
BUCKET = os.environ.get("SPB_STATE_BUCKET", "spb-state")
REMOTE_DB = f"ss:///{BUCKET}/spb_research_audit.db"
REMOTE_LOCK = f"ss:///{BUCKET}/harvest.lock.json"

# A harvest takes ~30 minutes. Well beyond that means the holder died rather than is busy.
LOCK_STALE_HOURS = int(os.environ.get("SPB_LOCK_STALE_HOURS", "4"))

# PORTAL SESSIONS TRAVEL IN THE BUCKET, NOT IN REPO SECRETS.
#
# A runner cannot sign in to E-Agent or Proxima — those need a human, and Proxima's 2FA
# needs Colin's inbox. So it has to be handed a session that a human already established.
# The obvious place is GitHub secrets, and that is the wrong one: these files EXPIRE
# (Proxima's Magento session died twice in nine days), so every refresh would mean a human
# editing repo settings. In the bucket, the laptop refreshes them with the tools that
# already exist and the runner picks them up on its next pull, with nobody touching
# GitHub.
#
# They are session cookies, so the bucket is private and they are never written anywhere
# the repo can see. .sessions/ is gitignored and stays that way.
SESSION_DIR = APP / ".sessions"
REMOTE_SESSIONS = f"ss:///{BUCKET}/sessions"


def _staged(name):
    r"""A path INSIDE the app directory, handed to the CLI as a relative name.

    The Supabase CLI on Windows refuses an absolute local path — `cp
    C:\Users\...\x.json ss:///bucket/x` fails with "Unsupported operation. Run cp -r
    <src> <dst> to copy between local directories", because it reads the drive-letter path
    as a second local target. A tempfile.TemporaryDirectory() path is always absolute, so
    every lock write failed with "could not take the lock" and no reason. Staging in the
    app directory and passing the bare filename is what works.
    """
    return APP / name


def _rel(path):
    """`path` (inside APP) as the CLI takes it: relative, with forward slashes."""
    return Path(path).relative_to(APP).as_posix()


def _fetch(remote, dest, timeout=120, min_bytes=1):
    """Bucket object -> `dest` (a path inside APP), REPLACING whatever `dest` holds.

    THE CLI NEVER OVERWRITES A LOCAL FILE, and it says so only in a line nobody reads.
    `storage cp ss:///... <local>` opens the destination exclusively: when the file is
    already there the copy fails with "failed to create file: open ...: The file exists."
    before a byte is fetched. And a copy that fails after that open (a 404, a network blip)
    leaves a 0-byte file behind. Measured with CLI 2.107.0 on 26 Oct 2026.

    Nothing that runs on a fresh GitHub checkout ever sees this, which is why it went
    unnoticed. A laptop keeps its checkout, so there:
      * the builder CSV was pulled once (30 Sep, 4,598 bytes) and every later pull logged
        "!! builder csv NOT pulled", ran on that 30 Sep copy, then PUSHED it back over the
        bucket's;
      * on a fresh laptop whose first pull fails, the empty file left behind is never
        replaced: 0 builders, no direct portals and an empty builder directory, every night.
    (It was not the Windows backslash in the relative path: `drive_input\\vendors.csv`
    resolves to the same file, and the 30 Sep pull used exactly that path and worked.)

    So the copy lands on a fresh staging name beside `dest`, and replaces `dest` only once
    it is complete and at least `min_bytes` long. Returns (ok, why): when ok is False,
    `dest` is exactly as it was before the call.
    """
    dest = Path(dest)
    staging = dest.with_name(f".{dest.name}.pulling")
    staging.unlink(missing_ok=True)          # debris from a run that was killed mid-copy
    try:
        p = _cli("storage", "cp", remote, _rel(staging), timeout=timeout)
        if p is None:
            return False, "the Supabase CLI did not run"
        if p.returncode != 0 or not staging.exists():
            last = (p.stderr or p.stdout or "").strip().splitlines()
            return False, (last[-1][:160] if last else f"exit {p.returncode}")
        size = staging.stat().st_size
        if size < min_bytes:
            return False, f"the bucket's copy is only {size} bytes"
        os.replace(staging, dest)
        return True, ""
    finally:
        staging.unlink(missing_ok=True)


def _cli(*args, timeout=900):
    """Run the Supabase CLI. --experimental is required for every storage subcommand."""
    # --yes because `storage rm` PROMPTS ("Confirm deleting files in bucket spb-state?
    # [y/N]"). Unattended that prompt is never answered, so the lock was never released
    # and the next run refused to start — a deadlock that only appears at 4am.
    cmd = ["supabase", *args, "--linked", "--experimental", "--yes"]
    try:
        p = subprocess.run(cmd, cwd=str(APP), capture_output=True, text=True,
                           timeout=timeout)
    except FileNotFoundError:
        print("!! the Supabase CLI is not installed or not on PATH")
        return None
    except subprocess.TimeoutExpired:
        print(f"!! timed out after {timeout}s: supabase {' '.join(args[:2])}")
        return None
    return p


def _read_lock():
    """The lock object as a dict, or None when the bucket has no lock."""
    local = _staged(".harvest.lock.read.json")
    try:
        # A copy left by a run killed inside this block would make the CLI refuse ("The
        # file exists"), and a refused read means "no lock": two runners at once.
        local.unlink(missing_ok=True)
        p = _cli("storage", "cp", REMOTE_LOCK, local.name, timeout=120)
        if p is None or p.returncode != 0 or not local.exists():
            return None
        try:
            return json.loads(local.read_text(encoding="utf-8"))
        except Exception:                                             # noqa: BLE001
            # A lock we cannot parse is still a lock. Treating it as absent is how two
            # runners end up harvesting at once.
            return {"holder": "(unreadable)", "taken_at": ""}
    finally:
        local.unlink(missing_ok=True)


def _lock_age_hours(lock):
    try:
        taken = datetime.fromisoformat(str(lock.get("taken_at")))
    except (TypeError, ValueError):
        return None
    if taken.tzinfo is None:
        taken = taken.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - taken).total_seconds() / 3600.0


def _write_lock():
    body = {"holder": f"{socket.gethostname()}:{os.getpid()}",
            "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    local = _staged(".harvest.lock.write.json")
    try:
        local.write_text(json.dumps(body), encoding="utf-8")
        # Remove any existing lock first: `cp` answers 409 KeyAlreadyExists rather than
        # overwriting, so taking a lock over a stale one failed with "could not take the
        # lock" — which is exactly the case --force exists to handle, and it could not.
        # Safe here because the caller has already decided the old lock may be broken.
        _cli("storage", "rm", REMOTE_LOCK, timeout=120)
        p = _cli("storage", "cp", local.name, REMOTE_LOCK, timeout=120)
    finally:
        local.unlink(missing_ok=True)
    return (p is not None and p.returncode == 0), body


def _clear_lock():
    p = _cli("storage", "rm", REMOTE_LOCK, timeout=120)
    return p is not None and p.returncode == 0


def cmd_status(_args):
    lock = _read_lock()
    print(f"  bucket        : {BUCKET}")
    print(f"  local database: {DB.stat().st_size:,} bytes" if DB.exists() else
          "  local database: MISSING")
    if not lock:
        print("  lock          : free")
    else:
        age = _lock_age_hours(lock)
        stale = age is not None and age > LOCK_STALE_HOURS
        print(f"  lock          : held by {lock.get('holder')} since {lock.get('taken_at')}"
              + (f"  ({age:.1f}h — STALE, --force will break it)" if stale
                 else f"  ({age:.1f}h)" if age is not None else ""))
    return 0


def cmd_pull(args):
    """Bucket -> local, taking the lock first. Refuses if someone else holds it."""
    lock = _read_lock()
    if lock:
        age = _lock_age_hours(lock)
        stale = age is not None and age > LOCK_STALE_HOURS
        if not (args.force or stale):
            print(f"!! locked by {lock.get('holder')} since {lock.get('taken_at')}"
                  + (f" ({age:.1f}h)" if age is not None else ""))
            print("   Another harvest is running. Refusing to pull — two runs over one")
            print("   database means whoever pushes last silently discards the other.")
            print(f"   A lock older than {LOCK_STALE_HOURS}h can be broken with --force.")
            return 1
        print(f"   breaking a {'stale' if stale else 'forced'} lock held by {lock.get('holder')}")

    ok, body = _write_lock()
    if not ok:
        print("!! could not take the lock — not pulling")
        return 1
    print(f"   lock taken by {body['holder']}")

    # The local copy is kept until the download is proven good. A truncated download
    # over the only database on this machine would be the worst outcome available.
    staged = _staged(".pulled.db")
    try:
        staged.unlink(missing_ok=True)       # the CLI will not overwrite it (see _fetch)
        p = _cli("storage", "cp", REMOTE_DB, staged.name)
        if p is None or p.returncode != 0 or not staged.exists():
            print("!! download failed — local database untouched")
            if p is not None and p.stderr:
                print("   " + p.stderr.strip().splitlines()[-1][:160])
            _clear_lock()
            return 1
        rows = _row_count(staged)
        if rows is None:
            print("!! the downloaded file is not a readable SQLite database — discarded")
            _clear_lock()
            return 1
        if DB.exists():
            backup = DB.with_name(f"{DB.stem}.before-pull-{datetime.now():%Y%m%d-%H%M%S}.db")
            shutil.copy2(DB, backup)
            print(f"   local copy backed up to {backup.name}")
        shutil.copy2(staged, DB)
    finally:
        staged.unlink(missing_ok=True)
    print(f"   pulled {DB.stat().st_size:,} bytes, {rows:,} building row(s)")
    SESSION_DIR.mkdir(exist_ok=True)
    _sync_sessions("pull")
    _sync_builder_csv("pull")
    return 0


def cmd_push(args):
    """Local -> bucket, then release the lock."""
    if getattr(args, "files_only", False):
        # No database, and NO LOCK RELEASE: this can run while a harvest holds the lock,
        # and releasing someone else's lock would let a second runner start on top of it.
        _sync_sessions("push")
        _sync_builder_csv("push")
        print("   files only — database and lock untouched")
        return 0
    if not DB.exists():
        print("!! no local database to push")
        return 1
    rows = _row_count(DB)
    if rows is None:
        print("!! the local database is not readable — refusing to push it over a good copy")
        return 1
    if rows == 0 and not args.force:
        print("!! the local database has 0 building rows. Refusing to overwrite the")
        print("   bucket with it — this is what an empty runner looks like, and pushing")
        print("   it would destroy the real catalogue. Use --force if this is deliberate.")
        return 1

    # AND REFUSE A STALE ONE, not just an empty one.
    #
    # 0 rows was the only shape guarded against, and it is not the shape that actually
    # happens. On 17 Aug 2026 a `push` intended only to send the builder CSV also sent the
    # database — because push sends everything — and a 12 Aug local copy (11,474 rows)
    # overwrote the bucket's 11,693, discarding that day's harvest: 174 new E-Agent rows,
    # the recovered Denman lot, the lot Coleen's consultant had just been told was back.
    # Nothing refused it, because 11,474 is not 0.
    #
    # The nightly runner is unaffected either way (it pulls, harvests, pushes more than it
    # pulled). This guard exists for the laptop, which is where a stale copy lives.
    if not args.force:
        remote_rows = _remote_row_count()
        if remote_rows is not None and remote_rows > rows:
            print(f"!! the bucket holds MORE rows than this machine: {remote_rows:,} vs {rows:,}.")
            print("   Pushing would discard a harvest that has already run. Refresh first:")
            print("     python state_sync.py pull")
            print("   Use --force only if losing those rows is genuinely intended.")
            return 1
    # UPLOAD, THEN SWAP. `storage cp` will not overwrite — it answers 409
    # KeyAlreadyExists — so the object has to be removed first. Removing the live
    # database and then uploading would leave a window with NO database in the bucket,
    # and a failure inside that window loses the only off-machine copy. So the new file
    # goes to a staging key, the live key is removed only once that upload succeeded, and
    # the staging key is renamed into place.
    incoming = f"ss:///{BUCKET}/spb_research_audit.incoming.db"
    _cli("storage", "rm", incoming, timeout=120)          # any debris from a failed run
    p = _cli("storage", "cp", DB.name, incoming)
    if p is None or p.returncode != 0:
        print("!! upload failed — the bucket still holds the previous database, and the")
        print("   lock is NOT released, so nothing else will start on stale state.")
        if p is not None and p.stderr:
            print("   " + p.stderr.strip().splitlines()[-1][:160])
        return 1
    _cli("storage", "rm", REMOTE_DB, timeout=120)
    p = _cli("storage", "mv", incoming, REMOTE_DB, timeout=300)
    if p is None or p.returncode != 0:
        print("!! the upload succeeded but the rename did not. The new database is at")
        print(f"   {incoming} — recover with:")
        print(f"     supabase storage mv {incoming} {REMOTE_DB} --linked --experimental --yes")
        return 1
    print(f"   pushed {DB.stat().st_size:,} bytes, {rows:,} building row(s)")
    _sync_sessions("push")
    _sync_builder_csv("push")
    print("   lock released" if _clear_lock() else "   !! could not release the lock")
    return 0


def _sync_sessions(direction):
    """Copy .sessions/*.json between here and the bucket. Never fatal.

    A missing or stale session costs one channel on one run and says so in the log; a
    failure here must not cost the whole harvest, which is why nothing this returns is
    checked. Files are copied one at a time because `cp -r` on the CLI takes a directory
    and this only ever wants the JSON.
    """
    if not SESSION_DIR.exists():
        return
    if direction == "push":
        for f in sorted(SESSION_DIR.glob("*.json")):
            # Backups and aborted captures are local debris, not sessions a runner wants.
            if any(t in f.name for t in (".backup-", ".aborted", ".expired")):
                continue
            rel = f"{SESSION_DIR.name}/{f.name}"
            _cli("storage", "rm", f"{REMOTE_SESSIONS}/{f.name}", timeout=60)
            _cli("storage", "cp", rel, f"{REMOTE_SESSIONS}/{f.name}", timeout=120)
        print(f"   sessions pushed: {len(list(SESSION_DIR.glob('*.json')))} file(s)")
    else:
        p = _cli("storage", "ls", f"{REMOTE_SESSIONS}/", timeout=120)
        if p is None or p.returncode != 0:
            return
        names = [l.strip() for l in (p.stdout or "").splitlines()
                 if l.strip().endswith(".json")]
        # REPLACING the local file (see _fetch): a plain cp kept every session this machine
        # already had, so a laptop ran on its own old sessions and pushed them back.
        missed = [name for name in names
                  if not _fetch(f"{REMOTE_SESSIONS}/{name}", SESSION_DIR / name)[0]]
        if names:
            print(f"   sessions pulled: {len(names) - len(missed)} file(s)")
        if missed:
            print(f"   !! {len(missed)} session(s) NOT pulled: {', '.join(missed[:5])}")


def _sync_builder_csv(direction):
    """Carry the builder directory CSV between here and the bucket. Never fatal.

    WITHOUT IT A RUNNER HAS NO BUILDERS AT ALL, and says so only in a line nobody reads:
    "0 registry builders". BuilderRegistry loads from config.BUILDER_CSV_PATH
    (drive_input/vendors.csv, else Book1(Builders) List.csv) and BOTH are gitignored — they
    have to be, that file has a PASSWORD column and leaking it once is what
    SECURITY_CREDENTIAL_EXPOSURE.md is about. So since the harvest moved to GitHub Actions
    on 12 Aug 2026 the runner has loaded an EMPTY directory, and two things followed that
    nobody connected:

      * "Direct portals: 0 unique listing(s) seen" every single night —
        BuilderPortalSource iterates the registry, and an empty registry visits no portals.
        Its 388 stored listings are the ones the laptop read on 12 Aug, going stale since.
      * The published Approved Builder Directory tab was built with 0 rows on any nightly
        publish, and 33 whenever a human published from the laptop.

    Same channel as the sessions, and for the same reason: private bucket, never the repo.
    build_web.py already strips credentials from what it publishes
    (_export_builders / "credentials blanked"), so the deployed copy stays safe — this only
    puts the file where the HARVEST can read it.
    """
    import config
    # ONE FIXED REMOTE NAME, never the local file's own. config.BUILDER_CSV_PATH resolves
    # to drive_input/vendors.csv where that exists and "Book1(Builders) List.csv" where it
    # does not — so keying the bucket on local.name meant the laptop stored
    # builders/vendors.csv while the runner asked for builders/Book1(Builders) List.csv,
    # missed, reported "0 builders", and then pushed an empty file back under the name it
    # had guessed. Measured on the 17 Aug restore run.
    remote = f"ss:///{BUCKET}/builders/vendors.csv"
    # PULL LANDS ON THE PREFERRED PATH, not on BUILDER_CSV_PATH. That constant is fixed at
    # import time by which file existed THEN — on a fresh runner, before this pull, that is
    # the fallback name. Every later step is a separate process that re-imports config, so
    # writing the preferred path is what makes them pick it up.
    dest = config.DRIVE_INPUT_DIR / "vendors.csv"
    local = config.BUILDER_CSV_PATH if direction == "push" else dest
    # Left beside the CSV by a pull that failed, removed by one that worked. A machine whose
    # pull failed holds a copy that may be OLDER than the bucket's, and pushing it back is how
    # the laptop runner sent its 30 Sep copy over the bucket's every night from 25 Oct.
    unpulled = local.with_name(f".{local.name}.unpulled")
    if direction == "push":
        if not local.exists():
            print("   builder csv: nothing local to push")
            return
        # A runner that failed its pull must not push its empty guess over the real file.
        if local.stat().st_size < 512:
            print(f"   builder csv: {local.name} is only {local.stat().st_size} bytes — "
                  "refusing to push it over the real directory")
            return
        # ... nor its possibly older copy. Edited by hand since that failed pull? Then it is
        # deliberate, and goes.
        if unpulled.exists() and unpulled.stat().st_mtime >= local.stat().st_mtime:
            print("   builder csv: not pushed — this machine's last pull of it failed, so its "
                  "copy may be older than the bucket's")
            return
        # Relative name: the CLI rejects an absolute local path on Windows (see _staged).
        try:
            rel = _rel(local)
        except ValueError:
            print(f"   builder csv: {local} is outside {APP}, not pushed")
            return
        _cli("storage", "rm", remote, timeout=60)
        p = _cli("storage", "cp", rel, remote, timeout=180)
        print(f"   builder csv pushed: {local.name}"
              if p is not None and p.returncode == 0 else
              "   builder csv: push FAILED (the runner will have no builders)")
    else:
        local.parent.mkdir(parents=True, exist_ok=True)
        try:
            local.relative_to(APP)
        except ValueError:
            return
        # Replacing the local copy, and only with a whole one: the CLI never overwrites a
        # local file, so a plain cp failed on every pull after the first (see _fetch).
        # 512 bytes is the floor push applies too.
        ok, why = _fetch(remote, local, timeout=180, min_bytes=512)
        if ok:
            unpulled.unlink(missing_ok=True)
            print(f"   builder csv pulled: {local.name}")
            return
        unpulled.write_text(f"{datetime.now():%Y-%m-%d %H:%M:%S} {why}\n", encoding="utf-8")
        if local.exists() and local.stat().st_size >= 512:
            kept = datetime.fromtimestamp(local.stat().st_mtime)
            print(f"   !! builder csv NOT pulled ({why}) — using this machine's copy from "
                  f"{kept:%d %b %Y %H:%M}, which may be out of date.")
        else:
            # Loud, because the run will otherwise succeed while visiting no portals and
            # publishing an empty builder directory.
            print(f"   !! builder csv NOT pulled ({why}) — this run will see 0 builders, "
                  "visit no direct portals, and publish an empty builder directory.")
            print("      Push it once from the laptop:  python state_sync.py push")


def _remote_row_count():
    """Building rows in the bucket's copy, or None if it cannot be read.

    Downloads 13 MB to answer a question that decides whether to DESTROY that copy. The
    push already moves the same volume the other way, so the cost is symmetric — and it is
    the only honest way to know: object size does not track row count (a supersede pass
    grows the file while the live count falls).

    None means "could not tell", and the caller must treat that as permission to proceed
    rather than as a block: a network blip must not make the laptop unable to push at all.
    """
    local = _staged(".remote_check.db")
    try:
        # Left over, it would make this answer None -- "could not tell" -- and wave a stale
        # push through. The CLI will not overwrite it (see _fetch).
        local.unlink(missing_ok=True)
        p = _cli("storage", "cp", REMOTE_DB, local.name, timeout=600)
        if p is None or p.returncode != 0 or not local.exists():
            return None
        return _row_count(local)
    finally:
        local.unlink(missing_ok=True)


def _row_count(path):
    """Building rows, or None when the file is not a readable SQLite database."""
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return con.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
        finally:
            con.close()
    except Exception:                                                 # noqa: BLE001
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("pull", cmd_pull), ("push", cmd_push), ("status", cmd_status)):
        s = sub.add_parser(name)
        s.add_argument("--force", action="store_true",
                       help="break a stale lock (pull) / push a stale or empty database (push)")
        # Sending a refreshed session or an updated builder list should not require
        # sending a 13 MB database with it. It did, and on 17 Aug that took a day's
        # harvest with it — the database is simply what `push` happened to send first.
        s.add_argument("--files-only", action="store_true",
                       help="push/pull only the sessions and the builder CSV, "
                            "leaving the database and the lock alone")
        s.set_defaults(fn=fn)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
