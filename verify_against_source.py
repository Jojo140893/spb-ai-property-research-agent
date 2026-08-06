"""
Re-read E-Agent's own price lists and check every stored price against them.

Colin, 6 Aug: "reverify all the buildings from e agent too and search from their portal
to verify too compare the prices you extracted and the prices that are in their portal."

Every E-Agent row records the file it came out of. This re-downloads those files today,
finds the price structure the file itself states, and classifies each stored price:

  TOTAL          our price is a package total the file prints          -> correct
  COMPONENT      our price is a land or build figure the file prints,  -> UNDERSTATED
                 and the file states a larger total containing it
  NOT FOUND      our price appears nowhere in the file                 -> stale or wrong
  NO EVIDENCE    the file states no land/build/total structure          -> cannot judge

A file states a "package" when three money figures on one line satisfy land + build =
total. That is the arithmetic Colin pointed at on Proxima ($675,000 + $452,000 =
$1,127,000) and it is how every E-Agent stocklist is laid out. Nothing is inferred from
ratios or averages: a row is only called understated when the SAME FILE prints a total
that contains our figure as one of its two components.

Read-only over the network and over the database. Pass --apply to write the corrections
it finds; without it, nothing is changed.
"""

import argparse
import csv
import io
import re
import sqlite3
import ssl
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DATABASE_PATH                                    # noqa: E402
from sources.scraper_base import normalise_money_spacing            # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"}
_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

CACHE = Path(__file__).resolve().parent / ".verify_cache"

# Comma-grouped only. A bare number in a spreadsheet is as likely to be a floor area or
# a lot number as a price -- that confusion is what published $149 for a $7.95M
# apartment -- so a price has to look like money before it counts as evidence.
MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+)(?:\.\d{2})?(?!\d)")
MIN_PRICE, MAX_PRICE = 50_000.0, 10_000_000.0

# The sheet's own arithmetic is allowed to be a couple of dollars out (rounding, or an
# advertised "$999,900" against a $1,000,350 sum). Anything larger is not the same number.
TOL = 2_000.0


def sheet_url(url: str) -> str:
    """A Google Sheets edit link -> the CSV export of the same tab."""
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url)
    if not m:
        return url
    gid = (re.search(r"[#&?]gid=(\d+)", url) or [None, "0"])[1]
    return ("https://docs.google.com/spreadsheets/d/%s/export?format=csv&gid=%s"
            % (m.group(1), gid))


def fetchable(url: str) -> bool:
    u = (url or "").lower()
    if "docs.google.com/spreadsheets" in u:
        return True
    return "e-agent.com.au/_files" in u


def fetch(url: str) -> bytes:
    """Download, with an on-disk cache so a re-run does not re-hit the vendor."""
    CACHE.mkdir(exist_ok=True)
    key = CACHE / (re.sub(r"[^A-Za-z0-9]", "_", url)[-120:] + ".bin")
    if key.exists() and key.stat().st_size:
        return key.read_bytes()
    real = sheet_url(url) if "docs.google.com/spreadsheets" in url.lower() else url
    with urllib.request.urlopen(urllib.request.Request(real, headers=UA),
                                timeout=90, context=_SSL) as r:
        body = r.read()
    key.write_bytes(body)
    return body


def lines_from(url: str, body: bytes):
    """The file as text lines, whatever kind of file it is."""
    head = body[:5]
    if head.startswith(b"%PDF"):
        import pdfplumber
        out = []
        with pdfplumber.open(io.BytesIO(body)) as pdf:
            for page in pdf.pages:
                out.extend((page.extract_text() or "").splitlines())
                for table in (page.extract_tables() or []):
                    for row in table:
                        out.append(" ".join(c or "" for c in row))
        return out
    if head.startswith(b"PK"):                       # xlsx
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(body), data_only=True, read_only=True)
        out = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                out.append(" ".join("" if v is None else str(v) for v in row))
        return out
    text = body.decode("utf-8", "replace")
    if text.lstrip()[:1] == "<":                     # an HTML error/login page
        return []
    return [" ".join(c for c in row) for row in csv.reader(io.StringIO(text))]


def amounts(line: str):
    # The vendor files carry the same PDF split-money shapes the extractor repairs
    # ("$ 9 32,900", "$ 1 ,599,400"). Without this the source's own total is
    # invisible to the check and a correct row is reported as unverifiable.
    line = normalise_money_spacing(line)
    out = []
    for m in MONEY.finditer(line):
        v = float(m.group(1).replace(",", ""))
        if MIN_PRICE <= v <= MAX_PRICE:
            out.append(v)
    return out


def price_structure(lines):
    """What the file itself says a package costs.

    totals      -> every figure the file presents as a package total
    components  -> every figure the file presents as PART of some total
    contains    -> component figure : the totals that contain it
    """
    totals, components = set(), set()
    contains = defaultdict(set)
    for line in lines:
        a = amounts(line)
        if len(a) < 3:
            continue
        # Try every ordered pair against every third figure on the line, so the column
        # order does not have to be assumed. A line only counts when its own numbers add up.
        for i in range(len(a)):
            for j in range(i + 1, len(a)):
                for k in range(len(a)):
                    if k in (i, j):
                        continue
                    if abs((a[i] + a[j]) - a[k]) <= TOL and a[k] > max(a[i], a[j]):
                        totals.add(a[k])
                        components.add(a[i])
                        components.add(a[j])
                        contains[a[i]].add(a[k])
                        contains[a[j]].add(a[k])
    return totals, components, contains


