"""Save a CLEAN, PORTABLE Proxima session — one a runner elsewhere can replay.

WHY THIS EXISTS. The Proxima harvest has only ever run on Ahsan's laptop, and
PROJECT_PLAN.md recorded the reason as "2FA — can never run unattended". Measured
2026-08-09, that is not what stops it:

  * 2FA has not fired ONCE in nine days of nightly runs. Proxima issues a persistent
    `otp_authentication` cookie — a "remember this device" token with a 180-day life,
    created 1 Aug, expiring 28 Jan 2027, never re-issued since.
  * What DOES break, twice in nine days, is the Magento server session, and recovering
    from it needs SPB's own password — already in the vault, already resolvable from an
    environment variable on a headless box.
  * The 123 MB browser profile cannot leave this machine: its cookies are DPAPI-encrypted
    against this Windows account. The 81 KB `storage_state` JSON can, and it carries the
    device-trust token.
  * The claim that a storage_state gets re-challenged for 2FA was measured against two
    session files that contained NO `otp_authentication` cookie. It could only ever have
    failed. Replayed with the token present, a fresh context landed on the projects page,
    signed in, no challenge.

THE GUARD IS THE POINT OF THIS SCRIPT. Browsing Proxima while signed in NARROWS what the
next harvest can see — the known-good line is 40 projects and 9 availability views, and a
session saved while a Quick Filter is active silently exports that narrowing. A test
replay on 9 Aug rendered 8 projects for exactly this class of reason. So this script
REFUSES TO SAVE below the line, and says what it saw. A session that quietly harvests a
fifth of the stock is worse than no session, because nothing about it looks broken.

    python export_proxima_session.py            # headed; you sign in; it verifies, then saves
    python export_proxima_session.py --check    # verify the CURRENT saved file, save nothing

Run it right after a nightly harvest, not before: if something goes wrong, the run you
might disturb has already happened.
"""
import argparse
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

from sources.scraper_base import SESSION_DIR, browser_user_agent          # noqa: E402

PROJECTS_URL = "https://portal.proxima.com.au/agent/projects/index/"
SESSION_FILE = SESSION_DIR / "portal_proxima.json"

# The known-good line, from _daily.log: "Proxima: 40 project(s) listed, 9 with an
# availability view." Anything under this is a filtered session, not a smaller portal.
MIN_PROJECTS = 40
MIN_AVAILABILITY = 9

PROJECT_SEL = "label.tab-label[data-project_id]"
AVAIL_SEL = "a.availability-view-btn[data-project_id]"


def _counts(page):
    return page.locator(PROJECT_SEL).count(), page.locator(AVAIL_SEL).count()


def _report(projects, avail):
    print(f"    projects listed      : {projects:>3}   (need >= {MIN_PROJECTS})")
    print(f"    availability views   : {avail:>3}   (need >= {MIN_AVAILABILITY})")
    ok = projects >= MIN_PROJECTS and avail >= MIN_AVAILABILITY
    print(f"    -> {'CLEAN' if ok else 'NARROWED — a filter is active'}")
    return ok


