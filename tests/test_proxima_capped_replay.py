"""Proxima projects stuck at the 80-lot render cap (plan rank 3, 7 Oct 2026).

THE BUG. search() lends the first captured accordion POST body to every off-index
project. _read_off_index re-aimed that body at the project it was reading -- but handed the
DONOR body back as `_body`, and search() passed the donor body (off_body) to
_top_up_capped. So the "sort flip" re-read the donor project (761, 180 George Parramatta,
3 lots) instead of the capped one. The 6 Oct log, 13 times:

    project 724: render cap reached, sort flip recovered 3 more lot(s) -- 83 in hand

and the three 180 George lots were stored under "Lindfield House" with no builder (ids
17058-17060). The capped projects never got past 80: Live Caringbah stated 140 available
on 26 Sep, THE MAYBELLE 141, The Grande 117, Melbourne Square BLVD 109 -- each read 80.
The STILL SHORT warning could not fire, because off-index projects passed no stated
count, and only price-desc was ever replayed.

The existing top-up test (tests/test_proxima_source.py, "merges the second sort") hands
_top_up_capped a body whose projectId already matches, so it could not see this. The fakes
below answer a replay with the lots of WHATEVER projectId the body carries, filtered by the
body's own sort and price ceiling and capped at 80 -- the server's behaviour as measured
(memory: proxima-render-cap) -- so a body aimed at the wrong project returns the wrong lots.
"""
import logging
import urllib.parse

import pytest

from sources import proxima as px
from sources.proxima import _RENDER_CAP, ProximaSource


def _tile(pid, n, price):
    return {"name": f"Lot {n} Unit {n}, {pid} Example Street, SAMPLETON, NSW, 2000",
            "lot": f"{n:08d}/{n:08d}", "rop": str(price), "packageprice": "0",
            "room": "2", "bathroom": "1", "carspace": "1", "landsize": "0",
            "_status": "For Sale", "_titled": "", "_floorplan": "", "_reserved": "no"}


def _inventory(pid, count, start=400000, step=1000):
    return [_tile(pid, i + 1, start + i * step) for i in range(count)]


BODY = ("keyword_search=&property%5Bproperty_status%5D=SALE"
        "&sort%5Bsort_attributes%5D=price-asc"
        "&property_range_filter%5Brop%5D%5Brange_two%5D=26000000"
        "&projectId={pid}&form_key=AbC123")


def _fields(body):
    out = {}
    for kv in body.split("&"):
        k, _, v = kv.partition("=")
        out[urllib.parse.unquote_plus(k)] = urllib.parse.unquote_plus(v)
    return out


class _Server:
    """The accordion endpoint: projectId, sort and price ceiling, 80 lots at most."""

    def __init__(self, projects, honour_sort=True):
        self.projects = projects            # {pid: [tiles]}
        self.honour_sort = honour_sort
        self.posts = []                     # every body replayed, decoded

    def answer(self, body):
        f = _fields(body)
        self.posts.append(f)
        lots = list(self.projects.get(f.get("projectId", ""), []))
        ceiling = f.get("property_range_filter[rop][range_two]")
        if ceiling:
            lots = [t for t in lots if float(t["rop"]) <= float(ceiling)]
        if self.honour_sort and f.get("sort[sort_attributes]") == "price-desc":
            lots = sorted(lots, key=lambda t: -float(t["rop"]))
        else:
            lots = sorted(lots, key=lambda t: float(t["rop"]))
        return [dict(t) for t in lots[:_RENDER_CAP]]


class _Label:
    def __init__(self, page, pid):
        self.page, self.pid = page, pid

    def get_attribute(self, name):
        return self.pid if name == "data-project_id" else None

    def click(self):
        # expanding fires the page's own accordion POST, which _open_and_read captures
        self.page.expanded = not self.page.expanded
        if self.page.expanded:
            body = BODY.format(pid=self.pid)
            for fn in list(self.page.listeners):
                fn(type("Req", (), {"url": "https://portal/agent/projects/properties/",
                                    "method": "POST", "post_data": body})())


