"""Dropbox folders whose price list is not NAMED like one (plan rank 10, 7 Oct 2026).

A PDF in a Dropbox folder was downloaded only if its name said price/stock/availab/sales
list. On 6 Oct, 9 E-Agent folders were logged "no price list in this folder" -- among them
Murcia, whose new file "report_murcia-residences_2026-8-22_134434 (1)-2.pdf" is a price
export (25 lots, unread since 22 Sep), and eight VIC townhouse folders ("Fraser Rise
Hattan.pdf", "Phoenix Cranbourne New.pdf") that have never produced a row. Meanwhile the
bare "stock" in the name rule picked six "Stockland - EOI Request Form.pdf" forms.

And none of these fetch problems -- no price list, a private link, an empty folder --
reached data/unreadable_stocklists.json, so the Settings tab listed none of them. Worse,
the harvest wrote that file only on a FAILED night: a good night returned before the
write, so the published list was whatever an old failed night had left.

All offline: _dropbox_entries and the session are fakes.
"""
import json
import logging

import pytest

import sources.remote_stocklist as RS


def _entries(*items):
    return [(n, d, f"https://www.dropbox.com/x/{n}?dl=0") for n, d in items]


class _Resp:
    def __init__(self, size=40_000, status=200):
        self.status_code = status
        self.ok = status == 200
        self.headers = {"Content-Length": str(size)}
        self._body = b"%PDF-1.4 " + b"x" * max(0, size - 9)
        self.closed = False

    @property
    def content(self):
        return self._body

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def close(self):
        self.closed = True


class _Sess:
    def __init__(self, sizes=None):
        self.got = []
        self.sizes = sizes or {}

    def get(self, url, **kw):
        self.got.append(url)
        for name, size in self.sizes.items():
            if name in url:
                return _Resp(size)
        return _Resp()


@pytest.fixture
def folder(monkeypatch):
    tree = {}

    def entries(sess, url):
        return tree.get(url, tree.get("__root__", []))

    monkeypatch.setattr(RS, "_dropbox_entries", entries)
    return tree


def _names(out):
    return [f.label.split(" / ", 1)[-1] for f in out if not f.problem]


def test_murcia_is_read_although_its_name_says_no_price(folder):
    """The 6 Oct folder, as the log listed it."""
    folder["__root__"] = _entries(("report_murcia-residences_2026-8-22_134434 (1)-2.pdf", False),
                                  ("Schedule of Finishes.pdf", False),
                                  ("SIL0153 Murcia DIGITAL_Brochure_V3.pdf", False))
    sess = _Sess()
    out = RS._fetch_dropbox("https://www.dropbox.com/scl/fo/abc", "Access Portal", sess)
    assert _names(out) == ["report_murcia-residences_2026-8-22_134434 (1)-2.pdf"], _names(out)
    assert out[0].meta.get("guessed") is True
    assert not any("Brochure" in u or "Finishes" in u for u in sess.got)


@pytest.mark.parametrize("names, want", [
    (["Fraser Rise Hattan.pdf", "Hattan_Westwood_Stage_10B_Release_Brochure.pdf"],
     ["Fraser Rise Hattan.pdf"]),
    (["Phoenix Cranbourne New.pdf", "WOL 2087-4 Phoenix Brochure_4.pdf"],
     ["Phoenix Cranbourne New.pdf"]),
    (["713_BARKLY_FOOTSCRAY_inclusions list (1).pdf", "713_BARKLY_ST_FLOORPLANS (1).pdf",
      "BARKLY_CUT_THROUGH - Copy.jpg"], []),
])
def test_the_6_oct_townhouse_folders(folder, names, want):
    folder["__root__"] = _entries(*[(n, False) for n in names])
    out = RS._fetch_dropbox("https://www.dropbox.com/scl/fo/abc", "Access Portal", _Sess())
    assert _names(out) == want
    if not want:
        assert len(out) == 1 and "no price list" in out[0].problem


def test_a_named_price_list_still_wins_and_nothing_is_guessed(folder):
    folder["__root__"] = _entries(("Murcia Price List.pdf", False),
                                  ("report_murcia.pdf", False))
    sess = _Sess()
    out = RS._fetch_dropbox("https://www.dropbox.com/scl/fo/abc", "Access Portal", sess)
    assert _names(out) == ["Murcia Price List.pdf"]
    assert not out[0].meta.get("guessed")


def test_stockland_is_not_a_stock_list(folder):
    folder["__root__"] = _entries(("Stockland - EOI Request Form.pdf", False),
                                  ("Stockland VOI Form.pdf", False))
    out = RS._fetch_dropbox("https://www.dropbox.com/scl/fo/abc", "Access Portal", _Sess())
    assert _names(out) == [], _names(out)


@pytest.mark.parametrize("name", ["Stock List.pdf", "Stocklist Oct.pdf", "VIC Stock.pdf",
                                  "Current Stock Update.pdf", "stock_list.pdf"])
