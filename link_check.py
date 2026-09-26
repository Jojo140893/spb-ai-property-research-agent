"""Check that each lot's own page still opens and still offers the lot, so a lot the builder
has taken down or marked sold is not recommended as if it were for sale.

Coleen, 26 Sep 2026: the #1 result of an "SA, any price" search was "Lot 1, House and Land
Packages, Adelaide" (Alete Homes via E-Agent), and its "Lot PDF -- opens this lot" link
opened Kenekt's "Sorry but we were unable to find the page you were looking for". So did
#2 and #6. Measured the same day on the live search: 4 of its 102 lot links answered 404,
all read from E-Agent on 24 Sep, and E-Agent has not been read since (its login form stopped
appearing). Kenekt deletes a package's page when the package is withdrawn, re-issued or its
land sold under another design.

This had happened before in other shapes -- dead brochure links (29 Jul), folder share
links (7 Aug), Proxima's per-project URLs (14 Aug) -- and each fix corrected one kind of
link. Nothing ever asked whether a link still opens. This does, every night, for every
current row's listing_url, which is what the card calls "Lot PDF" (provenance.py).

WHAT COUNTS AS GONE. Only HTTP 404 and 410: the site itself saying the page does not exist.
A 403, a 5xx, a timeout, a redirect to a sign-in page -- none of those says the lot is gone,
so each is "unknown" and changes nothing. NEVER GUESS A FACT applies to absence as much as
to presence.

A PAGE CAN BE UP AND SAY SOLD. Review, 26 Sep: Kenekt-family portals (Alete, Hudson,
Land.build) keep a sold or reserved package's page live with its status on it -- 1 of 120
sampled said Sold, 1 Under offer, and the live SA search showed #55, Lot 86 Narooma Rise,
Murray Bridge, "Available" on the card and "Under offer" on its own page. On those pages the
status is read (portal_property_status_build_*) and a word the search already treats as not
available (_candidates.NOT_AVAILABLE) makes the page "unavailable", which refuses the lot
exactly as a 404 does.

A WHOLE HOST GOING DARK IS NOT A SALE -- MEASURED AGAINST LAST NIGHT. If most of one host's
pages that were fine last night answer 404 tonight, the likelier story is that the host
changed its links. A first version compared the share of ALL a host's pages that were gone:
Hudson already sat at 58% from lots that had died over weeks (rows are kept until a harvest
supersedes them), so a normal fortnight of sales would have tipped it past 80% and handed
every dead Hudson link back at once, while an outage that also timed out a quarter of pages
slipped under it. Now only pages that answered, and were not already gone last night, count;
held pages are only the NEWLY gone ones; a page gone last night and still 404 stays gone.

Writes .sessions/link_status.json, which build_web.py reads (the same pattern as
portal_login.py --check and login_status.json), with each gone page's first-gone date
carried forward. Run by run_daily.py before the build; optional there, because a check that
cannot run must never withhold tonight's stock. With no file, every row is shown as before.

    python link_check.py                 # check every current row's lot page
    python link_check.py --limit 50      # a quick look at the first 50
"""
import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlsplit

import config

LINK_STATUS_PATH = config.PROJECT_ROOT / ".sessions" / "link_status.json"
GONE_STATUSES = (404, 410)
REFUSED = ("gone", "unavailable")      # verdicts that take a lot out of the search
TIMEOUT = 15                 # seconds per request
WORKERS = 6                  # polite: ~600 pages, a minute or two
RETRIES = 1                  # a connection error gets one more try; a status does not
BUDGET_SECONDS = 600         # the whole run; pages not reached are "unknown"
SECOND_PASS_PAUSE = 20       # seconds before asking the silent pages again, one at a time
HOST_FLOOR = 10
HOST_GONE_SHARE = 0.8
PAGE_BYTES = 3_000_000       # a Kenekt lot page is ~70 KB; the status sits about half way
# Never lot pages: a Gmail search opens a sign-in page for anyone but the owner, and a
# portal's front door is not the lot. provenance.py builds those; they are not listing_url
# values, but a stray one must not be judged as a lot.
SKIP_HOSTS = ("mail.google.com",)
# A Kenekt-family package page (ibportal.kenekt.app, partners.hudsonhomes.com.au,
# packages.land.build): "/property/BP-<id>/protected/<signed token>".
PORTAL_PAGE = re.compile(r"/property/BP-\d+/")
_STATUS_FRAME = re.compile(r'id="portal_property_status_build_[^"]*"[^>]*>(.*?)</turbo-frame>',
                           re.S)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")