def check_existing(pw, ua):
    """Replay the saved file in a fresh context — exactly what a remote runner would do."""
    if not SESSION_FILE.exists():
        print(f"!! no session file at {SESSION_FILE}")
        return 2
    print(f"replaying {SESSION_FILE.name} in a FRESH context (no profile)…")
    browser = pw.chromium.launch(headless=True)
    try:
        ctx = browser.new_context(storage_state=str(SESSION_FILE), user_agent=ua,
                                  viewport={"width": 1500, "height": 950})
        page = ctx.new_page()
        page.goto(PROJECTS_URL, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(4000)
        print(f"    landed on            : {page.url}")
        if "twofactor" in page.url:
            print("    -> bounced to 2FA: the device-trust cookie did not carry.")
            return 1
        if "/login" in page.url:
            print("    -> bounced to the password page: the server session has expired.")
            return 1
        return 0 if _report(*_counts(page)) else 1
    finally:
        browser.close()


def export(pw, ua, wait_minutes=12):
    """Waits for the CONDITION, not for a keypress.

    An `input()` prompt cannot be answered from a non-interactive shell, and it is the
    weaker design regardless: pressing Enter a moment early exports exactly the narrowed
    session this script exists to refuse. Polling for "signed in AND >= 40 projects" means
    the good state is what triggers the save, so it cannot be confirmed too soon.
    """
    print("Opening Proxima in a VISIBLE browser.")
    print("  1. Sign in (complete 2FA if it asks — it should not).")
    print("  2. Clear every Quick Filter / keyword so ALL projects are listed.")
    print(f"  3. Leave it. This saves ITSELF the moment it sees >= {MIN_PROJECTS} projects")
    print(f"     and >= {MIN_AVAILABILITY} availability views. Giving up after {wait_minutes} min.\n")
    browser = pw.chromium.launch(headless=False, args=["--start-maximized"])
    try:
        ctx = browser.new_context(user_agent=ua, viewport=None)
        page = ctx.new_page()
        page.goto(PROJECTS_URL, wait_until="domcontentloaded", timeout=90000)

        deadline = time.time() + wait_minutes * 60
        last = None
        while time.time() < deadline:
            time.sleep(5)
            try:
                url = page.url
                if "twofactor" in url:
                    state = "waiting: 2FA screen — enter the code Proxima emailed"
                elif "/login" in url or "customer/account/login" in url:
                    state = "waiting: sign-in screen"
                else:
                    p, a = _counts(page)
                    if p >= MIN_PROJECTS and a >= MIN_AVAILABILITY:
                        print(f"\n    signed in, {p} projects / {a} availability views — saving.")
                        break
                    state = (f"waiting: signed in, {p} projects / {a} views "
                             f"— need {MIN_PROJECTS}/{MIN_AVAILABILITY}, clear the filters")
            except Exception as e:                                    # noqa: BLE001
                state = f"waiting: page busy ({type(e).__name__})"
            if state != last:
                print(f"    {state}")
                last = state
        else:
            print("\n    Timed out. Nothing saved — the existing session file is untouched.")
            return 1

        page.goto(PROJECTS_URL, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(4000)
        print(f"\n    landed on            : {page.url}")
        if "twofactor" in page.url or "/login" in page.url:
            print("    -> still on a sign-in page. Nothing saved.")
            return 1

        if not _report(*_counts(page)):
            print("\n    NOT SAVED. Clear the filters and run this again.")
            print("    A narrowed session harvests a fraction of the stock and looks fine "
                  "doing it — that is the failure this refusal exists to prevent.")
            return 1

        if SESSION_FILE.exists():
            backup = SESSION_FILE.with_suffix(
                f".backup-{datetime.now():%Y%m%d-%H%M%S}.json")
            shutil.copy2(SESSION_FILE, backup)
            print(f"\n    previous session backed up to {backup.name}")
        SESSION_DIR.mkdir(exist_ok=True)
        ctx.storage_state(path=str(SESSION_FILE))
        print(f"    saved {SESSION_FILE.name}  ({SESSION_FILE.stat().st_size:,} bytes)")

        names = set()
        try:
            import json
            names = {c.get("name") for c in
                     json.loads(SESSION_FILE.read_text(encoding="utf-8")).get("cookies", [])}
        except Exception:                                             # noqa: BLE001
            pass
        got = "otp_authentication" in names
        print(f"    device-trust cookie  : {'present' if got else 'MISSING'}")
        if not got:
            print("    Without it a remote replay will be re-challenged for 2FA. Sign in")
            print("    again and tick 'remember this device' if Proxima offers it.")
            return 1

        print("\n    Verify it the way a runner will:  python export_proxima_session.py --check")
        print(f"    Then pin the user agent on that runner:\n      SPB_USER_AGENT={ua}")
        return 0
    finally:
        browser.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="verify the saved session in a fresh context; save nothing")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        # The SAME string the harvest will present. Device trust may be bound to it, so
        # signing in under one UA and harvesting under another is how an interactive
        # sign-in silently fails to stay done.
        ua = browser_user_agent(pw=pw)
        print(f"user agent: {ua}\n")
        return check_existing(pw, ua) if args.check else export(pw, ua)


if __name__ == "__main__":
    sys.exit(main())