class _Page:
    def __init__(self, server, index, dropdown, headers=None, developers=None):
        self.server = server
        self.developers = developers or {}
        self.index = list(index)            # pids rendered on the index
        self.dropdown = list(dropdown)      # [(pid, name)] in the account dropdown
        self.headers = headers or {}
        self.listeners = []
        self.expanded = False
        self.url = ""

    # --- what search() and _open_and_read touch
    def on(self, _ev, fn):
        self.listeners.append(fn)

    def remove_listener(self, _ev, fn):
        self.listeners.remove(fn)

    def locator(self, _sel):
        page = self
        return type("Loc", (), {"count": lambda _s: len(page.index)})()

    def query_selector_all(self, sel):
        if "tab-label" in sel:
            return [_Label(self, p) for p in self.index]
        return []                            # no availability views

    def query_selector(self, sel):
        for p in self.index:
            if f'"{p}"' in sel:
                return _Label(self, p)
        return None

    def wait_for_selector(self, *a, **k): pass
    def wait_for_timeout(self, *a, **k): pass
    def wait_for_load_state(self, *a, **k): pass

    def evaluate(self, js, *args):
        if js is px._FILTER_SNAPSHOT_JS:
            return {}
        if js is px._COLLECT_JS:
            pid = args[0]
            return {"header": {"project": self.headers.get(pid, f"Project {pid}"),
                               "status": "For Sale", "location": "NSW",
                               "developer": self.developers.get(pid, ""),
                               "sunset": "", "deposit": ""},
                    "props": self.server.answer(BODY.format(pid=pid))}
        if js is px._PROJECT_OPTIONS_JS:
            return [{"id": p, "name": n} for p, n in self.dropdown]
        if js is px._REPLAY_ACCORDION_JS:
            return self.server.answer(args[0][0])
        return None


class _Scraper:
    def __init__(self, page):
        from pathlib import Path
        self.page = page
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


def _harvest(page, stored=None):
    saved = (px.PlaywrightScraper, px.PLAYWRIGHT_AVAILABLE)
    px.PlaywrightScraper = lambda **k: _Scraper(page)
    px.PLAYWRIGHT_AVAILABLE = True
    try:
        src = ProximaSource()
        # Set rather than passed, so the same harness runs against the code before the
        # fix (which has no such parameter) and shows the 83-lot read.
        src.stored_counters = stored or {}
        return src, src.search({})
    finally:
        px.PlaywrightScraper, px.PLAYWRIGHT_AVAILABLE = saved


def _by_project(rows):
    out = {}
    for r in rows:
        out.setdefault(r["source_project_id"], []).append(r)
    return out


# ------------------------------------------------------------------ the 6 Oct shape

def test_an_off_index_project_is_topped_up_from_its_own_lots(caplog):
    """180 George (761) is on the index and lends its body; Lindfield House (724, 107
    lots) is off it and capped. Before the fix: 80 own lots + 3 of 761's = 83."""
    server = _Server({"761": _inventory("761", 3), "724": _inventory("724", 107)})
    # The index states 761's developer and the off-index header states none, so 761's
    # lots re-read under 724 do not collapse into 761's own rows: they become new rows
    # with a blank builder, exactly ids 17058-17060 on 6 Oct.
    page = _Page(server, index=["761"], dropdown=[("724", "Lindfield House")],
                 developers={"761": "Meriton"})
    with caplog.at_level(logging.INFO, logger="spb.scraper.proxima"):
        src, rows = _harvest(page)
    got = _by_project(rows)
    assert len(got["724"]) == 107, f"read {len(got['724'])} of Lindfield House's 107"
    assert all(" 724 Example Street" in r["lot_address"] for r in got["724"]), \
        "a lot from another project was filed under Lindfield House"
    assert len(got["761"]) == 3
    replays = [p for p in server.posts if p.get("sort[sort_attributes]") == "price-desc"]
    assert replays and all(p["projectId"] == "724" for p in replays), \
        [p["projectId"] for p in replays]


def test_both_price_sorts_are_replayed_explicitly():
    server = _Server({"761": _inventory("761", 3), "724": _inventory("724", 120)})
    page = _Page(server, index=["761"], dropdown=[("724", "Lindfield House")])
    _harvest(page)
    sorts = {p.get("sort[sort_attributes]") for p in server.posts
             if p["projectId"] == "724"}
    assert {"price-asc", "price-desc"} <= sorts, sorts


def test_an_index_project_over_the_cap_is_still_topped_up_with_its_own_body():
    server = _Server({"587": _inventory("587", 129)})
    page = _Page(server, index=["587"], dropdown=[],
                 headers={"587": "DAMAC Islands Stage 2 (129/267)"})
    _src, rows = _harvest(page)
    assert len(rows) == 129


