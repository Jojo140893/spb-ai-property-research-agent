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
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_PARTIAL = []          # steps that partly succeeded (exit 2)
APP = Path(__file__).resolve().parent
# Written when a channel read nothing, so CI can fail the run AFTER it has published.
# A file rather than an exit code: the publish step is gated on the run succeeding, and
# the channels that DID read must still reach the dashboard. See _chain().
_PARTIAL_MARKER = APP / "_harvest_partial"
# The same idea for a step that failed while the run carried on -- see where it is
# written, at the end of main().
_DEGRADED_MARKER = APP / "_run_degraded"
# Portals that cannot be signed into without a human. Their saved session is the whole
# mechanism, so its age is the thing worth reporting.
INTERACTIVE_PORTALS = {"paramount": "invisible reCAPTCHA", "proxima": "2FA"}
SESSION_MAX_AGE_DAYS = 14


# Optional steps that FAILED rather than being skipped. Reported at the end so a
# partially-degraded run is never announced as a clean one.
_DEGRADED: list = []


def _run(label: str, args: list, optional: bool = False) -> bool:
    print(f"\n{'=' * 70}\n  {label}\n{'=' * 70}", flush=True)
    if not (APP / args[0]).is_file():
        # Named in the chain but not written yet. Say so, rather than let Python's
        # "No such file" look like a crash. (benchmark_buildings.py was the live
        # example until 2026-08-01; nothing in the chain is missing now.)
        print(f"  -> NOT BUILT YET ({args[0]} does not exist) — step skipped", flush=True)
        return optional
    started = time.time()
    # A step that hangs must not hold the run open until Task Scheduler kills it hours
    # later, silently costing a whole day's refresh. Generous, because a full harvest is
    # legitimately slow; override with SPB_STEP_TIMEOUT_S.
    limit = int(os.environ.get("SPB_STEP_TIMEOUT_S", "5400"))
    try:
        proc = subprocess.run([sys.executable, "-X", "utf8", *args], cwd=str(APP),
                              timeout=limit)
        # 2 means PARTIAL — some channels failed, others read normally. The chain
        # continues so the channels that DID refresh reach the dashboard; the failure is
        # still reported and still ends the run non-zero.
        ok = proc.returncode in (0, 2, 3)
        if proc.returncode == 3:
            # 3 means FINISHED ON ITS FALLBACK PATH: a step's new pass raised, the step
            # logged it, kept today's behaviour and wrote that. The chain must continue
            # (it is often a REQUIRED step, and aborting would withhold the whole night's
            # refresh over one pass), but the run is marked DEGRADED so CI goes red after
            # publishing. Not 2: CI answers 2 with "a channel returned nothing, refresh
            # the portal login", which is the wrong remedy. First user:
            # supersede_duplicates.py's status pass (DEGRADED_EXIT).
            _DEGRADED.append(label + " (a new pass failed; fell back to the previous rule)")
        if proc.returncode == 2:
            _PARTIAL.append(label)
            # Also feed the EXISTING degraded channel, so the "finished but something
            # failed" email fires. That mechanism was written for exactly this shape —
            # a run nobody looks at because it completed — and a second parallel one
            # would just be a second thing to forget to wire up.
            _DEGRADED.append(label + " (some channels returned nothing)")
    except subprocess.TimeoutExpired:
        ok = False
        print(f"  -> TIMED OUT after {limit}s", flush=True)

    # An optional step that CRASHED is not the same as one that was skipped. Printing
    # "skipped" for a crash is how a broken benchmark step read as success for the whole
    # chain: the run carried on to export, build and deploy, and reported "Data
    # refreshed" while benchmark_median and friends went stale in the public snapshot.
    # The chain still continues — that is what optional means — but it says what
    # happened, and main() reports it at the end.
    if ok:
        mark = "ok"
    elif optional:
        mark = "FAILED (optional — the chain continues, but this did not run)"
    else:
        mark = "FAILED"
    print(f"  -> {mark} in {time.time() - started:.0f}s", flush=True)
    if not ok and optional:
        _DEGRADED.append(label)
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


def _log_tail(lines: int = 40) -> str:
    """The end of the run's log, for the alert body.

    Both _alert calls below already ended in `+ tail`, and `tail` was never assigned
    anywhere in this file — so every alert path raised NameError before sending, and the
    alerting added to stop failures going unnoticed went unnoticed itself. The 7 Aug run
    hit it: aborted at step 1/7 with Proxima returning 0 rows, and nobody was told.

    Task Scheduler redirects stdout to _daily.log; SPB_DAILY_LOG overrides. If neither is
    readable the alert still goes out, saying so, because an alert with no log attached is
    worth far more than no alert.
    """
    path = Path(os.environ.get("SPB_DAILY_LOG") or (APP / "_daily.log"))
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:                                            # noqa: BLE001
        return f"(could not read {path}: {exc})"
    return "\n".join(text.splitlines()[-lines:]) or "(the log is empty)"


