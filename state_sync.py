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
    return 0


def cmd_push(args):
    """Local -> bucket, then release the lock."""
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
    print("   lock released" if _clear_lock() else "   !! could not release the lock")
    return 0


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
                       help="break a stale lock (pull) / push an empty database (push)")
        s.set_defaults(fn=fn)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
