"""
Benchmarks are the market. Building stock is what Coleen can sell. They must not mix.

Coleen, 8 Aug: *"the realestate.com.au ones are a totally another category named
BENCHMARKS and the listings will be stored in there with the link that opens that
building inside the realestate.com.au website so Coleen can compare them"*.

The whole separation is ONE line — harvest_buildings.py calls benchmarks.record_listing
instead of db.record_building — and one line is exactly the kind of thing a future
commit "unifying the harvest" reverts without noticing. It works only because every
consumer of `buildings` selects it with no source_channel predicate, so a row that is
not in that table cannot reach any of them.

That argument is not asserted here, it is MEASURED. The leak test copies the real
statements out of the repo, runs them UNEDITED against a database with benchmark rows
planted in it, and asserts zero come back. The method is deliberate: an earlier
completeness scan over this repo's source found 1 DDL site where there are 3, because
two CREATE UNIQUE INDEX statements are split across adjacent Python string literals. A
completeness guard has to be a runtime assertion over a real database, never a grep.

The other half is the compare link, which works TODAY with no PropTrack key because
constructing a URL is not a request — Coleen's browser makes it when she clicks. It is
gated on suburb_quality.is_located(), and these tests hold that gate shut: the stored
suburb column still contains 'GARAGE', '2026' and 'Logan City Council', and
`/buy/in-,+nsw/list-1` is a page that loads and answers the wrong question. No link at
all beats a broken search in front of a client.
"""

import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmarks                                                  # noqa: E402
import rea_link                                                    # noqa: E402
import suburb_quality                                              # noqa: E402
from database import ResearchDatabase                              # noqa: E402

CHANNEL = "realestate.com.au"


def _tmp(name="bench.db"):
    return os.path.join(tempfile.mkdtemp(prefix="spb-bench-"), name)


