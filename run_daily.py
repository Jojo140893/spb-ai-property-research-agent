"""
The daily run. One command, every step, in the only order that is correct.

Coleen asked on 29 and 30 July for the stock to refresh itself daily. Until now every
harvest was run by hand, so the dashboard was only ever as fresh as the last time someone
remembered.

    python run_daily.py                 # the whole chain
    python run_daily.py --no-deploy     # refresh the data, do not publish
    python run_daily.py --email-only    # just sweep the inbox (fast, no browser)
    python run_daily.py --dry-run       # print the plan and exit

ORDER MATTERS, and each dependency below is a bug that was actually hit:

  1. harvest      — E-Agent + direct portals + the digital@ inbox
  2. enrich       — state, suburb, builder. Runs AFTER the harvest because it resolves a
                    file-level hint against the rows that file produced, so it needs them
                    all present. Re-resolves hint-based states rather than only filling
                    blanks, which is the only way a wrong one gets corrected.
  3. specs        — bed/bath/car out of each row's own text
  4. supersede    — mark older captures of a lot that is also stored fresher. Runs
                    before the benchmark because a superseded row holds the price
                    advertised at its capture date, and those drag medians.
  5. benchmark    — needs the whole set present to compute a median to compare against
  6. export       — CSV + Excel for Coleen
  7. build+deploy — the snapshot the deployed app reads

WHAT THIS CANNOT DO UNATTENDED, stated plainly rather than left to fail at 3am:
Paramount's login carries an invisible reCAPTCHA and Proxima enforces 2FA. Neither can be
automated, and neither should be. They rely on a browser session saved by
`python portal_login.py --profile <portal>`, which a person refreshes when it expires —
the run reports which sessions are stale rather than pretending it scraped them.

Exit code is non-zero if a step fails, so Task Scheduler shows the run as failed.
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

APP = Path(__file__).resolve().parent
# Portals that cannot be signed into without a human. Their saved session is the whole
# mechanism, so its age is the thing worth reporting.
INTERACTIVE_PORTALS = {"paramount": "invisible reCAPTCHA", "proxima": "2FA"}
SESSION_MAX_AGE_DAYS = 14


def _run(label: str, args: list, optional: bool = False) -> bool:
    print(f"\n{'=' * 70}\n  {label}\n{'=' * 70}", flush=True)
    if not (APP / args[0]).is_file():
        # Named in the chain but not written yet. Say so, rather than let Python's
        # "No such file" look like a crash. (benchmark_buildings.py was the live
        # example until 2026-08-01; nothing in the chain is missing now.)
        print(f"  -> NOT BUILT YET ({args[0]} does not exist) — step skipped", flush=True)
        return optional
    started = time.time()
    proc = subprocess.run([sys.executable, "-X", "utf8", *args], cwd=str(APP))
    ok = proc.returncode == 0
    mark = "ok" if ok else ("skipped" if optional else "FAILED")
    print(f"  -> {mark} in {time.time() - started:.0f}s", flush=True)
    return ok or optional


def _session_report() -> None:
    """Say which human-only logins have gone stale, before the harvest silently yields 0."""
    sessions = APP / ".sessions"
    print(f"\n{'=' * 70}\n  Saved portal sessions\n{'=' * 70}")
    if not sessions.is_dir():
        print("  none saved — run: python portal_login.py")
        return
    cutoff = datetime.now() - timedelta(days=SESSION_MAX_AGE_DAYS)
    for f in sorted(sessions.glob("*.json")):
        age = datetime.fromtimestamp(f.stat().st_mtime)
        stale = age < cutoff
        why = next((r for k, r in INTERACTIVE_PORTALS.items() if k in f.stem.lower()), "")
        note = f"  <- {why}: only a person can refresh this" if (stale and why) else ""
        print(f"  {'STALE' if stale else 'ok   '}  {f.stem:<28} {age:%d %b %H:%M}{note}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-deploy", action="store_true", help="refresh data, skip publishing")
    ap.add_argument("--email-only", action="store_true", help="inbox sweep only (no browser)")
    ap.add_argument("--email-days", type=int, default=7,
                    help="how far back to sweep the inbox on a daily run (default 7)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    harvest = ["harvest_buildings.py", "--email-days", str(args.email_days)]
    if args.email_only:
        harvest.append("--email-only")
    steps = [
        ("1/7  Harvest stock (E-Agent + portals + digital email)", harvest, False),
        ("2/7  Enrich: state, suburb, builder", ["enrich_buildings.py"], False),
        ("3/7  Recover bed / bath / car", ["recover_specs.py", "--apply"], False),
        # BEFORE the benchmark, not after: a superseded row carries the price that was
        # advertised at its capture date, and leaving those in the peer pool drags every
        # median toward stale numbers.
        ("4/7  Supersede older captures of the same lot", ["supersede_duplicates.py"], False),
        ("5/7  Benchmark against comparable stock", ["benchmark_buildings.py"], True),
        ("6/7  Export CSV + Excel", ["export_csv.py"], False),
        ("6/7  Export Excel workbook", ["export_excel.py"], False),
        ("7/7  Build the deployed snapshot", ["build_web.py"], False),
    ]

    print(f"SPB daily run — {datetime.now():%a %d %b %Y %H:%M}")
    if args.dry_run:
        for label, cmd, optional in steps:
            print(f"  {label}: python {' '.join(cmd)}{'  (optional)' if optional else ''}")
        print("  then: deploy.ps1" if not args.no_deploy else "  deploy: skipped")
        return 0

    _session_report()
    failed = []
    for label, cmd, optional in steps:
        if not _run(label, cmd, optional):
            failed.append(label)
            # A later step over half-refreshed data is worse than stopping: the export
            # and the deployed snapshot would publish a partial harvest as if complete.
            if not optional:
                print(f"\n[ABORT] {label} failed — not exporting or deploying a partial run.")
                break

    print(f"\n{'=' * 70}")
    if failed:
        print(f"  DAILY RUN FAILED at: {', '.join(failed)}")
        print(f"{'=' * 70}")
        return 1
    print("  Data refreshed.")
    if args.no_deploy:
        print("  Deploy skipped (--no-deploy). To publish: deploy.ps1")
    else:
        print("  To publish: powershell -File deploy.ps1")
        print("  (deploy is left as its own step so a data failure never publishes)")
    print(f"{'=' * 70}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