# A listing whose PRODUCT is a component. The component price is the right price here,
# and "correcting" it to a package total would overstate a land lot by the cost of a
# house that is not being sold.
#
#   "Available 23 LAND ONLY 9 318.6 Registered Land $918,000 $918,000"
#   "PR8831 8 Heathwood The Crest Estate September 2026 400 Build Only $390,639"
#
# Both were flagged as understated on the first run, because the same land figure also
# appears inside a package further down the same sheet. The source itself says what is
# being sold, so it is asked rather than inferred.
_PRODUCT_IS_A_COMPONENT = re.compile(
    r"\bland\s*only\b|\bbuild\s*only\b|\bhouse\s*only\b|\bvacant\s+land\b"
    r"|\bregistered\s+land\b|\bland\s+package\b(?!\s*\+)", re.I)


def classify(price, totals, components, contains, text=""):
    if _PRODUCT_IS_A_COMPONENT.search(text or ""):
        return "PART SOLD ALONE", None
    if not totals and not components:
        return "NO EVIDENCE", None
    for t in totals:
        if abs(price - t) <= TOL:
            return "TOTAL", None
    for comp, tots in contains.items():
        # EXACT here, not TOL. A component is matched across the whole file, so a loose
        # tolerance drags in a different lot's figure and invents an understatement:
        # $999,900 matched another row's $998,000 and proposed raising a correct
        # $999,900 listing to that row's $1,545,900 total. $650,000 matched $649,900 the
        # same way. The arithmetic test above can afford slack; identifying WHICH lot a
        # figure belongs to cannot.
        if abs(price - comp) < 1:
            return "COMPONENT", min(tots)
    return "NOT FOUND", None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the corrections found")
    ap.add_argument("--limit-files", type=int, default=0)
    args = ap.parse_args()

    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    rows = [r for r in conn.execute(
        "SELECT id, price, land_price, build_price, builder_name, "
        "       COALESCE(source_text,'') stext, "
        "       COALESCE(stocklist_file,'') sf, COALESCE(source_url,'') su "
        "  FROM buildings "
        " WHERE source_channel='E-Agent' AND superseded_by IS NULL AND price > 0")]

    by_file = defaultdict(list)
    for r in rows:
        by_file[(r["sf"] or r["su"])].append(r)

    targets = [(u, rs) for u, rs in by_file.items() if fetchable(u)]
    targets.sort(key=lambda x: -len(x[1]))
    if args.limit_files:
        targets = targets[:args.limit_files]

    print("E-AGENT PRICE VERIFICATION AGAINST THE SOURCE FILES")
    print("  live priced rows: %d across %d files; %d files re-fetchable\n"
          % (len(rows), len(by_file), len(targets)))

    verdict = Counter()
    fixes = []
    unreachable = 0
    for n, (url, rs) in enumerate(targets, 1):
        try:
            body = fetch(url)
            lines = lines_from(url, body)
        except Exception as e:
            unreachable += 1
            print("  [%3d/%d] UNREACHABLE (%s) %s" % (n, len(targets), type(e).__name__, url[:70]))
            continue
        totals, components, contains = price_structure(lines)
        local = Counter()
        for r in rs:
            v, better = classify(float(r["price"]), totals, components, contains,
                                 r["stext"])
            local[v] += 1
            verdict[v] += 1
            if v == "COMPONENT" and better and better > float(r["price"]):
                fixes.append((r, float(r["price"]), better, url))
        print("  [%3d/%d] %-52s rows=%-4d packages=%-4d %s"
              % (n, len(targets), url.split("/")[-1][:52], len(rs), len(totals), dict(local)))

    print("\n  ---------------------------------------------------------------")
    print("  VERDICT ACROSS EVERY RE-FETCHED FILE")
    for k in ("TOTAL", "PART SOLD ALONE", "COMPONENT", "NOT FOUND", "NO EVIDENCE"):
        print("    %-12s %d" % (k, verdict[k]))
    if unreachable:
        print("    files that would not download: %d" % unreachable)

    if fixes:
        gap = sum(b - p for _, p, b, _ in fixes)
        print("\n  UNDERSTATED — the source file states a larger total containing our figure")
        print("    rows: %d   understatement: $%s" % (len(fixes), format(gap, ",.0f")))
        for r, p, b, _u in sorted(fixes, key=lambda x: -(x[2] - x[1]))[:15]:
            print("      id %-6s %-24s $%12s -> $%12s"
                  % (r["id"], (r["builder_name"] or "?")[:24],
                     format(p, ",.0f"), format(b, ",.0f")))

    if fixes and args.apply:
        for r, _p, better, _u in fixes:
            conn.execute("UPDATE buildings SET price = ? WHERE id = ?", (better, r["id"]))
        conn.commit()
        print("\n  APPLIED: %d row(s) corrected to the total their own source file states."
              % len(fixes))
    elif fixes:
        print("\n  DRY RUN. Re-run with --apply to write these corrections.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