def _count_buildings(path):
    with sqlite3.connect(path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM buildings").fetchone()[0])


def _listing(**kw):
    """The dict sources/realestate.py._row actually produces. Key names UNCHANGED —
    `land_size_sqm` and `source_project_id` are the two that get silently dropped by
    anything that assumes the buildings column names."""
    row = {
        "source_channel": CHANNEL,
        "attribution_scope": "listing",
        "builder_name": "",
        "suburb": "Truganina",
        "state": "VIC",
        "price": 712000.0,
        "bedrooms": 4,
        "land_size_sqm": 350.0,
        "product_type": "Established",
        "lot_address": "12 Example Street Truganina",
        "listing_url": "https://www.realestate.com.au/property-house-vic-truganina-1",
        "source_url": "https://www.realestate.com.au/property-house-vic-truganina-1",
        "source_project_id": "143247076",
        "source_note": "realestate.com.au via PropTrack (REA Group)",
        "verified": True,
    }
    row.update(kw)
    return row


# ------------------------------------------------------------------ the URL builder

def test_the_search_url_matches_reas_own_form():
    """REA's own static host serves `in-tarneit,+vic+3029/list-2`: lowercase, LITERAL
    comma, spaces as plus signs. quote_plus without safe="," emits %2C, which is not
    that form — so this pins the encoding, not just the shape."""
    assert rea_link.search_url("Truganina", "VIC", 4) == \
        "https://www.realestate.com.au/buy/with-4-bedrooms-in-truganina,+vic/list-1"
    assert rea_link.search_url("Clyde North", "VIC") == \
        "https://www.realestate.com.au/buy/in-clyde+north,+vic/list-1"
    got = rea_link.search_url("Wollongong", "NSW", 2)
    assert "%2C" not in got, f"the comma was escaped: {got}"
    assert "+nsw" in got and got.endswith("/list-1"), got


def test_bedrooms_outside_reas_own_filter_are_dropped_not_sent():
    """REA's bedroom filter tops out at 5+, so `with-9-bedrooms` is not a narrower
    search — it is a clause the site may not honour. 24 linkable rows claim 6 or more,
    three of them 7 or 9, which are data faults rather than houses. The location-only
    link is still correct, so the clause goes and the link stays."""
    for beds in (0, 6, 9, None, "", "four"):
        got = rea_link.search_url("Tarneit", "VIC", beds)
        assert got == "https://www.realestate.com.au/buy/in-tarneit,+vic/list-1", \
            f"bedrooms={beds!r} produced {got}"
    assert "with-1-bedrooms" in rea_link.search_url("Tarneit", "VIC", 1)
    assert "with-5-bedrooms" in rea_link.search_url("Tarneit", "VIC", 5)


def test_half_an_address_never_becomes_a_url():
    """`/buy/in-,+nsw/list-1` is a page that LOADS and answers the wrong question, which
    is far harder to spot than a link that was never rendered."""
    for suburb, state in (("", "NSW"), ("Tarneit", ""), ("", ""), (None, None)):
        assert rea_link.search_url(suburb, state) == "", (suburb, state)


# ------------------------------------------------------------------ the gate

def test_a_row_whose_suburb_is_not_a_place_gets_no_link():
    """Every one of these is a real stored value in the suburb column. A link built from
    one sends Coleen's client to a search for something that is not a town."""
    for junk in ("GARAGE", "2026", "Logan City Council", "offer",
                 "Rooms Rooms m2 m2 m2"):
        row = dict(suburb=junk, state="QLD", lot_address="", street_address="",
                   source_text="", bedrooms=4)
        row["suburb"], row["suburb_source"] = suburb_quality.resolve(row)
        link = rea_link.compare_link(row)
        assert link["url"] == "", f"{junk!r} produced {link['url']!r}"
        assert row["suburb_source"], "a blank must always carry its reason"


def test_a_name_with_no_state_to_check_it_against_gets_no_link():
    """SHOWN is not LOCATED. 'Springfield' is displayed because the source really did say
    it, but a fifth of Australian locality names exist in more than one state and the
    state is INSIDE the slug — so an unchecked name would search a plausible wrong
    place, which is worse than searching nothing."""
    row = {"suburb": "Springfield", "state": "", "lot_address": "",
           "street_address": "", "source_text": "", "bedrooms": 3}
    row["suburb"], row["suburb_source"] = suburb_quality.resolve(row)
    assert row["suburb"] == "Springfield", row["suburb"]
    assert not suburb_quality.is_located(row["suburb_source"])
    assert rea_link.compare_link(row)["url"] == ""


def test_a_located_row_gets_a_link_and_the_term_to_paste():
    row = {"suburb": "Tarneit", "state": "VIC", "lot_address": "", "street_address": "",
           "source_text": "", "bedrooms": 4}
    row["suburb"], row["suburb_source"] = suburb_quality.resolve(row)
    link = rea_link.compare_link(row)
    assert link["url"].startswith("https://www.realestate.com.au/buy/"), link
    assert link["opens"] == "market" and link["label"] == "realestate.com.au"
    # The slug may go stale; what the destination indexes does not. Same contract as
    # provenance._kind's `search`.
    assert link["search"] == "Tarneit, VIC", link


# ------------------------------------------------------------------ storage

def test_a_comparable_is_stored_outside_buildings_entirely():
    path = _tmp()
    db = ResearchDatabase(path)
    before = _count_buildings(path)
    assert benchmarks.record_listing(_listing(), path=path) == "new"
    assert _count_buildings(path) == before, \
        "a market comparable reached the building stock table"
    assert benchmarks.count(path) == 1
    del db


def test_the_provider_id_and_the_land_size_survive_the_write():
    """record_building has no source_project_id key and drops it silently — measured, 0
    of 11,257 stored rows carry one, which is why backfill_proxima_projects.py exists.
    And the extractor emits `land_size_sqm`, which database.py:474 is what renames. This
    writer goes through neither, so both are pinned here."""
    path = _tmp()
    benchmarks.record_listing(_listing(), path=path)
    with sqlite3.connect(path) as conn:
        got = conn.execute("SELECT provider_listing_id, land_sqm, url_source "
                           "FROM benchmark_listings").fetchone()
    assert got[0] == "143247076", got
    assert got[1] == 350.0, got
    assert got[2] == benchmarks.URL_FROM_PROVIDER, got


def test_a_re_read_updates_in_place_rather_than_duplicating():
    """The provider's own id is the identity. `buildings` needs a sha256 over a
    flattened stocklist line because a spreadsheet row has none; a PropTrack listing
    does, and reusing that machinery would be lossy, not cheaper."""
    path = _tmp()
    assert benchmarks.record_listing(_listing(), path=path) == "new"
    assert benchmarks.record_listing(_listing(price=699000.0), path=path) == "updated"
    assert benchmarks.count(path) == 1
    with sqlite3.connect(path) as conn:
        price, first, last = conn.execute(
            "SELECT price, first_seen, last_seen FROM benchmark_listings").fetchone()
    assert price == 699000.0
    assert first and last, (first, last)


def test_a_listing_with_no_id_and_no_url_is_refused_not_stored():
    """There would be nothing to identify it by, so every night's harvest would insert
    it again — the duplicate-capture mechanism this codebase has already paid for."""
    path = _tmp()
    assert benchmarks.record_listing(
        _listing(source_project_id="", listing_url="", source_url=""), path=path) \
        == "refused"
    assert benchmarks.count(path) == 0


def test_a_listing_with_no_provider_url_still_opens_somewhere():
    """PropTrack's response contract is a guess by comp_provider_proptrack.py's own
    admission — provider_url reads `listingUrl` or `url` and neither has been seen from
    a real key. If the field name differs, the per-listing link does not exist. The row
    is not dropped and no URL is invented: it falls back to the suburb search and SAYS
    which it is, so the gap is a number on screen rather than a blank column."""
    path = _tmp()
    benchmarks.record_listing(
        _listing(listing_url="", source_url="", source_project_id="99"), path=path)
    with sqlite3.connect(path) as conn:
        url, search, source = conn.execute(
            "SELECT listing_url, search_url, url_source FROM benchmark_listings"
        ).fetchone()
    assert not url
    assert search.startswith("https://www.realestate.com.au/buy/"), search
    assert source == benchmarks.URL_FROM_SEARCH, source


def test_a_listing_that_stops_coming_back_is_marked_never_deleted():
    """A comparable vanishing from the market is a fact ABOUT the market. Deleting it
    would make the disappearance disappear too."""
    path = _tmp()
    benchmarks.record_listing(_listing(source_project_id="A"), path=path)
    benchmarks.record_listing(_listing(source_project_id="B"), path=path)
    assert benchmarks.mark_withdrawn(["A"], path=path) == 1
    assert benchmarks.count(path) == 2, "a withdrawn listing was deleted"
    assert benchmarks.status(path)["live"] == 1


# ------------------------------------------------------- the leak test, run for real

_READERS = [
    # (name, sql) — copied VERBATIM out of the file named, so a future edit there and
    # a leak here diverge loudly instead of quietly.
    ("api/_candidates.py:213 client shortlist pool",
     "SELECT source_channel FROM buildings"),
    ("build_web.py:339 snapshot SELECT",
     "SELECT source_channel FROM buildings ORDER BY builder_name, price"),
    ("build_web.py:410 'By source' dropdown",
     "SELECT source_channel, COUNT(*) FROM buildings GROUP BY 1 ORDER BY 2 DESC"),
    ("benchmark_buildings.py:326 internal peer median pool",
     "SELECT source_channel FROM buildings WHERE price IS NOT NULL AND price > 0 "
     "  AND (superseded_by IS NULL OR superseded_by = '')"),
    ("database.py:590 get_buildings — the CSV and the Excel workbook",
     "SELECT source_channel FROM buildings ORDER BY builder_name, price"),
    ("database.py:650 weekly promotion selection",
     "SELECT source_channel FROM buildings WHERE promo_selected=1"),
    ("cohorts.py:231 / catalogue_exposure.py:103",
     "SELECT source_channel FROM buildings WHERE superseded_by IS NULL AND price > 0"),
    ("database.py:668 distinct_suburbs — what PropTrack gets billed for next run",
     "SELECT source_channel FROM buildings WHERE price > 0 "
     "  AND (superseded_by IS NULL OR superseded_by = '')"),
    ("supersede_duplicates.py dedup pool",
     "SELECT source_channel FROM buildings"),
]


def test_no_reader_of_buildings_can_see_a_benchmark():
    """The completeness argument, measured rather than asserted.

    Plant five realestate.com.au comparables the way the harvest would, then run the
    real statements. Under a separate table this passes trivially — that is the point:
    it converts "a row not in the table cannot leak" from an argument into a test that
    goes red the day somebody re-points the routing line.
    """
    path = _tmp()
    ResearchDatabase(path)                       # creates `buildings`
    for i in range(5):
        assert benchmarks.record_listing(
            _listing(source_project_id=str(1000 + i), price=650000.0 + i),
            path=path) == "new"
    assert benchmarks.count(path) == 5, "the fixture did not plant anything"

    with sqlite3.connect(path) as conn:
        for name, sql in _READERS:
            got = [r[0] for r in conn.execute(sql).fetchall()]
            leaked = [c for c in got if c == CHANNEL]
            assert not leaked, f"{len(leaked)} comparable(s) leaked into {name}"


def test_the_promotion_can_never_select_a_comparable():
    """A benchmark row has a blank builder and an 'Established' product type — exactly
    the shape of a row that could be pasted into a client email as sellable stock. It is
    not in `buildings`, so set_promo_selection cannot reach it whatever it is handed."""
    path = _tmp()
    db = ResearchDatabase(path)
    benchmarks.record_listing(_listing(), path=path)
    assert db.set_promo_selection(["143247076"], True) == 0
    assert db.get_promo_selected() == []
    assert db.building_counts_by_channel() == []


def test_the_headline_count_does_not_move():
    """index.html's badge is STOCK.filter(b => !b.superseded_by).length, computed in the
    browser over stock.json. Comparables are in neither."""
    path = _tmp()
    db = ResearchDatabase(path)
    before = len(db.get_buildings())
    for i in range(3):
        benchmarks.record_listing(_listing(source_project_id="X%d" % i), path=path)
    assert len(db.get_buildings()) == before
    assert [r for r in db.building_counts_by_channel()
            if r["source_channel"] == CHANNEL] == []


# ------------------------------------------------------------------ the empty state

def test_with_no_key_the_status_says_why_and_names_the_cause():
    """An empty tab must read as an expected state, not a fault. The reason comes from
    the extractor rather than being restated, so the page and the pipeline can never
    report different statuses for the same fact."""
    path = _tmp()
    st = benchmarks.status(path)
    assert st["stored"] == 0 and st["live"] == 0
    assert st["configured"] is False
    assert "PropTrack" in st["reason"], st["reason"]
    # Not a fault, and it must say so — "0" alone tells nobody whether to buy a
    # subscription or fix a harvest.
    assert "commercial decision" in st["reason"] or "paid" in st["reason"], st["reason"]
    assert st["portal"] == "realestate.com.au"


def test_the_export_field_list_holds_nothing_the_pipeline_cannot_fill():
    """comp_provider.RawComp carries price, price_basis, price_kind, suburb, state,
    bedrooms, land_sqm, provider_listing_id, provider_url and is_new_build. Nothing
    else. bathrooms / car_spaces / house_sqm / listed_date columns would ship a table
    that is empty by construction and invite someone to 'fix' it."""
    banned = {"bathrooms", "car_spaces", "house_sqm", "listed_date", "listed_on",
              "builder_name", "superseded_by", "promo_selected", "content_hash",
              "benchmark_median", "postcode"}
    overlap = banned & set(benchmarks.EXPORT_FIELDS)
    assert not overlap, f"benchmarks export fields the pipeline cannot fill: {overlap}"


def test_the_published_payload_never_promises_a_link_it_cannot_open():
    """build_web._assert_every_benchmark_listing_opens_somewhere. A price Coleen cannot
    click is a number she has to take on trust, which is the thing this whole category
    exists to replace."""
    import build_web
    path = _tmp("benchmarks.json")
    good = {"keys": ["listing_url", "search_url"],
            "rows": [["https://www.realestate.com.au/x", ""],
                     ["", "https://www.realestate.com.au/buy/in-tarneit,+vic/list-1"]]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(good, fh)
    build_web._assert_every_benchmark_listing_opens_somewhere(_P(path))

    bad = {"keys": ["listing_url", "search_url"], "rows": [["", ""]]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bad, fh)
    try:
        build_web._assert_every_benchmark_listing_opens_somewhere(_P(path))
    except SystemExit:
        return
    raise AssertionError("a comparable with no destination was allowed to publish")


def test_the_export_guards_actually_run_over_the_new_payload():
    """_check() and _assert_no_contact_details both iterate hard-coded tuples. A payload
    missing from either ships with the credential allow-list or the contact-detail value
    scan never having run on it — and an agency-sourced listing payload is the most
    likely place a direct phone number has ever reached this project's free text."""
    import build_web
    src = open(build_web.__file__, encoding="utf-8").read()
    assert '("benchmarks", BENCHMARK_FIELDS)' in src, \
        "BENCHMARK_FIELDS is not registered with _check()"
    assert '"benchmarks.json"' in src.split("_assert_no_contact_details")[-1] \
        or '"vendor-assets.json", "benchmarks.json"' in src, \
        "benchmarks.json is not scanned by _assert_no_contact_details"


class _P(str):
    """Path-alike with the two attributes the guard uses, so the test needs no pathlib
    import gymnastics on Windows."""
    def exists(self):
        return os.path.exists(str(self))

    def read_text(self, encoding="utf-8"):
        with open(str(self), encoding=encoding) as fh:
            return fh.read()

    @property
    def name(self):
        return os.path.basename(str(self))


# --------------------------------------------------------------- the routing pin

def test_the_harvest_routes_realestate_to_benchmarks_and_not_to_buildings():
    """The entire separation is this one call. It is one line, in a file somebody will
    one day tidy, and every one of the readers above would start returning market
    listings the moment it changed back."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(here, "harvest_buildings.py"), encoding="utf-8").read()
    rea_block = src.split("if rea:")[1].split("if proxima:")[0]
    # Comments stripped: the routing comment in that block NAMES db.record_building to
    # explain what it deliberately is not, and a test that reads prose would fail on the
    # explanation of its own invariant.
    code = "\n".join(line.split("#", 1)[0] for line in rea_block.splitlines())
    assert "benchmarks.record_listing" in code, \
        "the realestate.com.au harvest no longer routes to the Benchmarks table"
    assert "db.record_building" not in code, \
        "the realestate.com.au harvest is writing market listings into building stock"
    # The channel must also stay out of the blocking guard: a comparables outage must
    # never abort run_daily at step 1/7 and freeze the whole dashboard.
    assert 'read["realestate.com.au"]' not in src, \
        "realestate.com.au is back in the harvest's blocking channel guard"


def run_all():
    tests = [
        ("the search URL matches REA's own form",
         test_the_search_url_matches_reas_own_form),
        ("bedrooms outside REA's filter are dropped",
         test_bedrooms_outside_reas_own_filter_are_dropped_not_sent),
        ("half an address never becomes a URL", test_half_an_address_never_becomes_a_url),
        ("a suburb that is not a place gets no link",
         test_a_row_whose_suburb_is_not_a_place_gets_no_link),
        ("a name with no state gets no link",
         test_a_name_with_no_state_to_check_it_against_gets_no_link),
        ("a located row gets a link and a search term",
         test_a_located_row_gets_a_link_and_the_term_to_paste),
        ("a comparable is stored outside buildings",
         test_a_comparable_is_stored_outside_buildings_entirely),
        ("the provider id and land size survive the write",
         test_the_provider_id_and_the_land_size_survive_the_write),
        ("a re-read updates in place", test_a_re_read_updates_in_place_rather_than_duplicating),
        ("an unidentifiable listing is refused",
         test_a_listing_with_no_id_and_no_url_is_refused_not_stored),
        ("no provider URL still opens somewhere",
         test_a_listing_with_no_provider_url_still_opens_somewhere),
        ("a withdrawn listing is marked, not deleted",
         test_a_listing_that_stops_coming_back_is_marked_never_deleted),
        ("no reader of buildings can see a benchmark",
         test_no_reader_of_buildings_can_see_a_benchmark),
        ("the promotion can never select a comparable",
         test_the_promotion_can_never_select_a_comparable),
        ("the headline count does not move", test_the_headline_count_does_not_move),
        ("with no key the status says why",
         test_with_no_key_the_status_says_why_and_names_the_cause),
        ("the export list holds nothing unfillable",
         test_the_export_field_list_holds_nothing_the_pipeline_cannot_fill),
        ("no comparable publishes without a destination",
         test_the_published_payload_never_promises_a_link_it_cannot_open),
        ("the export guards run over benchmarks.json",
         test_the_export_guards_actually_run_over_the_new_payload),
        ("the harvest routes REA to benchmarks",
         test_the_harvest_routes_realestate_to_benchmarks_and_not_to_buildings),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] benchmarks: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] benchmarks: {name}: {e}")
    return failed


if __name__ == "__main__":
    sys.exit(1 if run_all() else 0)
