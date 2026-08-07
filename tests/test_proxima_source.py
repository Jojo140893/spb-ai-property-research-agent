"""
Tests for the Proxima harvest (sources/proxima.py).

Every fixture reproduces the SHAPE of a real portal record — the address grammar, the
zero-padded lot codes, the "00"-means-unrecorded convention, the same-lot-under-two-
projects case — but the lots, streets and prices are invented. This repo is public and
the client's stock is her commercial asset, so no live listing goes in here; the shape
is what the parsing turns on, and the shape is faithful.

The parsing itself is deliberately thin, because Proxima hands over typed data-*
attributes rather than prose. These tests guard the places where a wrong answer would
be silent: a lot filed under the wrong project, a "0" read as a real measurement, or a
cross-listed lot counted twice.
"""

from sources.proxima import (ProximaSource, parse_property_name, parse_project_title,
                             _num, _int, _lot_number)


# ------------------------------------------------------------------ address

def test_the_address_forms_proxima_actually_uses():
    """The three grammars seen on the portal, with invented specifics.

    The middle one matters most: a lot label can carry a PRODUCT name after the
    number ("Lot 12 Riverstone Unit 12 Riverstone"), so the label is not simply
    "Lot <digits>" and the tail has to be read from the right.
    """
    cases = {
        "Lot 14 Unit 14, 7 Example Avenue, SAMPLETON, NSW, 2765":
            ("Sampleton", "NSW", "2765"),
        "Lot 12 Riverstone Unit 12 Riverstone, Specimen Drive, TESTVALE, VIC, 3351":
            ("Testvale", "VIC", "3351"),
        "Lot 9 Fixture Unit 9 Fixture, Placeholder Street, MOCKBURY, VIC, 3356":
            ("Mockbury", "VIC", "3356"),
    }
    for raw, (sub, st, pc) in cases.items():
        a = parse_property_name(raw)
        assert (a["suburb"], a["state"], a["postcode"]) == (sub, st, pc), (raw, a)


def test_a_missing_tail_is_left_blank_not_guessed():
    """Standing rule: a blank with a reason beats a plausible guess."""
    a = parse_property_name("Lot 12, Some Street")
    assert a["state"] == "" and a["postcode"] == "", a
    assert parse_property_name("")["suburb"] == ""
    assert parse_property_name(None)["state"] == ""


def test_a_suburb_is_never_mistaken_for_a_state():
    a = parse_property_name("Unit 5, 1 Smith Street, NORTHLAND, VIC, 3072")
    assert a["suburb"] == "Northland" and a["state"] == "VIC", a


# -------------------------------------------------------------------- values

def test_zero_means_not_recorded_not_zero():
    """Proxima writes "0"/"00"/"" for fields it does not hold.

    Storing 0 would look like a measurement — a 0 m frontage or a 0-bedroom house
    that nobody recorded. The scoring pipeline treats a real 0 very differently from
    a None, so this distinction has to survive into the row.
    """
    for blank in ("0", "00", "", "0.00", None):
        assert _num(blank) is None, blank
        assert _int(blank) is None, blank
    assert _num("318.00") == 318.0
    assert _num("10.200000") == 10.2
    assert _int("3") == 3


def test_price_strings_parse():
    assert _num("829990") == 829990.0
    assert _num("$1,234,567.00") == 1234567.0


def test_lot_number_loses_the_padding_but_not_the_lot():
    assert _lot_number("00000014/00000014") == "14"
    assert _lot_number("000000103") == "103"
    assert _lot_number("") == ""
    # A lot that is genuinely all zeros must not become "0"
    assert _lot_number("00000/00000") == ""


# --------------------------------------------------------------- row building

_HEADER = {
    "project": "1 Example Road Sampleton Land Only (10/27)",
    "status": "For Sale",
    "location": "NSW",
    "developer": "Placeholder Developments",
}