def _today_melbourne():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Australia/Melbourne")).strftime("%d/%m/%Y")
    except Exception:                                                  # noqa: BLE001
        return datetime.now().strftime("%d/%m/%Y")


def _not_available_words():
    """The search's own list (_candidates.NOT_AVAILABLE), so the two cannot disagree."""
    try:
        api = str(config.PROJECT_ROOT / "api")
        if api not in sys.path:
            sys.path.insert(0, api)
        from _candidates import NOT_AVAILABLE
        return frozenset(NOT_AVAILABLE)
    except Exception:                                                  # noqa: BLE001
        return frozenset(("sold", "under offer", "reserved", "leased", "on hold",
                          "withdrawn", "not available", "unavailable", "settled",
                          "deposit taken", "contract pending", "off market"))


def lot_urls(db_path, limit=0):
    """Distinct http(s) listing_url values of the current (not superseded) rows."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT DISTINCT listing_url FROM buildings "
            "WHERE (superseded_by IS NULL OR superseded_by = '') "
            "AND (listing_url LIKE 'http://%' OR listing_url LIKE 'https://%') "
            "ORDER BY listing_url").fetchall()
    finally:
        conn.close()
    urls = [str(r[0]).strip() for r in rows if r[0]]
    urls = [u for u in urls if urlsplit(u).hostname not in SKIP_HOSTS]
    return urls[:limit] if limit else urls


def page_status(html):
    """The status word a Kenekt-family package page shows ("Available", "Under offer",
    "Sold"), or None when the page has no status block."""
    m = _STATUS_FRAME.search(html or "")
    if not m:
        return None
    word = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()
    return word or None


def probe(url, opener=None, timeout=TIMEOUT):
    """HTTP status, or "ErrorName: reason", for one GET with redirects followed -- or, for a
    Kenekt-family package page that answered, (status, the status word on the page)."""
    opener = opener or urllib.request.build_opener()
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    portal = bool(PORTAL_PAGE.search(urlsplit(url).path or ""))
    last = "error"
    for _attempt in range(RETRIES + 1):
        try:
            with opener.open(req, timeout=timeout) as resp:
                if not portal:
                    resp.read(2048)
                    return resp.status
                body = resp.read(PAGE_BYTES).decode("utf-8", "replace")
                return resp.status, page_status(body)
        except urllib.error.HTTPError as e:
            return e.code
        except Exception as e:                                         # noqa: BLE001
            # The reason, not just the class: "URLError" alone is what the first run
            # printed for all 101 Kenekt pages, and it cannot tell a refused connection
            # from a DNS failure from a timeout.
            last = f"{type(e).__name__}: {str(getattr(e, 'reason', e))[:80]}"
    return last


def _split(result):
    """(status, page word or None) from what a prober returned."""
    if isinstance(result, tuple):
        return result[0], (result[1] if len(result) > 1 else None)
    return result, None


def verdict(status, word=None, not_available=None):
    if status in GONE_STATUSES:
        return "gone"
    if isinstance(status, int) and 200 <= status < 400:
        words = _not_available_words() if not_available is None else not_available
        if word and word.strip().lower() in words:
            return "unavailable"
        return "ok"
    return "unknown"


def classify(results, previous=None, today=""):
    """({url: {"status", "verdict", "page_status"?, "since"?}}, {host: held-back counts}).

    `results` is {url: status or (status, word)}; `previous` is last night's "urls" map.
    A refused page carries the date it was first found gone or unavailable ("since")."""
    previous = previous if isinstance(previous, dict) else {}
    words = _not_available_words()
    out = {}
    for u, res in results.items():
        status, word = _split(res)
        r = {"status": status, "verdict": verdict(status, word, words)}
        if word:
            r["page_status"] = word
        out[u] = r
    per_host = defaultdict(list)
    for u in out:
        per_host[urlsplit(u).hostname or ""].append(u)
    held = {}
    for host, urls in per_host.items():
        # Pages that answered, and were not already gone last night. A timeout says nothing
        # about the site's links, and a page gone for a week says nothing about tonight.
        base = [u for u in urls if out[u]["verdict"] in ("ok", "gone", "unavailable")
                and (previous.get(u) or {}).get("verdict") != "gone"]
        newly = [u for u in base if out[u]["verdict"] == "gone"]
        if len(base) >= HOST_FLOOR and len(newly) / len(base) >= HOST_GONE_SHARE:
            held[host] = {"pages": len(base), "newly_gone": len(newly)}
            for u in newly:
                out[u]["verdict"] = "unknown"
                out[u]["held_back"] = "most of this host's pages that were fine last night " \
                                      "answered 404 at once"
    for u, r in out.items():
        if r["verdict"] in REFUSED:
            before = previous.get(u) or {}
            r["since"] = (before.get("since") if before.get("verdict") in REFUSED
                          and before.get("since") else today)
    return out, held


def run(urls, prober=probe, workers=WORKERS, budget=BUDGET_SECONDS, clock=time.monotonic,
        pause=time.sleep):
    """{url: status} for every url; those not reached within `budget` are 'not reached'.

    TWICE FOR ANYTHING THAT DID NOT ANSWER. The first run on 26 Sep got a connection error
    from all 101 Kenekt pages; minutes later the same pages answered 200 and 404 at the same
    concurrency. A host having a bad minute must not blind the check to the very channel
    Coleen's dead links came from, so every page that returned no status is asked again,
    one at a time, after a pause. Only a status decides anything (verdict), so this can only
    turn "unknown" into an answer, never invent one.
    """
    started = clock()
    results = {}

    def one(url):
        if clock() - started > budget:
            return url, "not reached"
        return url, prober(url)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for url, status in pool.map(one, urls):
            results[url] = status
    again = [u for u, s in results.items()
             if not isinstance(_split(s)[0], int) and s != "not reached"]
    if again:
        pause(SECOND_PASS_PAUSE)
        for url in again:
            if clock() - started > budget:
                break
            results[url] = prober(url)
    return results


def _previous():
    try:
        data = json.loads(LINK_STATUS_PATH.read_text(encoding="utf-8"))
        urls = data.get("urls") if isinstance(data, dict) else None
        return urls if isinstance(urls, dict) else {}
    except (OSError, ValueError):
        return {}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    urls = lot_urls(config.DATABASE_PATH, args.limit)
    started = time.monotonic()
    today = _today_melbourne()
    checked, held = classify(run(urls), _previous(), today)
    counts = Counter(r["verdict"] for r in checked.values())
    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": today,
        "summary": {"pages": len(urls), **dict(counts), "held_back_hosts": held},
        "urls": checked,
    }
    LINK_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LINK_STATUS_PATH.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    print(f"Lot pages checked: {len(urls)} in {time.monotonic() - started:.0f}s -- "
          f"{counts.get('ok', 0)} open, {counts.get('gone', 0)} gone (404/410), "
          f"{counts.get('unavailable', 0)} saying sold/under offer/on hold, "
          f"{counts.get('unknown', 0)} could not be judged")
    for host, h in sorted(held.items()):
        print(f"  HELD BACK {host}: {h['newly_gone']} of {h['pages']} pages that were fine "
              "last night answered 404 at once; treated as a changed site, not sales")
    refused = Counter((urlsplit(u).hostname, r["verdict"]) for u, r in checked.items()
                      if r["verdict"] in REFUSED)
    for (host, why), n in refused.most_common(8):
        print(f"  {why} on {host}: {n}")
    print(f"Wrote {LINK_STATUS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
