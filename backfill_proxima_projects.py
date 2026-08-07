"""
Give the 1,212 stored Proxima listings a link to the project page they live on.

    python portal_login.py portal_proxima --profile     # you type the OTP, once
    python backfill_proxima_projects.py                 # report only
    python backfill_proxima_projects.py --apply

Colin, 5 Aug 2026, after eight minutes of hunting one Rouse Hill lot: *"The idea is to
avoid all that extra... If there's a URL that I can click."* Proxima's only stored
source is the agent projects index, which is no help at all when you want one lot.

The project id has always been readable — the harvest matches lots by a per-project
class — but it was never stored, so existing rows cannot build a deep link. Rather than
re-harvest everything, this reads the projects page once and maps each project TITLE to
its id. The title is already on every row in `estate_name`, e.g.

    "Atchison and Kenny Wollongong Building A (2/305)"

so the join needs nothing new. A row whose title does not match exactly is left alone —
the projects list is a truthful fallback and a wrong project link is worse than a coarse
one, because it sends a consultant to someone else's stock.

Needs an authenticated session: Proxima is behind a login with 2FA, and this script
never types a password. Sign in first with portal_login.py --profile.
"""

import argparse
import re
import sqlite3
import sys

import config
from sources.proxima import PROJECTS_URL
from sources.scraper_base import PlaywrightScraper, PLAYWRIGHT_AVAILABLE, LOGGED_IN_JS

# Every project accordion carries both its id and its title.
READ_PROJECTS = """
() => [...document.querySelectorAll('label.tab-label[data-project_id]')].map(el => ({
    id: el.getAttribute('data-project_id'),
    title: (el.innerText || '').trim(),
}))
"""


def _key(title):
    """Match on the title with its trailing "(available/total)" counter removed.

    The counter moves as lots sell and come back — "(2/305)" becomes "(3/305)" — so
    joining on the raw string would stop matching a week later. Rows harvested since
    sources/proxima.py started splitting the counter off have no counter to remove;
    stripping is what lets one key match both those and the older rows.
    """
    text = re.sub(r"\(\s*\d+\s*/\s*\d+\s*\)\s*$", "", str(title or "")).strip()
    return re.sub(r"\s+", " ", text).lower()


def read_projects():
    if not PLAYWRIGHT_AVAILABLE:
        print("[abort] Playwright is not installed.")
        return None
    # read_only: this must never write back a session. A half-authenticated state saved
    # over a good one is how a working sign-in got destroyed once before.
    scraper = PlaywrightScraper(session_name="portal_proxima", read_only=True)
    if not (scraper.profile_dir.exists() or scraper.session_file.exists()):
        print("[abort] no saved Proxima sign-in. Run once, in a real browser:\n"
              "        python portal_login.py portal_proxima --profile")
        return None
    found = []
    with scraper.session():
        scraper.goto(PROJECTS_URL)
        scraper.page.wait_for_timeout(4000)
        if "twofactor" in scraper.page.url or "/login" in scraper.page.url or \
                not scraper.page.evaluate(LOGGED_IN_JS):
            print("[abort] Proxima bounced to %s — the saved sign-in has expired.\n"
                  "        python portal_login.py portal_proxima --profile"
                  % scraper.page.url)
            return None
        found = scraper.page.evaluate(READ_PROJECTS) or []
    return {_key(p["title"]): str(p["id"]) for p in found
            if str(p.get("id") or "").isdigit() and _key(p.get("title"))}


def main(apply_changes):
    by_title = read_projects()
    if not by_title:
        return 1
    print(f"read {len(by_title)} project(s) from Proxima\n")

    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, estate_name, source_project_id FROM buildings "
        "WHERE source_channel = 'Proxima'")]

    matched, already, unmatched = [], 0, set()
    for r in rows:
        if str(r["source_project_id"] or "").strip():
            already += 1
            continue
        pid = by_title.get(_key(r["estate_name"]))
        if pid:
            matched.append((r["id"], pid))
        elif str(r["estate_name"] or "").strip():
            unmatched.add(str(r["estate_name"]).strip())

    print(f"Proxima rows: {len(rows)}")
    print(f"  already have an id : {already}")
    print(f"  can be matched now : {len(matched)}")
    print(f"  no matching project: {len(rows) - already - len(matched)} "
          f"across {len(unmatched)} distinct title(s)")
    for title in sorted(unmatched)[:6]:
        print(f"     unmatched: {title[:80]}")

    if not apply_changes:
        print("\n(report only — pass --apply to write the project ids)")
        return 0

    cur = conn.cursor()
    for row_id, pid in matched:
        cur.execute("UPDATE buildings SET source_project_id=? WHERE id=?", (pid, row_id))
    conn.commit()
    print(f"\napplied: {len(matched)} row(s) can now link to their project page. "
          f"Run deploy.ps1 to publish.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the project ids")
    sys.exit(main(ap.parse_args().apply))