_LOT = {
    "name": "Lot 14 Unit 14, 7 Example Avenue, SAMPLETON, NSW, 2765",
    "lot": "00000014/00000014", "room": "00", "bathroom": "", "carspace": "",
    "propertywidth": "10.200000", "propertylength": "", "landsize": "318.00",
    "rop": "829990", "packageprice": "0",
    "_titled": "Land Registered", "_status": "For Sale",
}


def test_a_lot_maps_onto_the_schema():
    r = ProximaSource()._row(dict(_LOT), dict(_HEADER))
    assert r["state"] == "NSW"
    assert r["suburb"] == "Sampleton"
    assert r["postcode"] == "2765"
    assert r["lot_number"] == "14"
    assert r["advertised_package_price"] == 829990.0
    assert r["land_size_sqm"] == 318.0
    assert r["frontage_m"] == 10.2
    assert r["availability_status"] == "For Sale"
    assert r["title_status"] == "Land Registered"
    assert r["source_channel"] == "Proxima"
    # "00" bedrooms is not zero bedrooms
    assert r["bedrooms"] is None and r["bathrooms"] is None and r["car_spaces"] is None


def test_the_builder_is_read_from_the_header_never_invented():
    r = ProximaSource()._row(dict(_LOT), dict(_HEADER))
    assert r["builder_name"] == "Placeholder Developments"
    assert r["builder_source"] == "proxima project header"
    assert r["attribution_scope"] == "builder"

    blank = ProximaSource()._row(dict(_LOT), {**_HEADER, "developer": ""})
    assert blank["builder_name"] == "", "a nameless project must not acquire a builder"
    assert blank["attribution_scope"] == "project"
    assert blank["builder_source"] == ""


def test_the_estate_is_the_project_the_lot_actually_sits_in():
    """The bug that made the first run wrong.

    Every project's lots share one DOM container, so an unscoped read gave each lot
    whichever project came last — wrong estate AND wrong builder, on 10,774 rows.
    """
    r = ProximaSource()._row(dict(_LOT), dict(_HEADER))
    assert r["estate_name"] == "1 Example Road Sampleton Land Only"


# ------------------------------------------------------ the project-name counter

def test_the_live_counter_is_not_part_of_the_estates_name():
    """The header is a name AND a live availability counter; only the name is a name.

    Proxima writes "Ahlei (85/110)" — 85 lots available of 110 — and the pair moves
    whenever a lot sells or comes back. Storing it verbatim made the estate's own NAME
    volatile: the same Wollongong building was stored as "...Building A (2/305)" on
    3 Aug and "...Building A (3/305)" on 7 Aug. Nothing about the estate had changed.
    """
    assert parse_project_title("Ahlei (85/110)") == ("Ahlei", 85, 110)
    assert parse_project_title("Ascenta Living Traditional (56/86)") == \
        ("Ascenta Living Traditional", 56, 86)
    # A digit-led name must keep its digits; only the trailing pair goes.
    assert parse_project_title("350 Quakers Road Nirimba Fields (9/20)") == \
        ("350 Quakers Road Nirimba Fields", 9, 20)


def test_the_same_estate_reads_the_same_across_harvests():
    """The whole point: two harvests, two counters, ONE estate name.

    estate_name is not part of building_content_hash, so this never split a lot into
    two rows — but it fragmented every grouping, filter and display by estate, and made
    comparing one harvest to the next by estate unreliable.
    """
    monday = ProximaSource()._row(
        dict(_LOT), {**_HEADER, "project": "Atchison and Kenny Wollongong Building A (2/305)"})
    friday = ProximaSource()._row(
        dict(_LOT), {**_HEADER, "project": "Atchison and Kenny Wollongong Building A (3/305)"})
    assert monday["estate_name"] == friday["estate_name"] == \
        "Atchison and Kenny Wollongong Building A"
    # ...and the thing that legitimately moved is stored as the number it is.
    assert (monday["project_available"], monday["project_total"]) == (2, 305)
    assert (friday["project_available"], friday["project_total"]) == (3, 305)


