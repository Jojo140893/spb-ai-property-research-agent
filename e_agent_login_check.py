"""Run ONLY the E-Agent login, exactly as the nightly harvest does, and say what happened --
then say what the members' pages hold.

26 Sep 2026: after the login-form fix, the nightly reached E-Agent's email form and
submitted the credentials, then logged "signed in but no stocklist files are reachable" --
and nothing else. Finding the reason by re-running the 85-minute harvest costs a night per
guess. This takes about three minutes on the same GitHub runner (e-agent-login-check.yml,
started by hand). Its first run showed the login had WORKED and /access-projects had become
a hub whose ACCESS LISTINGS button leads to a members-only page; _goto_listings now follows
it. So after the login this also surveys the pages the stock can live on -- the hub, where
ACCESS LISTINGS leads, and the first category pages -- and writes each one's HTML and
screenshot to logs/ (every input blanked first) for the workflow to upload. The log names
only hosts and link texts, never a file's URL: stocklist links are capabilities.

A fresh browser with no saved session, and read_only: nothing is written back, so the
nightly's own session handling is untouched whatever this finds.

    python e_agent_login_check.py        # exit 0 = signed in and stocklists reachable
"""
import logging
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import config
from sources.e_agent import MEMBERS_DIALOG, EAgentSource
from sources.scraper_base import PlaywrightScraper

SESSION_NAME = "e_agent_login_check"      # no .sessions file or profile by this name
SURVEY_CATEGORY_PAGES = 4

_BLANK_INPUTS = ("() => document.querySelectorAll('input, textarea').forEach("
                 "e => { try { e.value = ''; e.setAttribute('value', ''); } catch (x) {} })")


def survey(source, scraper, out_dir):
    """Print what each page the stock could live on holds; keep a copy of each."""
    page = scraper.page
    targets = [("hub (listings_url)", source.cfg.listings_url)]
    try:
        button = page.get_by_role("link", name=source._ACCESS_LISTINGS)
        scraper.goto(source.cfg.listings_url)
        href = button.first.get_attribute("href") if button.count() else None
        if href:
            targets.append(("ACCESS LISTINGS", href))
    except Exception as e:                                             # noqa: BLE001
        print(f"  (could not read the ACCESS LISTINGS button: {e})")
    for c in list(source.cfg.category_pages or ())[:SURVEY_CATEGORY_PAGES]:
        targets.append((f"category {c.state} {c.product_type}", c.url))
    out_dir.mkdir(parents=True, exist_ok=True)
    for n, (label, url) in enumerate(targets, 1):
        try:
            scraper.goto(url)
            page.wait_for_timeout(3000)
            files = source._stocklist_links(scraper)
            dialog = page.locator(MEMBERS_DIALOG)
            walled = bool(dialog.count() and dialog.is_visible())
            anchors = page.eval_on_selector_all(
                "a[href]", "els => els.map(e => [e.innerText.trim().slice(0, 40), e.href])")
            hosts = Counter(urlsplit(h).hostname or "" for _t, h in anchors)
            texts = sorted({t for t, _h in anchors if t})[:25]
            print(f"  [{n}] {label}: landed {page.url} | title {page.title()!r} | "
                  f"members wall {'UP' if walled else 'down'} | stocklist files {len(files)}")
            print(f"      link hosts: {dict(hosts.most_common(8))}")
            print(f"      link texts: {texts}")
            page.evaluate(_BLANK_INPUTS)
            page.screenshot(path=str(out_dir / f"e_agent_page_{n}.png"), full_page=True)
            (out_dir / f"e_agent_page_{n}.html").write_text(page.content(), encoding="utf-8")
        except Exception as e:                                         # noqa: BLE001
            print(f"  [{n}] {label}: could not survey {url}: {e}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    source = EAgentSource()
    if not (source.username and source.password):
        print("E-Agent login check: no credentials (E_AGENT_USERNAME / E_AGENT_PASSWORD).")
        return 2
    scraper = PlaywrightScraper(SESSION_NAME, read_only=True)
    with scraper.session():
        ok = source._login(scraper)
        files = len(source._goto_listings(scraper)) if ok else 0
        print(f"E-Agent login check: {'SIGNED IN' if ok else 'FAILED'} -- "
              f"{files} stocklist file(s) reachable")
        print("Survey of the pages the stock can live on:")
        survey(source, scraper, Path(config.PROJECT_ROOT) / "logs")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
