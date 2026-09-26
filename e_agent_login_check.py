"""Run ONLY the E-Agent login, exactly as the nightly harvest does, and say what happened.

26 Sep 2026: after the login-form fix, the nightly reached E-Agent's email form and
submitted the credentials, then logged "signed in but no stocklist files are reachable" --
and nothing else, so the reason could not be seen. Finding it by re-running the 85-minute
harvest costs a night per guess. This takes about three minutes on the same GitHub runner
(e-agent-login-check.yml, started by hand), and _record_login_failure leaves a screenshot
and the page's HTML in logs/ -- every input blanked first -- for the workflow to upload.

A fresh browser with no saved session, and read_only: nothing is written back, so the
nightly's own session handling is untouched whatever this finds.

    python e_agent_login_check.py        # exit 0 = signed in and stocklists reachable
"""
import logging
import sys

from sources.e_agent import EAgentSource
from sources.scraper_base import PlaywrightScraper

SESSION_NAME = "e_agent_login_check"      # no .sessions file or profile by this name


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    source = EAgentSource()
    if not (source.username and source.password):
        print("E-Agent login check: no credentials (E_AGENT_USERNAME / E_AGENT_PASSWORD).")
        return 2
    scraper = PlaywrightScraper(SESSION_NAME, read_only=True)
    with scraper.session():
        ok = source._login(scraper)
        files = len(source._stocklist_links(scraper)) if ok else 0
    print(f"E-Agent login check: {'SIGNED IN' if ok else 'FAILED'} -- "
          f"{files} stocklist file(s) reachable")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