def test_a_real_parenthesis_in_a_name_survives():
    """The reason the pattern is anchored to a trailing pair of integers.

    These are real project names on this portal. A looser "strip the last bracket" rule
    would file stock under "Ascenta Living" and "Creation Homes", quietly merging two
    different names into one and losing the entity the client actually deals with.
    """
    for name in ("Ascenta Living (DBN Homes)",
                 "Creation Homes (Qld) Pty Ltd",
                 "Riverleigh, Logan Reserve (stage 3) House & Land",
                 "East (Ocean Views) West (Hinterland Views)"):
        assert parse_project_title(name) == (name, None, None), name

    # Both at once: the counter goes, the company does not.
    assert parse_project_title("Ascenta Living (DBN Homes) (56/86)") == \
        ("Ascenta Living (DBN Homes)", 56, 86)


def test_only_a_pair_of_integers_counts_as_a_counter():
    """Anything that is not <int>/<int> at the very end is part of the name."""
    for name in ("Stage 2 (3)",            # one number, not a pair
                 "Somewhere (a/b)",        # not numbers
                 "Somewhere (3/)",         # half a pair
                 "Somewhere (/20)",
                 "Somewhere (3/20",        # never closed
                 "Somewhere (3/20) Stage 2"):   # not at the end
        assert parse_project_title(name) == (name, None, None), name
    # Proxima's own spacing varies; a spaced pair is still a counter.
    assert parse_project_title("Somewhere ( 3 / 20 )") == ("Somewhere", 3, 20)


def test_a_project_with_no_counter_keeps_its_whole_name():
    assert parse_project_title("Arcadia Estate") == ("Arcadia Estate", None, None)
    assert parse_project_title("") == ("", None, None)
    assert parse_project_title(None) == ("", None, None)
    # Nothing to store is stored as nothing — never as 0, which would read as
    # "this project has sold out" on a project we simply did not measure.
    r = ProximaSource()._row(dict(_LOT), {**_HEADER, "project": "Arcadia Estate"})
    assert r["project_available"] is None and r["project_total"] is None


def test_a_header_that_is_only_a_counter_still_names_something():
    """A blank estate is the one outcome a grouping cannot recover from.

    Stripping is not allowed to be the thing that empties the column, so a header with
    no name left over keeps the raw string.
    """
    name, avail, total = parse_project_title("(3/305)")
    assert name == "(3/305)" and (avail, total) == (3, 305)


def test_a_lot_with_no_price_is_not_a_listing():
    src = ProximaSource()
    assert src._row({**_LOT, "rop": "0", "packageprice": "0"}, _HEADER) is None
    assert src.stats.get("no price") == 1


def test_a_lot_with_no_address_is_refused():
    src = ProximaSource()
    assert src._row({**_LOT, "name": ""}, _HEADER) is None
    assert src.stats.get("no address") == 1


def test_the_state_falls_back_to_the_project_location():
    """Only where the project header actually states one."""
    lot = {**_LOT, "name": "Lot 9, Some Road, SOMEWHERE"}
    r = ProximaSource()._row(lot, {**_HEADER, "location": "BRADDON, ACT, 2612"})
    assert r["state"] == "ACT", r["state"]
    blank = ProximaSource()._row(lot, {**_HEADER, "location": ""})
    assert blank["state"] == "", "no stated state anywhere means no state"


# ------------------------------------------------------------- cross-listing

def test_a_cross_listed_lot_is_stored_once_and_counted():
    """One vendor publishes the same lots under two programme names.

    One physical lot, two programmes. Both would collide on content_hash and the
    upsert would silently keep the last, so the collapse happens here where it can
    be counted instead of vanishing.
    """
    src = ProximaSource()
    a = src._row(dict(_LOT), {**_HEADER, "project": "Sample Estate Programme A (9/11)"})
    b = src._row(dict(_LOT), {**_HEADER, "project": "Sample Estate Programme B (56/86)"})
    out = src._collapse_cross_listed([a, b])
    assert len(out) == 1, out
    assert len(src.cross_listed) == 1
    assert out[0]["estate_name"] == "Sample Estate Programme A", "first wins, deterministically"


