"""The projects Proxima's index never renders — two thirds of the portfolio.

FOUND FROM A LISTING THE APP DID NOT HOLD. Coleen, 18 Aug 2026: "We just searched for
properties in Liverpool NSW under 800K and there is nothing coming from the app though
there is a 3bed in proxima for 740K." There is no Liverpool project on the index. There
are two in the account's own dropdown — The Pinnacle Tower A and Tower B — and Tower B
holds exactly that lot:

    Lot 73 Unit B1001, 10 Norfolk Street, LIVERPOOL   $744,581   3 bed   For Sale

Every project label on the index carries class "top-projects". The index renders 40; the
dropdown lists 118. The other 78 are neither filtered nor archived — they simply never
appear on the page the harvest reads. Nothing reported it, because 40 projects looked
exactly like a complete read.

Measured after the fix: 3,391 lots from 118/118 projects, against 1,319 from 40.
"""
import pytest

from sources.proxima import ProximaSource, _PROJECT_OPTIONS_JS, _with_field


TILES = [
    {"name": "Lot 73 Unit B1001, 10 Norfolk Street, LIVERPOOL, NSW, 2170",
     "lot": "73", "room": "003", "bathroom": "2", "carspace": "1",
     "landsize": "0", "rop": "744581", "packageprice": "0",
     "_status": "For Sale", "_titled": "", "_floorplan": ""},
    {"name": "Lot 86 Unit B1202, 10 Norfolk Street, LIVERPOOL, NSW, 2170",
     "lot": "86", "room": "002", "bathroom": "2", "carspace": "1",
     "landsize": "0", "rop": "593224", "packageprice": "0",
     "_status": "For Sale", "_titled": "", "_floorplan": ""},
]

BODY = ("keyword_search=&property%5Bproperty_status%5D=SALE"
        "&sort%5Bsort_attributes%5D=price-asc&projectId=593&form_key=AbC123")


class _Scraper:
    """Returns TILES for whatever body it is handed, and records the body."""
    def __init__(self, tiles=None):
        self.seen = []
        self._tiles = TILES if tiles is None else tiles

        outer = self

        class _Page:
            def evaluate(self, js, arg):
                outer.seen.append(arg[0] if isinstance(arg, list) else arg)
                return outer._tiles

        self.page = _Page()


# ------------------------------------------------------------------ aiming the POST

def test_the_post_is_re_aimed_at_the_off_index_project():
    """The body must be the page's own, with only projectId changed — see _with_field."""
    src = ProximaSource()
    sc = _Scraper()
    src._read_off_index(sc, "275", "The Pinnacle Tower B", BODY)
    sent = sc.seen[0]
    assert "projectId=275" in sent
    assert "projectId=593" not in sent
    # everything else survives byte for byte, including the form key
    for keep in ("keyword_search=", "property%5Bproperty_status%5D=SALE", "form_key=AbC123"):
        assert keep in sent


def test_no_captured_body_means_no_read_rather_than_a_hand_built_one():
    """A minimal body returned 10 lots where the page showed 80, so one is never invented."""
    assert ProximaSource()._read_off_index(_Scraper(), "275", "Tower B", "") is None


def test_a_project_that_returns_nothing_is_not_a_row():
    assert ProximaSource()._read_off_index(_Scraper(tiles=[]), "275", "T", BODY) is None
    assert ProximaSource()._read_off_index(_Scraper(tiles=None if False else []),
                                           "275", "T", BODY) is None


def test_a_replay_failure_is_reported_not_raised():
    class _Boom:
        class page:
            @staticmethod
            def evaluate(js, arg):
                raise RuntimeError("network")
    assert ProximaSource()._read_off_index(_Boom(), "275", "Tower B", BODY) is None


# ------------------------------------------------------------------ the thin header

def test_the_header_carries_the_dropdown_name_and_nothing_invented():
    """An off-index project states no developer, location or availability counter. Those
    stay blank rather than being guessed — the lot's own address carries what matters."""
    data = ProximaSource()._read_off_index(_Scraper(), "275", "The Pinnacle Tower B", BODY)
    h = data["header"]
    assert h["project"] == "The Pinnacle Tower B"
    for blank in ("developer", "location", "status", "sunset", "deposit"):
        assert h[blank] == "", f"{blank} should be blank, not guessed"


def test_the_shape_matches_the_index_reader_so_row_cannot_tell_them_apart():
    data = ProximaSource()._read_off_index(_Scraper(), "275", "Tower B", BODY)
    assert set(data) >= {"header", "props"}
    assert isinstance(data["props"], list) and data["props"]


def test_the_lot_still_parses_its_own_suburb_and_state():
    """This is what makes an off-index lot findable at all: the header says nothing about
    location, so the address has to carry it."""
    src = ProximaSource()
    data = src._read_off_index(_Scraper(), "275", "The Pinnacle Tower B", BODY)
    rows = [r for r in (src._row(d, data["header"], "275") for d in data["props"]) if r]
    assert len(rows) == 2
    liv = [r for r in rows if r["suburb"] == "Liverpool"]
    assert len(liv) == 2, "both lots are in Liverpool"
    assert all(r["state"] == "NSW" for r in liv)
    assert all(r["postcode"] == "2170" for r in liv)
    # Coleen's lot, at the price she quoted.
    three = [r for r in liv if r["bedrooms"] == 3]
    assert len(three) == 1
    assert three[0]["advertised_package_price"] == 744581.0
    assert three[0]["source_project_id"] == "275"


def test_no_builder_is_invented_for_an_off_index_project():
    """The index header is where a project names its developer. Off-index there is none,
    and a plausible guess is exactly what this file refuses to make."""
    src = ProximaSource()
    data = src._read_off_index(_Scraper(), "275", "Tower B", BODY)
    rows = [r for r in (src._row(d, data["header"], "275") for d in data["props"]) if r]
    assert all(r["builder_name"] == "" for r in rows)
    assert all(r["attribution_scope"] == "project" for r in rows)


# ------------------------------------------------------------------ enumerating them

def test_the_dropdown_reader_only_accepts_numeric_ids():
    """The select carries a placeholder option ("Project Name") with an empty value."""
    assert 'select[name="projects"]' in _PROJECT_OPTIONS_JS
    assert "/^\\d+$/" in _PROJECT_OPTIONS_JS, "non-numeric option values must be dropped"


def test_the_counter_starts_at_zero():
    assert ProximaSource().off_index == 0