def _benchmark_basis_report() -> None:
    """Say, every night, whether the benchmark can actually reach a market source.

    Without a provider the benchmark still runs and still writes numbers -- against
    OTHER STOCK WE HOLD. That is a real signal and it is not a market benchmark, which
    is the distinction Coleen raised on 3 Aug. The run has to state which basis it used,
    or a stack of internal medians quietly reads as the thing she asked for.
    """
    header = "=" * 70
    print(chr(10) + header + chr(10) + "  Market comparables" + chr(10) + header)
    try:
        from comp_provider import provider_status
        st = provider_status()
    except Exception as exc:                                          # noqa: BLE001
        print(f"  could not determine provider status: {exc}")
        return
    if st["live"]:
        print(f"  ok     provider '{st['provider']}' is configured — market basis available")
    else:
        print("  NONE   no market source is configured, so every benchmark tonight is")
        print("         INTERNAL: this lot against other stock we hold, not against the")
        print("         market. Nothing published will claim otherwise.")
        print(f"         {st['reason']}")


def _alert(subject: str, body: str) -> None:
    """Tell someone the run failed, rather than leaving it in a log nobody opens.

    The whole point of the step-by-step output is to say WHICH step failed, and until now
    it went to _daily.log and stopped there. That is the reason Proxima was dead for three
    nights without anyone knowing: the harvest reported it, and the report had no reader.

    Uses the inbox credentials the email sweep already relies on, so no new secret is
    introduced. If none are stored the alert is skipped and SAYS so — silently swallowing
    the alert would recreate the exact problem this exists to fix.
    """
    to = os.environ.get("SPB_ALERT_TO", "").strip()
    if not to:
        print("  [alert] SPB_ALERT_TO is not set — nobody was notified of this run.",
              flush=True)
        return
    try:
        import smtplib
        from email.message import EmailMessage

        from secrets_store import get_credentials
        user, password, _src = get_credentials("email_inbox")
        if not (user and password):
            print("  [alert] no inbox credentials stored — nobody was notified.", flush=True)
            return
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = to
        msg.set_content(body)
        host = os.environ.get("SPB_SMTP_HOST", "smtp.gmail.com")
        port = int(os.environ.get("SPB_SMTP_PORT", "465"))
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
        print(f"  [alert] sent to {to}", flush=True)
    except Exception as exc:                                          # noqa: BLE001
        # An alert that fails must be loud in the log, not silent — otherwise the
        # notification channel becomes the next thing that dies unnoticed.
        print(f"  [alert] COULD NOT NOTIFY {to}: {exc}", flush=True)



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-deploy", action="store_true", help="refresh data, skip publishing")
    ap.add_argument("--email-only", action="store_true", help="inbox sweep only (no browser)")
    ap.add_argument("--email-days", type=int, default=7,
                    help="how far back to sweep the inbox on a daily run (default 7)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--state-sync", action="store_true",
                    help="pull the database from Supabase first and push it back after")
    args = ap.parse_args()

    # SHARED STATE IS OPT-IN, AND OFF BY DEFAULT ON THIS MACHINE.
    #
    # A runner elsewhere must pull the database before it can harvest anything — it has
    # no copy of its own. But on Ahsan's laptop the LOCAL file is the source of truth
    # today, and pulling on every local run would overwrite it with whatever the bucket
    # last received. Turning that on silently is how a good local database gets replaced
    # by a stale one. So the runner sets SPB_STATE_SYNC=1 (or --state-sync) and says so.
    sync = args.state_sync or os.environ.get(
        "SPB_STATE_SYNC", "").strip().lower() in ("1", "true", "yes", "on")

    if sync and not args.dry_run:
        if not _run("0/8  Pull the shared database from Supabase",
                    ["state_sync.py", "pull"], optional=False):
            print("")
            print("[ABORT] could not pull the shared database. Harvesting now would")
            print("        either start from an empty file or fight another runner for")
            print("        the lock — both worse than not running.")
            return 1

    try:
        return _chain(args)
    finally:
        # ALWAYS, even when the chain failed or raised. Rows already harvested are worth
        # keeping, and more importantly the push is what RELEASES THE LOCK — leaving it
        # held would block every future run until someone breaks it by hand at 4am.
        # Optional, so a push failure cannot turn a good run into a reported failure;
        # state_sync itself refuses to push a 0-row database over a real one.
        if sync and not args.dry_run:
            _run("8/8  Push the database back to Supabase",
                 ["state_sync.py", "push"], optional=True)