def test_two_harvests_of_a_cross_listed_lot_collapse_the_same_way():
    """Stripping the counter must not make the collapse pick a different winner.

    The order is page order and the winner is the first, so both harvests have to see
    the same two names. With the counter still attached they differed on every run,
    which made the drop that gets logged look like a different lot each time.
    """
    src = ProximaSource()
    later = src._collapse_cross_listed([
        src._row(dict(_LOT), {**_HEADER, "project": "Sample Estate Programme A (8/11)"}),
        src._row(dict(_LOT), {**_HEADER, "project": "Sample Estate Programme B (55/86)"}),
    ])
    assert len(later) == 1
    assert later[0]["estate_name"] == "Sample Estate Programme A"


def test_genuinely_different_lots_both_survive():
    src = ProximaSource()
    a = src._row(dict(_LOT), dict(_HEADER))
    b = src._row({**_LOT, "lot": "00000015/00000015",
                  "name": "Lot 15 Unit 15, 9 Example Avenue, SAMPLETON, NSW, 2765"},
                 dict(_HEADER))
    assert len(src._collapse_cross_listed([a, b])) == 2


# ------------------------------------------------------------------ storage

def test_a_reharvest_refreshes_the_counters_instead_of_freezing_them():
    """The counters describe the project NOW, so a re-harvest must move them.

    record_building fills most detail columns with COALESCE(NULLIF(col,''), ?) — first
    value wins — which is right for a name and wrong for a live count: it would pin the
    project at whatever it read the first time and quietly present that as current. It
    is also why the stored estate names had to be backfilled rather than left for the
    next harvest to correct (backfill_proxima_estate_names.py).
    """
    import sqlite3
    import tempfile
    from pathlib import Path
    from database import ResearchDatabase

    db = ResearchDatabase(db_path=Path(tempfile.mkdtemp()) / "proxima.db")
    monday = ProximaSource()._row(dict(_LOT), {**_HEADER, "project": "Ahlei (85/110)"})
    assert db.record_building(dict(monday)) == "new"

    friday = ProximaSource()._row(dict(_LOT), {**_HEADER, "project": "Ahlei (84/110)"})
    db.record_building(dict(friday))
    rows = db.get_buildings()
    assert len(rows) == 1, "a moved counter created a second listing"
    assert rows[0]["estate_name"] == "Ahlei"
    assert rows[0]["project_available"] == 84, "the counter froze at its first reading"
    assert rows[0]["project_total"] == 110

    # A project with nothing left is a real reading, not a missing one — and 0 is
    # exactly the value the generic "did this run supply anything" guard discards.
    db.record_building(dict(ProximaSource()._row(
        dict(_LOT), {**_HEADER, "project": "Ahlei (0/110)"})))
    assert db.get_buildings()[0]["project_available"] == 0, "a sold-out project read as stale"

    # A source that states no counter must not blank one that was read before.
    db.record_building(dict(ProximaSource()._row(
        dict(_LOT), {**_HEADER, "project": "Ahlei"})))
    kept = db.get_buildings()[0]
    assert kept["project_total"] == 110, "an unstated counter erased a stored one"


def test_the_channel_matches_the_portal_config():
    """The channel is part of identity AND is Colin's by-source filter entry."""
    from sources.portal_config import BUILDER_PORTAL_CONFIGS
    assert ProximaSource().channel_name == BUILDER_PORTAL_CONFIGS["proxima.com.au"].source_channel


