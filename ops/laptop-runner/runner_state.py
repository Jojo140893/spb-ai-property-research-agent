"""The laptop runner's questions about the shared bucket, and about this machine.

nightly.ps1 calls this with its venv's Python. Every command but day-mark only READS: none of
them takes the harvest lock, and none touches the database.

    python runner_state.py --app <Root>/app bucket-check
    python runner_state.py --app <Root>/app day-status  --day 2026-10-27
    python runner_state.py --app <Root>/app lock-status [--wait-hours 8]
    python runner_state.py --app <Root>/app day-mark    --day 2026-10-27 [--gate-ok 1]
    python runner_state.py env-check

ONE RUN PER MELBOURNE DAY ACROSS LAPTOPS. Each laptop that finishes a harvest (pushed and
deployed) writes ss:///spb-state/laptop-runs/<Melbourne day>.json: the day, the machine, when it
finished and the code it ran. Every laptop reads it before starting and stands down when the
day is already done, by whichever machine. A harvest still RUNNING elsewhere has no marker
yet; the bucket lock (state_sync.py's harvest.lock.json) is what stops that one, and
lock-status reports it so the runner can stand down cleanly instead of failing on the pull.

Exit codes, which nightly.ps1 acts on:
    bucket-check   0 the bucket and its database are visible, 1 not
    day-status     0 not done today, 3 done today, 2 could not tell
    lock-status    0 free, 3 held (younger than --wait-hours), 4 held for longer, 2 could not tell
    day-mark       0 written, 1 not
    env-check      0 this Python can run the harvest, 1 not

THE CLI, AS state_sync.py USES IT: --linked (the app folder holds the link to the SPB
project), --experimental (every storage command needs it), --yes (rm prompts), local paths
RELATIVE to the app folder (it refuses an absolute Windows path), and it never overwrites a
local file -- so each download goes to a name that is removed first.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BUCKET = os.environ.get("SPB_STATE_BUCKET", "spb-state")
ROOT_URI = f"ss:///{BUCKET}/"
RUNS_DIR = "laptop-runs"
LOCK_NAME = "harvest.lock.json"
DB_NAME = "spb_research_audit.db"
OCR_VERSION = "1.4.4"           # what the runner's row identities were built with
PYTHON_MINOR = (3, 11)

DONE, UNKNOWN, HELD, HELD_LONG = 3, 2, 3, 4


# --- pure parts (tested in tests/test_laptop_runner_state.py) -----------------------------

def parse_ls(stdout):
    """Names from `supabase storage ls`: one per line, a folder with a trailing '/'."""
    names = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        folder = line.endswith("/")
        name = line.rstrip("/").rsplit("/", 1)[-1]
        if name:
            names.append(name + ("/" if folder else ""))
    return names


def marker_name(day):
    datetime.strptime(day, "%Y-%m-%d")          # a malformed day must never reach the bucket
    return f"{day}.json"


def marker_body(day, machine, code, finished_at, gate_ok):
    return {"melbourne_day": day, "machine": machine, "finished_at": finished_at,
            "code": code, "gate_ok": bool(gate_ok), "runner": "laptop (ops/laptop-runner)"}


def describe_marker(marker):
    if not isinstance(marker, dict) or not marker.get("machine"):
        return "a marker this runner cannot read"
    return (f"{marker.get('machine', '?')} finished at {marker.get('finished_at', '?')}"
            f" (code {marker.get('code', '?')})")


def lock_age_hours(lock, now):
    try:
        taken = datetime.fromisoformat(str(lock.get("taken_at")))
    except (TypeError, ValueError, AttributeError):
        return None
    if taken.tzinfo is None:
        taken = taken.replace(tzinfo=timezone.utc)
    return (now - taken).total_seconds() / 3600.0


def lock_verdict(lock, now, wait_hours):
    """(exit code, sentence) for a lock object read from the bucket, or None for no lock."""
    if lock is None:
        return 0, "free"
    holder = lock.get("holder", "?") if isinstance(lock, dict) else "?"
    age = lock_age_hours(lock, now) if isinstance(lock, dict) else None
    if age is None:
        # Unreadable is still a lock: treating it as absent is how two runs start at once.
        return HELD, f"held by {holder} (age unknown)"
    if age < wait_hours:
        return HELD, f"held by {holder} for {age:.1f} h"
    return HELD_LONG, (f"held by {holder} for {age:.1f} h, longer than {wait_hours:g} h: "
                       "probably abandoned")


# --- the CLI ---------------------------------------------------------------------------------

def supabase_exe():
    return (os.environ.get("SPB_SUPABASE_CLI") or shutil.which("supabase")
            or os.path.expanduser(r"~\bin\supabase.exe"))


def cli(app, *args, timeout=120):
    cmd = [supabase_exe(), *args, "--linked", "--experimental", "--yes"]
    try:
        return subprocess.run(cmd, cwd=str(app), stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"!! the Supabase CLI did not run: {exc.__class__.__name__}")
        return None


def ls(app, uri):
    p = cli(app, "storage", "ls", uri)
    if p is None or p.returncode != 0:
        if p is not None:
            last = (p.stderr or p.stdout or "").strip().splitlines()
            print("!! could not list " + uri + (f": {last[-1][:160]}" if last else ""))
        return None
    return parse_ls(p.stdout)


def read_json(app, uri, staging_name):
    """A small JSON object from the bucket, or None. `staging_name` is removed before and after."""
    local = Path(app) / staging_name
    local.unlink(missing_ok=True)
    try:
        p = cli(app, "storage", "cp", uri, staging_name)
        if p is None or p.returncode != 0 or not local.exists():
            return None
        try:
            return json.loads(local.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    finally:
        local.unlink(missing_ok=True)


# --- commands --------------------------------------------------------------------------------

def cmd_bucket_check(args):
    names = ls(args.app, ROOT_URI)
    if names is None:
        return 1
    if DB_NAME not in names:
        print(f"!! the bucket {BUCKET} is visible but holds no {DB_NAME}: wrong project?")
        return 1
    # Without it a fresh laptop harvests no direct portals, and nightly.ps1 refuses the deploy.
    builders = ls(args.app, f"{ROOT_URI}builders/") if "builders/" in names else []
    if builders is None:
        return 1
    if "vendors.csv" not in builders:
        print(f"!! the bucket {BUCKET} has no builders/vendors.csv: a run here would see 0 builders. "
              "Push it once from a laptop that has it: python state_sync.py push")
        return 1
    print(f"bucket {BUCKET}: {len(names)} entries, the shared database and the builder list are there")
    return 0


def cmd_day_status(args):
    name = marker_name(args.day)
    top = ls(args.app, ROOT_URI)
    if top is None:
        return UNKNOWN
    if f"{RUNS_DIR}/" not in top:
        print(f"Melbourne day {args.day}: no laptop has finished it (no marker has ever been written)")
        return 0
    names = ls(args.app, f"{ROOT_URI}{RUNS_DIR}/")
    if names is None:
        return UNKNOWN
    if name not in names:
        print(f"Melbourne day {args.day}: no laptop has finished it yet")
        return 0
    marker = read_json(args.app, f"{ROOT_URI}{RUNS_DIR}/{name}", ".laptop-run.read.json")
    print(f"Melbourne day {args.day}: already done - {describe_marker(marker)}")
    return DONE


def cmd_lock_status(args):
    top = ls(args.app, ROOT_URI)
    if top is None:
        return UNKNOWN
    lock = None
    if LOCK_NAME in top:
        lock = read_json(args.app, ROOT_URI + LOCK_NAME, ".runner-lock.read.json")
        if lock is None:
            lock = {}                    # listed but unreadable: still held
    code, text = lock_verdict(lock, datetime.now(timezone.utc), args.wait_hours)
    print(f"bucket lock: {text}")
    return code


def _code(app):
    try:
        p = subprocess.run(["git", "-C", str(app), "log", "--oneline", "-1"], capture_output=True,
                           text=True, timeout=30, stdin=subprocess.DEVNULL)
        return p.stdout.strip()[:120] or "?"
    except (OSError, subprocess.TimeoutExpired):
        return "?"


def cmd_day_mark(args):
    name = marker_name(args.day)
    machine = os.environ.get("COMPUTERNAME") or socket.gethostname()
    body = marker_body(args.day, machine, _code(args.app),
                       datetime.now().astimezone().isoformat(timespec="seconds"), args.gate_ok)
    staging = ".laptop-run.write.json"
    local = Path(args.app) / staging
    remote = f"{ROOT_URI}{RUNS_DIR}/{name}"
    try:
        local.write_text(json.dumps(body, indent=1), encoding="utf-8")
        cli(args.app, "storage", "rm", remote)       # cp will not overwrite a bucket object
        p = cli(args.app, "storage", "cp", staging, remote)
    finally:
        local.unlink(missing_ok=True)
    if p is None or p.returncode != 0:
        print(f"!! could not write the day marker {RUNS_DIR}/{name}: another laptop may run today too")
        return 1
    print(f"day marker written: {RUNS_DIR}/{name} ({machine})")
    return 0


def cmd_env_check(_args):
    """Can this Python run the harvest exactly as the runner does? Local only, changes nothing."""
    import importlib.metadata as md
    bad = []

    def line(ok, text):
        print(("PASS  " if ok else "FAIL  ") + text)
        if not ok:
            bad.append(text)

    v = sys.version_info
    line((v.major, v.minor) == PYTHON_MINOR,
         f"Python {v.major}.{v.minor}.{v.micro} (needs 3.11: on 3.13 the OCR library stops at 1.2.3)")
    store = "WindowsApps" in sys.base_prefix
    line(not store, "Python from python.org, not the Microsoft Store" + (" - it is the Store one" if store else ""))
    try:
        ocr = md.version("rapidocr-onnxruntime")
    except md.PackageNotFoundError:
        ocr = "not installed"
    line(ocr == OCR_VERSION, f"rapidocr-onnxruntime {ocr} (needs {OCR_VERSION}: another version reads "
                             "the picture price lists into different rows)")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo("Australia/Melbourne")
        line(True, "time zones: Australia/Melbourne found (tzdata)")
    except Exception as exc:                                          # noqa: BLE001
        line(False, f"time zones: Australia/Melbourne not found ({exc.__class__.__name__}) - pip install tzdata")
    try:
        import keyring
        backend = type(keyring.get_keyring()).__name__
        line("Win" in backend, f"keyring backend {backend} (Windows Credential Manager)")
    except Exception as exc:                                          # noqa: BLE001
        line(False, f"keyring: {exc.__class__.__name__}")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            version = browser.version
            browser.close()
        line(True, f"Chromium {version} starts (Playwright)")
    except Exception as exc:                                          # noqa: BLE001
        line(False, f"Chromium does not start: {str(exc).splitlines()[0][:160]} - "
                    "python -m playwright install chromium")
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", type=Path, default=Path.cwd(),
                    help="the runner's checkout (linked to the SPB Supabase project)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bucket-check").set_defaults(fn=cmd_bucket_check)
    s = sub.add_parser("day-status")
    s.add_argument("--day", required=True)
    s.set_defaults(fn=cmd_day_status)
    s = sub.add_parser("lock-status")
    s.add_argument("--wait-hours", type=float, default=8.0)
    s.set_defaults(fn=cmd_lock_status)
    s = sub.add_parser("day-mark")
    s.add_argument("--day", required=True)
    s.add_argument("--gate-ok", type=int, default=1)
    s.set_defaults(fn=cmd_day_mark)
    sub.add_parser("env-check").set_defaults(fn=cmd_env_check)
    args = ap.parse_args(argv)
    args.app = Path(args.app).resolve()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
