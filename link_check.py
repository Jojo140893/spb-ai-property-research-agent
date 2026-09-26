"""Check that each lot's own page still opens, so a lot the builder has taken down is not
recommended as if it were for sale.

Coleen, 26 Sep 2026: the #1 result of an "SA, any price" search was "Lot 1, House and Land
Packages, Adelaide" (Alete Homes via E-Agent), and its "Lot PDF -- opens this lot" link
opened Kenekt's "Sorry but we were unable to find the page you were looking for". So did
#2 and #6. Measured the same day on the live search: 4 of its 102 lot links answered 404,
all read from E-Agent on 24 Sep, and E-Agent has not been read since (its login form stopped
appearing). Kenekt removes a lot's page when the lot is withdrawn or sold, so a lot whose
page has gone is at best "confirm before you say anything" and at worst sold.

This had happened before in other shapes -- dead brochure links (29 Jul), folder share
links (7 Aug), Proxima's per-project URLs (14 Aug) -- and each fix corrected one kind of
link. Nothing ever asked whether a link still opens. This does, every night, for every
current row's listing_url, which is what the card calls "Lot PDF" (provenance.py).

WHAT COUNTS AS GONE. Only HTTP 404 and 410: the site itself saying the page does not exist.
A 403, a 5xx, a timeout, a redirect to a sign-in page -- none of those says the lot is gone,
so each is "unknown" and changes nothing. NEVER GUESS A FACT applies to absence as much as
to presence.

A WHOLE HOST GOING DARK IS NOT A SALE. If most of one host's pages answer 404 at once, the
likelier story is that the host changed its links, not that every lot on it sold overnight.
Past HOST_GONE_SHARE of at least HOST_FLOOR pages, that host's 404s are recorded as
"unknown" and the run says so, rather than emptying a builder from every search.

Writes .sessions/link_status.json, which build_web.py reads (the same pattern as
portal_login.py --check and login_status.json). Run by run_daily.py before the build;
optional there, because a check that cannot run must never withhold tonight's stock. With
no file, every row is shown exactly as before.

    python link_check.py                 # check every current row's lot page
    python link_check.py --limit 50      # a quick look at the first 50
"""
import argparse
import json
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
TIMEOUT = 15                 # seconds per request
WORKERS = 6                  # polite: ~600 pages, a minute or two
RETRIES = 1                  # a connection error gets one more try; a status does not
BUDGET_SECONDS = 600         # the whole run; pages not reached are "unknown"
SECOND_PASS_PAUSE = 20       # seconds before asking the silent pages again, one at a time
HOST_FLOOR = 10
HOST_GONE_SHARE = 0.8
# Never lot pages: a Gmail search opens a sign-in page for anyone but the owner, and a
# portal's front door is not the lot. provenance.py builds those; they are not listing_url
# values, but a stray one must not be judged as a lot.
SKIP_HOSTS = ("mail.google.com",)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")


def _today_melbourne():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Australia/Melbourne")).strftime("%d/%m/%Y")
    except Exception:                                                  # noqa: BLE001
        return datetime.now().strftime("%d/%m/%Y")


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


def probe(url, opener=None, timeout=TIMEOUT):
    """(HTTP status, or "ErrorName: reason") for one GET, redirects followed."""
    opener = opener or urllib.request.build_opener()
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    last = "error"
    for _attempt in range(RETRIES + 1):
        try:
            with opener.open(req, timeout=timeout) as resp:
                resp.read(2048)
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception as e:                                         # noqa: BLE001
            # The reason, not just the class: "URLError" alone is what the first run
            # printed for all 101 Kenekt pages, and it cannot tell a refused connection
            # from a DNS failure from a timeout.
            last = f"{type(e).__name__}: {str(getattr(e, 'reason', e))[:80]}"
    return last


def verdict(status):
    if status in GONE_STATUSES:
        return "gone"
    if isinstance(status, int) and 200 <= status < 400:
        return "ok"
    return "unknown"


def classify(results):
    """{url: {"status", "verdict"}} with the whole-host guard applied, and the hosts it held
    back. `results` is {url: status}."""
    out = {u: {"status": s, "verdict": verdict(s)} for u, s in results.items()}
    per_host = defaultdict(list)
    for u, r in out.items():
        per_host[urlsplit(u).hostname or ""].append(r)
    held = {}
    for host, rs in per_host.items():
        gone = sum(r["verdict"] == "gone" for r in rs)
        if len(rs) >= HOST_FLOOR and gone / len(rs) >= HOST_GONE_SHARE:
            held[host] = {"pages": len(rs), "gone": gone}
            for r in rs:
                if r["verdict"] == "gone":
                    r["verdict"] = "unknown"
                    r["held_back"] = "most of this host's pages answered 404 at once"
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
    again = [u for u, s in results.items() if not isinstance(s, int) and s != "not reached"]
    if again:
        pause(SECOND_PASS_PAUSE)
        for url in again:
            if clock() - started > budget:
                break
            results[url] = prober(url)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    urls = lot_urls(config.DATABASE_PATH, args.limit)
    started = time.monotonic()
    checked, held = classify(run(urls))
    counts = Counter(r["verdict"] for r in checked.values())
    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": _today_melbourne(),
        "summary": {"pages": len(urls), **dict(counts), "held_back_hosts": held},
        "urls": checked,
    }
    LINK_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LINK_STATUS_PATH.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    print(f"Lot pages checked: {len(urls)} in {time.monotonic() - started:.0f}s -- "
          f"{counts.get('ok', 0)} open, {counts.get('gone', 0)} gone (404/410), "
          f"{counts.get('unknown', 0)} could not be judged")
    for host, h in sorted(held.items()):
        print(f"  HELD BACK {host}: {h['gone']} of {h['pages']} pages answered 404 at once; "
              f"treated as a changed site, not {h['gone']} sales")
    gone_hosts = Counter(urlsplit(u).hostname for u, r in checked.items()
                         if r["verdict"] == "gone")
    for host, n in gone_hosts.most_common(8):
        print(f"  gone on {host}: {n}")
    print(f"Wrote {LINK_STATUS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