# ------------------------------------------------------- the remembered filter
#
# Proxima holds the projects-page filter in the Magento SESSION, and the harvest reuses
# the persistent browser profile a human signs in with (2FA is re-challenged per browser
# context, so it has no choice). A `property[state]=NSW` left behind after a manual
# sign-in on 2026-08-07 rendered 8 projects instead of 40 and the harvest stored 52 lots
# instead of 1,293 — the page was not broken, it was showing a smaller portfolio.
#
# THE FAKE KEEPS TWO STATES APART, because the gap between them is the entire bug. `dom`
# is what the form on screen shows; `session` is what the server holds. A page load
# re-renders the DOM from the session. Proxima's `#search_clear` empties the DOM and
# leaves the session alone, so a check that re-reads the same DOM sees an empty field and
# calls it cleared — measured live on 2026-08-07, which harvested 1,049 lots against 1,293
# and reported [SUCCESS]. Only the submit reaches the session, and only a reload proves it.

class _FakePage:
    """A projects page whose form state and server state can disagree."""

    def __init__(self, session_filter=None, submit_persists=True, has_submit=True,
                 labels=()):
        self.session = dict(session_filter or {})   # what the server holds
        self.dom = dict(self.session)               # what the form shows
        self.submit_persists = submit_persists      # False = clears the DOM only
        self.has_submit = has_submit
        self.labels = list(labels)
        self.dispatched = 0                         # clear+submit attempts made
        self.loads = 0                              # fresh page loads
        self.enumerations = 0
        self.url = ""

    def evaluate(self, js, *args):
        from sources import proxima as px
        if js is px._FILTER_SNAPSHOT_JS:
            return dict(self.dom)
        if js is px._CLEAR_AND_SUBMIT_JS:
            self.dispatched += 1
            defaults = dict((args[0] or {}).get("defaults") or {}) if args else {}
            self.dom = dict(defaults)               # the clear control, DOM only
            if not self.has_submit:
                return ""                           # nothing posted, session untouched
            if self.submit_persists:
                self.session = dict(defaults)
            return "#search_clear + #search_submit"
        return None

    def load(self):
        """What a navigation does: the form is re-rendered from the session."""
        self.loads += 1
        self.dom = dict(self.session)

    def query_selector_all(self, selector):
        if "tab-label" in selector:
            self.enumerations += 1
        return list(self.labels)

    def wait_for_load_state(self, *a, **k): pass
    def wait_for_selector(self, *a, **k): pass
    def wait_for_timeout(self, *a, **k): pass


class _FakeScraper:
    def __init__(self, page):
        from pathlib import Path
        self.page = page
        # search() refuses to start without one of these; point at something real.
        self.profile_dir = Path(__file__).resolve().parent
        self.session_file = self.profile_dir / "no-such-session.json"

    def session(self):
        from contextlib import contextmanager
        @contextmanager
        def _open():
            yield self
        return _open()

    def goto(self, url):
        self.page.url = url
        self.page.load()


def _harvest_with(page):
    """Run search() against a faked portal, with the real decision logic intact."""
    from sources import proxima as px
    saved = (px.PlaywrightScraper, px.PLAYWRIGHT_AVAILABLE)
    px.PlaywrightScraper = lambda **k: _FakeScraper(page)
    px.PLAYWRIGHT_AVAILABLE = True
    try:
        src = ProximaSource()
        return src, src.search({})
    finally:
        px.PlaywrightScraper, px.PLAYWRIGHT_AVAILABLE = saved


_SALE = {"property_status": "SALE"}


def test_an_unfiltered_page_is_left_alone():
    """The guard must not touch the ordinary case, or every run pays for the reset."""
    page = _FakePage()
    src, rows = _harvest_with(page)
    assert page.dispatched == 0, "an unfiltered page was reset anyway"
    assert src.filter_found == {}
    assert page.enumerations == 1, "a clean page must still be harvested"
    assert rows == []


def test_a_form_default_is_not_a_filter():
    """Observed live on 2026-08-07, and the reason this distinction exists.

    The projects page arrives with property[property_status]=SALE and nothing else. That
    run read 40/40 projects and 1,293 lots — the known-good line, so the value hides
    nothing — and the reset control left it in place, which is what restoring a form
    default looks like. Treating it as a filter would put "the page came up FILTERED" in
    the log every night until nobody reads it, which is how a real one gets missed.
    """
    page = _FakePage(dict(_SALE))
    src, _ = _harvest_with(page)
    assert page.dispatched == 0, "a form default triggered a reset"
    assert src.filter_found == {}, "a form default was reported as a filter"
    assert page.enumerations == 1