def _chain(args) -> int:
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
        # AFTER superseding, because it finds a class supersede_duplicates cannot.
        #
        # `suburb_norm` is one of the eight fields in content_hash, so the day an extractor
        # starts reading a suburb the affected rows acquire a NEW identity: the harvest
        # inserts the improved row and the old suburb-less one is never matched again, so
        # it is never updated and never superseded. It sits there live -- a second copy of
        # a property Coleen can sell once, and the copy without a location.
        #
        # This is not hypothetical and it is not rare. Every extractor fix in this codebase
        # creates a fresh batch: AVIA Homes carried 191 live rows against 103 in the
        # current harvest, the other 88 being captures from five earlier days, some with
        # "Estate Brochure" sitting in the suburb column. repair_rows has had the repair
        # for it since it was written and nothing ever ran it.
        ("4/7  Repair rows older extractors wrote", ["repair_rows.py", "--apply"], True),
        # AFTER superseding, so it only reads documents that still have live rows, and
        # BEFORE the snapshot, so today's answers are in what gets published. Optional:
        # it makes network calls to third-party file hosts, and a Dropbox link that has
        # been rotated must not take the whole run down — the previous map stays in place
        # and the affected listings simply keep the suburb they already had.
        ("4/7  Read the suburb off orphan source documents",
         ["document_suburb.py"], True),
        # OPEN THE SHEETS AND CHECK WHAT WE SAY ABOUT THEM IS TRUE.
        #
        # Ahsan, 9 Sep: "i want you to confirm all the listings are present and when i
        # open the sheet they are at exact number that is written and if they are not
        # then mark them not available automatically". Every other step here reasons
        # about the database; this one downloads each price list and compares.
        #
        # AFTER superseding and the repairs, so it only opens documents that still have
        # live rows to check, and BEFORE the snapshot, so today's answers are in what
        # Coleen sees. Optional for the same reason document_suburb.py is: it depends on
        # third-party file hosts, and a rotated Dropbox link must not take the run down --
        # it makes no claim about a document it could not read, so a failed fetch costs
        # today's check and nothing else.
        # --save every night, not only when someone remembers to ask. The first full pass
        # returned 666 listings absent from their own sheet, and "should 628 properties be
        # hidden" is not a question anyone can answer from a summary line the next morning.
        # The report says which document each verdict came from, so last night's run can
        # be compared with tonight's -- the only way to tell a builder selling stock from
        # our reading of one file quietly getting worse.
        # REPORT-ONLY FOR NOW, and bounded. Two deliberate restraints:
        #
        # NO --apply. The writes are not as reversible as this script's own docstring
        # claims. It justifies acting on a single sighting because "the next harvest that
        # finds the lot writes its availability back" -- but database.record_building
        # writes availability only when the fresh read SUPPLIED one, and 1,951 of the
        # 6,730 rows in scope come from sources that never state it. On the last real run
        # 94 rows were marked Not Available and 21 of them had no previous status to
        # restore, so nothing would ever put those back. Read data/sheet_check.json, then
        # apply what it says with:
        #     python verify_sheet_positions.py --apply --from-report data/sheet_check.json
        #
        # --limit BOUNDS THE COST. Unbounded, the step opens all 491 documents serially
        # with no wall-clock budget -- about an hour on top of a harvest that has taken
        # 65-69 minutes on every recent run. Known limitation, stated rather than hidden:
        # --limit takes the N LARGEST documents each night, so the same 120 are checked
        # every time and the rest never are. A wall-clock budget that rotates is the
        # follow-up; main() already reports a "not reached in this run" bucket for it.
        ("4/7  Check each listing against its own sheet",
         ["verify_sheet_positions.py", "--limit", "120",
          "--save", "data/sheet_check.json"],
         True),
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
    _benchmark_basis_report()
    failed = []
    _PARTIAL.clear()
    # Clear last night's marker before the run, not after: left behind, it would fail a
    # perfectly healthy run and train everyone to ignore the signal — which is the same
    # way the [FAILED] line came to be ignored for three nights.
    _PARTIAL_MARKER.unlink(missing_ok=True)
    _DEGRADED_MARKER.unlink(missing_ok=True)
    for label, cmd, optional in steps:
        if not _run(label, cmd, optional):
            failed.append(label)
            # A later step over half-refreshed data is worse than stopping: the export
            # and the deployed snapshot would publish a partial harvest as if complete.
            if not optional:
                print(f"\n[ABORT] {label} failed — not exporting or deploying a partial run.")
                break

    print(f"\n{'=' * 70}")
    if _PARTIAL:
        # Said before the failure line, because this is the case where the run kept going:
        # some channels failed, the rest refreshed and WERE published. Reporting it as a
        # flat failure would train everyone to ignore the word.
        print(f"  PARTIAL: {', '.join(_PARTIAL)} — one or more channels returned nothing.")
        print("  The channels that did read were refreshed and published; fix the failing")
        print("  one and its rows catch up on the next run. See the [FAILED] lines above.")
        # AND TELL SOMEONE. Until 17 Aug this printed and nothing else: no alert, no
        # non-zero exit, no marker. E-Agent's sign-in broke on 13 Aug and the next three
        # nightly runs printed "[FAILED] ... E-Agent: read 0 tonight" and reported SUCCESS
        # to GitHub — three green ticks over the largest channel, ~4,500 listings, going
        # stale. Coleen found it when a consultant could not find stock he was looking at
        # in E-Agent. A degraded step already alerted; a dead CHANNEL, which is worse, did
        # not.
        _alert("SPB harvest PARTIAL — a channel returned nothing",
               "These channels read nothing tonight:\n\n  - "
               + "\n  - ".join(_PARTIAL)
               + "\n\nTheir stored rows are now going stale while the dashboard keeps\n"
                 "serving them as current. The usual cause is an expired sign-in:\n\n"
                 "  python portal_login.py e_agent\n"
                 "  python portal_login.py portal_proxima --profile\n\n"
                 "Last 40 lines of the log:\n\n" + _log_tail())
        # A marker the CI job can see. The run must still exit 0 here so the publish step
        # runs — the channels that did read have fresh rows and withholding them helps
        # nobody — but the job is failed AFTER publishing, so the run goes red and is
        # noticed. See "Fail the run if a channel died" in daily-harvest.yml.
        try:
            _PARTIAL_MARKER.write_text("\n".join(_PARTIAL), encoding="utf-8")
        except Exception as exc:                                       # noqa: BLE001
            print(f"  (could not write the partial marker: {exc})")
    if failed:
        print(f"  DAILY RUN FAILED at: {', '.join(failed)}")
        print(f"{'=' * 70}")
        _alert("SPB daily run FAILED — " + failed[0][:60],
               "The nightly run stopped at: " + ", ".join(failed) + "\n\n"
               "Nothing was published, so the dashboard is still serving the last good\n"
               "snapshot rather than a partial one.\n\n"
               "Last 40 lines of the log:\n\n" + _log_tail())
        return 1
    print("  Data refreshed.")
    if _DEGRADED:
        # The run finished, so nobody would otherwise look — which is precisely when a
        # degraded step goes unnoticed for days.
        _alert("SPB daily run finished DEGRADED",
               "The run completed and published, but these steps failed:\n\n  - "
               + "\n  - ".join(_DEGRADED)
               + "\n\nAnything they produce is now stale in the published snapshot.\n\n"
               + "Last 40 lines of the log:\n\n" + _log_tail())
        # AND A MARKER, for the same reason _PARTIAL has one. The email above is the only
        # other signal and it needs SPB_ALERT_TO, which is not set on the runner — a real
        # run on 12 Aug printed "nobody was notified of this run" and went green.
        #
        # Three of these steps write data the build then bundles: repair_rows.py --apply,
        # document_suburb.py and benchmark_buildings.py are all optional, so any of them
        # can die while the run still prints "Data refreshed." and exits 0.
        # document_suburb.py failing means every source document that arrived that day
        # keeps no suburb, and the coverage floor in build_web only catches that once it
        # has cost 5% of the catalogue — days later, if ever.
        #
        # Exit stays 0 so the publish step still runs: the rows that DID refresh are worth
        # publishing. The CI job is failed AFTER publishing, exactly as PARTIAL is.
        try:
            _DEGRADED_MARKER.write_text("\n".join(_DEGRADED), encoding="utf-8")
        except Exception as exc:                                       # noqa: BLE001
            print(f"  (could not write the degraded marker: {exc})")
    if args.no_deploy:
        print("  Deploy skipped (--no-deploy). To publish: deploy.ps1")
    else:
        print("  To publish: powershell -File deploy.ps1")
        print("  (deploy is left as its own step so a data failure never publishes)")
    print(f"{'=' * 70}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