def test_a_project_over_160_is_read_in_price_bands():
    """Two sorts reach 2 x 80. Above that the middle is reached by walking the price
    ceiling (property_range_filter[rop][range_two]) down from the dearest lot."""
    server = _Server({"761": _inventory("761", 3), "305": _inventory("305", 305)})
    page = _Page(server, index=["761"], dropdown=[("305", "Atchison and Kenny")])
    src, rows = _harvest(page)
    got = _by_project(rows)
    assert len(got["305"]) == 305, len(got["305"])
    ceilings = [p.get("property_range_filter[rop][range_two]") for p in server.posts
                if p["projectId"] == "305"]
    assert len(set(ceilings)) > 2, "no price band was ever asked for"


def test_a_stale_low_counter_does_not_stop_the_bands():
    """The stored counter can be weeks old: 100 stated in September, 305 tonight. Two
    disjoint full sorts already hold 160 > 100, and the middle must still be read."""
    server = _Server({"761": _inventory("761", 3), "305": _inventory("305", 305)})
    page = _Page(server, index=["761"], dropdown=[("305", "Atchison and Kenny")])
    _src, rows = _harvest(page, stored={"305": 100})
    assert len(_by_project(rows)["305"]) == 305


def test_a_band_that_returns_the_cap_is_split_again():
    """A band holding more than 80 lots comes back as exactly 80: that is the cap talking,
    so the walk keeps going until a band comes back short."""
    lots = _inventory("410", 250, step=10)
    server = _Server({"761": _inventory("761", 3), "410": lots})
    page = _Page(server, index=["761"], dropdown=[("410", "Big Building")])
    _src, rows = _harvest(page)
    assert len(_by_project(rows)["410"]) == 250


# ------------------------------------------------------------------ saying so by name

def test_a_project_still_short_of_its_stored_counter_is_named(caplog):
    """The portal ignores the sort on this project, so both sorts return the same 80. The
    stored counter (141 on 26 Sep) is what lets the shortfall be said at all."""
    server = _Server({"761": _inventory("761", 3), "720": _inventory("720", 141)},
                     honour_sort=False)
    page = _Page(server, index=["761"], dropdown=[("720", "THE MAYBELLE")])
    # a body with no ceiling field cannot be banded either
    old = BODY
    globals()["BODY"] = BODY.replace(
        "&property_range_filter%5Brop%5D%5Brange_two%5D=26000000", "")
    try:
        with caplog.at_level(logging.WARNING, logger="spb.scraper.proxima"):
            src, rows = _harvest(page, stored={"720": 141})
    finally:
        globals()["BODY"] = old
    assert len(_by_project(rows)["720"]) == _RENDER_CAP
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "THE MAYBELLE" in said and "141" in said, said
    short = {s["pid"]: s for s in src.short_projects}
    assert short["720"]["read"] == _RENDER_CAP and short["720"]["stated"] == 141


@pytest.mark.parametrize("count", [_RENDER_CAP, _RENDER_CAP + 3, 2 * _RENDER_CAP])
def test_a_project_ending_on_a_cap_signature_is_named_even_with_no_counter(count, caplog):
    """80, 83 (80 + the old donor's 3) and 160 are what a capped read looks like. Said by
    name whether or not any counter is known."""
    src = ProximaSource()
    src.stored_counters = {}
    with caplog.at_level(logging.WARNING, logger="spb.scraper.proxima"):
        src._warn_if_short("999", "Nova Reve", count, None)
    assert any("Nova Reve" in r.getMessage() for r in caplog.records)
    assert src.short_projects and src.short_projects[0]["read"] == count


def test_an_uncapped_project_is_not_named():
    src = ProximaSource()
    src.stored_counters = {}
    src._warn_if_short("999", "No 1 Zoe", 77, 77)
    assert src.short_projects == []