def test_the_default_is_matched_on_the_value_not_the_field():
    """property_status=SOLD is a filter, and would hide most of the portfolio."""
    page = _FakePage({"property_status": "SOLD"})
    src, _ = _harvest_with(page)
    assert src.filter_found == {"property_status": "SOLD"}, src.filter_found
    assert page.dispatched == 1
    assert page.enumerations == 1, "it cleared, so the harvest goes ahead"


def test_a_leftover_filter_is_cleared_before_anything_is_counted():
    """The 7 Aug shape: a state filter set, gone once the cleared form is posted."""
    page = _FakePage({**_SALE, "state": "NSW"})
    src, _ = _harvest_with(page)
    assert page.dispatched == 1, page.dispatched
    assert src.filter_found == {"state": "NSW"}, "what was cleared has to be reportable"
    assert page.enumerations == 1, "the page should be counted once it is clear"
    assert page.session == _SALE, "the SERVER's filter is what had to change"
    # The default is put back rather than blanked: the known-good 40 projects / 9
    # availability views is measured with property_status=SALE set.
    assert page.dom == _SALE


def test_emptying_the_form_is_not_clearing_the_filter():
    """The defect the live run caught, and the reason verification is a fresh load.

    Proxima's `#search_clear` empties the select in the DOM and never reaches the
    session. A first version of this fix clicked it, re-read the now-empty field,
    declared the page clear, and harvested 1,049 lots against the unfiltered 1,293 —
    printing [SUCCESS], because a 19% shortfall clears the partial-read floor.

    Modelled here by a submit that does not persist: whatever the form says, a reload
    brings the filter back, and that is the only answer worth trusting.
    """
    page = _FakePage({**_SALE, "state": "NSW"}, submit_persists=False)
    src, rows = _harvest_with(page)
    assert rows == [], "a page that only LOOKS cleared was harvested"
    assert page.enumerations == 0, "the still-filtered listing was enumerated"
    assert page.loads >= 1, "the check never reloaded — it re-read the DOM it just edited"
    assert page.session["state"] == "NSW", "the session was never actually cleared"


def test_a_filter_that_will_not_clear_stops_the_harvest():
    """A filtered read is worse than no read.

    It stores a subset and stamps it as today's stock, so the rest goes stale with
    nothing on screen to say so. Reading nothing at least trips the dead-channel check.
    """
    from sources.proxima import ProximaSource as _P
    page = _FakePage({"state": "NSW"}, submit_persists=False)
    src, rows = _harvest_with(page)
    assert rows == [], "a filtered page must not be harvested"
    assert src.filter_found == {"state": "NSW"}
    assert page.dispatched == _P._CLEAR_ATTEMPTS, "it gave up without retrying the submit"


def test_only_the_confirmed_filter_can_stop_a_run():
    """A field nobody has verified must not hard-fail the nightly harvest.

    property[state] is the one whose unfiltered value is known — clearing it took the
    page from 1,049 lots back to 1,293, and it comes back with nothing selected. Another
    property[...] control could carry a non-empty default that has simply never been
    looked at, and refusing to harvest every night over a guess would cost more than the
    bug does. It is cleared, it is logged, and the partial-read guard is the net.
    """
    page = _FakePage({"sortby": "price"}, submit_persists=False)
    src, _ = _harvest_with(page)
    assert page.enumerations == 1, "an unverified field stopped the harvest"