def test_real_stock_names_still_match(name):
    assert RS._DBX_STOCK_FILE.search(name), name


def test_a_file_in_a_price_named_subfolder_is_taken_by_where_it_sits(folder):
    folder["__root__"] = _entries(("Summary.xlsx", False), ("Price List", True))
    folder["https://www.dropbox.com/x/Price List?dl=0"] = _entries(
        ("Murcia Residences.pdf", False), ("Brochure.pdf", False))
    out = RS._fetch_dropbox("https://www.dropbox.com/scl/fo/abc", "Access Portal", _Sess())
    assert sorted(_names(out)) == ["Price List/Murcia Residences.pdf", "Summary.xlsx"]


def test_a_large_guess_is_not_downloaded_whole(folder):
    """A guess is a small file: a 30 MB brochure with an innocent name is refused at the
    Content-Length, and reported as no price list, not as a failure."""
    folder["__root__"] = _entries(("Edison Park VB.pdf", False))
    sess = _Sess(sizes={"Edison": 30 * 1024 * 1024})
    out = RS._fetch_dropbox("https://www.dropbox.com/scl/fo/abc", "Access Portal", sess)
    assert len(out) == 1 and out[0].problem and not out[0].data
    assert "no price list" in out[0].problem


# ------------------------------------------------------------------ reported, by heading

class _Scraper:
    def throttle(self):
        pass


def _problem_piece(problem):
    return RS.Fetched(label="Access Portal", url="https://www.dropbox.com/scl/fo/x",
                      problem=problem)


@pytest.mark.parametrize("problem, outcome", [
    ("no price list in this folder \u2014 it holds only a.pdf. Nothing to harvest",
     "no price list"),
    ("this is a link into the owner's private Dropbox file browser, not a share link",
     "private link"),
    ("folder listing came back empty (layout change or dead link)", "empty folder"),
])
def test_every_fetch_problem_is_listed_with_its_heading(monkeypatch, problem, outcome):
    import harvest_manifest as HM
    from sources import e_agent as EA
    from sources import spreadsheet_extract as SE
    monkeypatch.setattr(SE, "UNREADABLE", [])
    HM.reset()
    monkeypatch.setattr(EA.remote, "fetch_stocklist", lambda *a, **k: [_problem_piece(problem)])
    src = EA.EAgentSource.__new__(EA.EAgentSource)
    src._http = None
    link = {"href": "https://www.dropbox.com/scl/fo/x?rlkey=k&dl=0", "label": "Access Portal",
            "heading": "Fraser Rise Hattan"}
    rows = src._parse_stocklist(_Scraper(), link, "", "project", "VIC", "Townhouse",
                                page="townhouses-vic")
    assert rows == []
    assert len(SE.UNREADABLE) == 1, SE.UNREADABLE
    shown = SE.UNREADABLE[0]["file"]
    assert "Fraser Rise Hattan" in shown and "dropbox.com/scl" not in shown, shown
    for bad in "/?#%:":
        assert bad not in shown, f"{bad!r} would make the Settings tab drop the name"
    e = HM.ENTRIES[0]
    assert (e["outcome"], e["heading"], e["page"], e["state"]) == \
        (outcome, "Fraser Rise Hattan", "townhouses-vic", "VIC")
    assert "dropbox.com" not in json.dumps(e).lower(), "a capability link reached the manifest"


def test_a_good_night_writes_the_unreadable_list_too(tmp_path, monkeypatch):
    """It was written only on the failure path, after `return 0` for a good night."""
    import harvest_buildings as HB
    from sources import spreadsheet_extract as SE
    monkeypatch.setattr(SE, "UNREADABLE", [{"kind": "remote", "file": "Murcia - no price "
                                            "list in the folder.", "why": "x"}])
    written = {}
    monkeypatch.setattr(HB, "_record_unreadable_stocklists",
                        lambda: written.setdefault("unreadable", True))
    monkeypatch.setattr(HB, "_require_readers", lambda: True)

    class _Nothing:
        def __init__(self, *a, **k):
            self.username = self.password = ""
            self.configured = False
            self.projects_with_stock = self.projects_seen = 0
            self.cross_listed, self.filter_found, self.project_log = [], {}, []
            self.short_projects = []

        def search(self, *a, **k):
            return []

    monkeypatch.setattr(HB, "BuilderRegistry", lambda: type(
        "R", (), {"get_all_builders": lambda s: []})())
    monkeypatch.setattr(HB, "ResearchDatabase", lambda: type("D", (), {
        "building_reads_by_channel": lambda s: {},
        "building_counts_by_channel": lambda s: []})())
    import harvest_manifest as HM
    monkeypatch.setattr(HM, "write", lambda *a, **k: written.setdefault("manifest", True))
    assert HB.harvest(eagent=False, portals=False, email=False, proxima=False,
                      rea=False) == 0
    assert written == {"unreadable": True, "manifest": True}, written