def test_the_stored_counter_is_read_from_the_database(tmp_path):
    """The off-index header states no counter; the last one the index stated is in the
    database, refreshed in place each time the project was on the index."""
    import sqlite3
    from database import ResearchDatabase
    path = tmp_path / "px.db"
    ResearchDatabase(path)
    con = sqlite3.connect(str(path))
    for i, (pid, avail, when) in enumerate([("720", 141, "2026-09-26T14:00:00"),
                                            ("720", 150, "2026-09-01T14:00:00"),
                                            ("724", None, "2026-10-06T14:00:00"),
                                            ("724", 107, "2026-09-27T14:00:00")]):
        con.execute("INSERT INTO buildings (source_channel, source_project_id, "
                    "project_available, checked_at, dedup_key, content_hash) "
                    "VALUES ('Proxima', ?, ?, ?, ?, ?)", (pid, avail, when, f"k{i}", f"h{i}"))
    con.commit()
    con.close()
    assert px.stored_project_counters(path) == {"720": 141, "724": 107}
    assert px.stored_project_counters(tmp_path / "missing.db") == {}


def test_a_failed_replay_keeps_the_lots_already_in_hand():
    src = ProximaSource()
    src.stored_counters = {}
    first = _inventory("724", _RENDER_CAP)

    class _Boom:
        class page:
            @staticmethod
            def evaluate(js, arg):
                raise RuntimeError("session lapsed")

    out = src._top_up_capped(_Boom(), "724", BODY.format(pid="724"), list(first), 107,
                             name="Lindfield House")
    assert len(out) == _RENDER_CAP


def test_a_project_proven_whole_is_not_named_even_on_a_signature():
    """83 lots really can be 83 lots: when the two price sorts overlap, the read is whole
    and a cap signature alone is no reason to warn."""
    server = _Server({"761": _inventory("761", 3), "730": _inventory("730", 83)})
    page = _Page(server, index=["761"], dropdown=[("730", "Exactly 83")])
    src, rows = _harvest(page)
    assert len(_by_project(rows)["730"]) == 83
    assert src.short_projects == [], src.short_projects


# ------------------------------------------------- repair round 1 (7 Oct 2026): the walk

def _tiles(pid, prices):
    return [_tile(pid, i + 1, p) for i, p in enumerate(prices)]


class _ExclusiveServer(_Server):
    """The same endpoint with a price ceiling that EXCLUDES its own value -- what the live
    portal does is unverified, and the walk must not depend on it."""

    def answer(self, body):
        f = _fields(body)
        self.posts.append(f)
        lots = list(self.projects.get(f.get("projectId", ""), []))
        ceiling = f.get("property_range_filter[rop][range_two]")
        if ceiling:
            lots = [t for t in lots if float(t["rop"]) < float(ceiling)]
        desc = f.get("sort[sort_attributes]") == "price-desc"
        lots = sorted(lots, key=lambda t: -float(t["rop"]) if desc else float(t["rop"]))
        return [dict(t) for t in lots[:_RENDER_CAP]]


def test_eighty_lots_at_one_price_are_never_called_a_whole_read(caplog):
    """240 lots, 90 of them at $600,000: the walk read 155, marked the read complete and
    said nothing -- 'the band met the cheapest 80' was tested before 'the band cannot split
    one price'. (data verifier)"""
    prices = ([400000 + i * 1000 for i in range(75)] + [600000] * 90
              + [700000 + i * 1000 for i in range(75)])
    server = _Server({"761": _inventory("761", 3), "902": _tiles("902", prices)})
    page = _Page(server, index=["761"], dropdown=[("902", "One Price Tower")])
    with caplog.at_level(logging.WARNING, logger="spb.scraper.proxima"):
        src, rows = _harvest(page)
    read = len(_by_project(rows)["902"])
    assert read < 240                                  # the portal cannot give more
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "One Price Tower" in said, said
    assert any(s["pid"] == "902" for s in src.short_projects), src.short_projects


def test_the_walk_reads_everything_whether_the_ceiling_is_inclusive_or_not():
    """300 lots priced in blocks of 6. Against a server whose ceiling excludes its own
    value, the lots sharing the boundary price were never asked for: 292 read and the read
    called complete. (data verifier)"""
    prices = [400000 + (i // 6) * 5000 for i in range(300)]
    for cls in (_Server, _ExclusiveServer):
        server = cls({"761": _inventory("761", 3), "904": _tiles("904", prices)})
        page = _Page(server, index=["761"], dropdown=[("904", "Blocks of Six")])
        src, rows = _harvest(page)
        assert len(_by_project(rows)["904"]) == 300, (cls.__name__,
                                                      len(_by_project(rows)["904"]))
        assert src.short_projects == [], (cls.__name__, src.short_projects)