def test_a_page_with_no_submit_is_not_a_silent_pass():
    """Without the submit there is no way to reach the session, so nothing was cleared.

    Emptying the form and calling it done is exactly the failure this whole path exists
    to prevent, so a missing submit control is a refusal, not a shrug.
    """
    page = _FakePage({"state": "NSW"}, has_submit=False)
    src, rows = _harvest_with(page)
    assert rows == [] and page.enumerations == 0
    assert page.dispatched == 1, "it should stop, not retry a control that is absent"


def test_an_unreadable_page_does_not_become_a_second_failure():
    """A page that cannot even be probed is a bigger problem, and it surfaces anyway.

    It comes back as an empty harvest a moment later, which the dead-channel check
    already names correctly. Refusing here would only bury that cause under this one.
    """
    class Broken(_FakePage):
        def evaluate(self, js, *args):
            from sources import proxima as px
            if js is px._FILTER_SNAPSHOT_JS:
                raise RuntimeError("Execution context was destroyed")
            return super().evaluate(js, *args)

    page = Broken()
    src, _ = _harvest_with(page)
    assert page.enumerations == 1, "an unreadable filter state must not block the run"
    assert src.filter_found == {}


def run_all():
    tests = [
        ("address forms parse", test_the_address_forms_proxima_actually_uses),
        ("missing tail stays blank", test_a_missing_tail_is_left_blank_not_guessed),
        ("suburb is not a state", test_a_suburb_is_never_mistaken_for_a_state),
        ("zero means not recorded", test_zero_means_not_recorded_not_zero),
        ("prices parse", test_price_strings_parse),
        ("lot number unpadded", test_lot_number_loses_the_padding_but_not_the_lot),
        ("a lot maps onto the schema", test_a_lot_maps_onto_the_schema),
        ("builder read, never invented", test_the_builder_is_read_from_the_header_never_invented),
        ("estate is the right project", test_the_estate_is_the_project_the_lot_actually_sits_in),
        ("counter is not part of the name", test_the_live_counter_is_not_part_of_the_estates_name),
        ("estate reads the same each harvest", test_the_same_estate_reads_the_same_across_harvests),
        ("a real parenthesis survives", test_a_real_parenthesis_in_a_name_survives),
        ("only an int pair is a counter", test_only_a_pair_of_integers_counts_as_a_counter),
        ("no counter, whole name kept", test_a_project_with_no_counter_keeps_its_whole_name),
        ("a counter-only header still names", test_a_header_that_is_only_a_counter_still_names_something),
        ("no price is not a listing", test_a_lot_with_no_price_is_not_a_listing),
        ("no address is refused", test_a_lot_with_no_address_is_refused),
        ("state falls back to project", test_the_state_falls_back_to_the_project_location),
        ("cross-listed stored once", test_a_cross_listed_lot_is_stored_once_and_counted),
        ("cross-listed collapse is stable", test_two_harvests_of_a_cross_listed_lot_collapse_the_same_way),
        ("different lots both survive", test_genuinely_different_lots_both_survive),
        ("a re-harvest refreshes the counters", test_a_reharvest_refreshes_the_counters_instead_of_freezing_them),
        ("channel matches portal_config", test_the_channel_matches_the_portal_config),
        ("filter: a clean page is left alone", test_an_unfiltered_page_is_left_alone),
        ("filter: a form default is not a filter", test_a_form_default_is_not_a_filter),
        ("filter: the default is matched on its value", test_the_default_is_matched_on_the_value_not_the_field),
        ("filter: a leftover one is cleared first", test_a_leftover_filter_is_cleared_before_anything_is_counted),
        ("filter: emptying the form is not clearing it", test_emptying_the_form_is_not_clearing_the_filter),
        ("filter: one that will not clear stops the harvest", test_a_filter_that_will_not_clear_stops_the_harvest),
        ("filter: only a confirmed filter stops a run", test_only_the_confirmed_filter_can_stop_a_run),
        ("filter: no submit control is not a pass", test_a_page_with_no_submit_is_not_a_silent_pass),
        ("filter: an unreadable page still runs", test_an_unreadable_page_does_not_become_a_second_failure),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] proxima-src: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] proxima-src: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
