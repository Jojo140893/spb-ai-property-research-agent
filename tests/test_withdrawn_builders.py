"""
Withdrawn builders: Bathla, 23 Oct 2026.

Colin, call of 23 Oct 2026: "Take off Butler. They went off bust. We're no longer using
them. So just, just delete them." Bathla Group entered voluntary administration on 25 Aug.

The trap these tests exist for: most of Bathla's stock came through PROXIMA (198 of its
226 rows in the 26 Sep database), not Bathla's own portal. Switching the portal off alone
would have left those 198 recommendable. So the rule is about the builder, and the search
test puts Bathla lots on Proxima as well as on the portal.

The second trap, found the night the rule went live: Proxima stores a project's developer
only on nights it reads as a company, so project 499 named Bathla on 9 Sep and nobody from
22 Sep, and its blank-builder lots were still on the live NSW answer. So a blank-builder
lot in one of a withdrawn builder's projects is withdrawn with it (builder_names), and the
tests at the bottom follow it through publish, search, Settings, parity and the replay.

The third, found on review of the second: two of Bathla's Box Hill lots came back blank
under ANOTHER project (593 -> 724), which no row naming Bathla is filed under. Only
superseded_by still joined them to the rows that named it, so a blank-builder capture
joined to a withdrawn row goes too, and the last section follows that the same way.

Each test first shows the same input WITHOUT the rule reaching the place it guards, so a
pass means the rule did the work and not that the input never got there.
"""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import builder_names  # noqa: E402
from builder_names import (WITHDRAWN_BUILDERS, BuilderNameCanonicaliser,  # noqa: E402
                           is_withdrawn_builder)
import _candidates as C  # noqa: E402
from tests import _synthetic_stock as S  # noqa: E402


def _never(_name):
    return False


# ------------------------------------------------------------------------ the rule

def test_every_spelling_colin_merged_is_withdrawn():
    for name in ("Bathla", "Bathla Group", "Bathla Development", "BATHLA DEVELOPMENTS",
                 "bathla  group"):
        assert is_withdrawn_builder(name), name


def test_other_builders_and_blanks_are_not_withdrawn():
    for name in ("Hermitage Homes", "Torsion Homes", "Atchison and Kenny", "", None):
        assert not is_withdrawn_builder(name), name


def test_a_name_that_only_contains_bathla_is_not_withdrawn():
    """Nothing looser than the client's own aliases: a new spelling is added there, by
    someone with authority, not caught by a substring."""
    assert not is_withdrawn_builder("Abathla Homes")


def test_each_withdrawn_name_is_the_name_its_rows_are_canonicalised_to():
    """Spelled any other way it withdraws nothing. "Bathla Developments" here would leave
    every Bathla row in search, because they all canonicalise to "Bathla Development"."""
    canon = BuilderNameCanonicaliser()
    for name in WITHDRAWN_BUILDERS:
        assert canon.canonical(name) == name


# -------------------------------------------- search: Clare's hook and the Research tab

def _stock_with_bathla():
    rows = S.stock(fresh=40, estates=4)
    for i, r in enumerate(rows[:8]):
        on_proxima = i < 6
        r.update(builder_name=("Bathla Group", "Bathla Development", "Bathla")[i % 3],
                 source_channel="Proxima" if on_proxima else "Direct Builder Portal (live)",
                 source_project_id="bathla-1" if on_proxima else None,
                 estate_name="Bathla Rise", source_doc="doc-bathla", suburb="Box Hill",
                 state="VIC")
    return rows


def _cards(resp):
    return list(resp.get("shortlist") or []) + list(resp.get("needs_confirming") or [])


def _bathla_cards(resp):
    return [c for c in _cards(resp) if "bathla" in str(c.get("builder_name") or "").lower()]


@pytest.fixture(scope="module")
def research(tmp_path_factory):
    path = S.write(tmp_path_factory.mktemp("withdrawn"), _stock_with_bathla())
    mp = pytest.MonkeyPatch()
    mp.setenv("SPB_SNAPSHOT_JSON", str(path))
    mp.delenv("SPB_MAX_CANDIDATES", raising=False)
    mp.delenv("SPB_SHORTLIST_MAX", raising=False)
    mp.setattr(C, "datetime", S.Sep16)
    import research as module
    mp.setattr(module, "EXPLAIN_BUDGET_MS", 120000)
    mp.setattr(module, "REQUEST_DEADLINE_MS", 600000)
    yield module
    mp.undo()


def _never_row(_row, _projects=frozenset(), _captures=frozenset()):
    return False


def test_without_the_rule_the_search_would_offer_bathla(research, monkeypatch):
    monkeypatch.setattr(C, "is_withdrawn_row", _never_row)
    assert _bathla_cards(S.run(research)), "premise: the Bathla lots reach the shortlist"


def test_no_caller_of_the_search_is_offered_a_withdrawn_builder(research):
    unpaged = S.run(research)                                        # Clare's hook
    paged = S.run(research, explain=True, offset=0, limit=100)       # the Research tab
    for name, resp in (("unpaged (Clare's hook)", unpaged), ("paged (Research tab)", paged)):
        assert not _bathla_cards(resp), name
        assert resp["shortlist_count"] == 32, name                   # 40 lots, 8 Bathla


# --------------------------------------------------------------------------- harvest

def _portal_source(monkeypatch):
    from sources import builder_portals as BP
    registry = SimpleNamespace(get_builders_by_state=lambda state: [
        {"builder_name": "Bathla", "portal_url": "https://portal.bathla.com.au"},
        {"builder_name": "Hermitage Homes", "portal_url": "https://hermitage.example.com"}])
    source = BP.BuilderPortalSource(registry=registry)
    signed_in = []
    monkeypatch.setattr(BP, "PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(source, "_scrape_builder",
                        lambda builder, filters: signed_in.append(builder["builder_name"]) or [])
    return BP, source, signed_in


def test_without_the_rule_the_harvest_would_sign_in_to_bathla(monkeypatch):
    BP, source, signed_in = _portal_source(monkeypatch)
    monkeypatch.setattr(BP, "is_withdrawn_builder", _never)
    source.search({"state": "NSW"})
    assert signed_in == ["Bathla", "Hermitage Homes"]


def test_the_harvest_does_not_sign_in_to_a_withdrawn_builders_portal(monkeypatch):
    _BP, source, signed_in = _portal_source(monkeypatch)
    source.search({"state": "NSW"})
    assert signed_in == ["Hermitage Homes"]


# ----------------------------------------------------- the Settings tab and its checks

def _registry():
    return SimpleNamespace(get_all_builders=lambda: [
        {"builder_name": "Bathla", "portal_url": "https://portal.bathla.com.au"},
        {"builder_name": "Hermitage Homes", "portal_url": "https://hermitage.example.com"}])


def test_the_sign_in_check_does_not_list_a_withdrawn_builder(monkeypatch):
    import portal_login
    monkeypatch.setattr(portal_login, "BuilderRegistry", _registry)
    monkeypatch.setattr(portal_login, "is_withdrawn_builder", _never)
    assert "Bathla" in [label for _s, label, _u in portal_login._targets()], "premise"
    monkeypatch.undo()
    monkeypatch.setattr(portal_login, "BuilderRegistry", _registry)
    labels = [label for _s, label, _u in portal_login._targets()]
    assert "Bathla" not in labels
    assert "Hermitage Homes" in labels


def _db(rows):
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE buildings (source_channel TEXT, builder_name TEXT, "
                 "superseded_by TEXT, date_checked TEXT, checked_at TEXT)")
    conn.executemany("INSERT INTO buildings VALUES (?,?,?,?,?)", rows)
    return conn


def _portal_health(tmp_path):
    import build_web
    today = datetime.now().strftime("%d/%m/%Y")
    portal = "Direct Builder Portal (live)"
    # Bathla's rows stopped refreshing when its portal stopped being read.
    rows = ([(portal, "Bathla", None, "01/09/2026", None)] * 30
            + [(portal, "Hermitage Homes", None, today, None)] * 30)
    return build_web._export_channel_health(tmp_path, _db(rows))


def test_the_settings_tab_does_not_raise_an_alarm_for_a_withdrawn_portal(tmp_path,
                                                                         monkeypatch):
    import build_web
    monkeypatch.setattr(build_web, "_login_status", lambda: {})
    monkeypatch.setattr(builder_names, "is_withdrawn_builder", _never)
    before = _portal_health(tmp_path)
    assert "Bathla" in before["needs_signin"], "premise: a stale Bathla row raises an alarm"
    monkeypatch.undo()
    monkeypatch.setattr(build_web, "_login_status", lambda: {})
    payload = _portal_health(tmp_path)
    assert [p["channel"] for p in payload["portals"]] == ["Hermitage Homes"]
    assert "Bathla" not in payload["needs_signin"]


# ---------------------------------------------------------------------------- parity

def _lot(content_hash, builder):
    return {"content_hash": content_hash, "builder_name": builder, "source_channel": "Proxima",
            "source_project_id": "p-" + content_hash, "date_checked": "23/10/2026",
            "availability_status": "Available", "price": 750000.0, "state": "NSW",
            "bedrooms": 3}


def test_parity_does_not_count_a_withdrawn_builders_lots_as_missing():
    """Proxima keeps listing Bathla's lots and the harvest keeps storing them; parity reads
    its side from that database. Without this, every night would report each of those lots
    as listed by a source and hidden by the app."""
    import parity_check as P
    catalogue = SimpleNamespace(db={"h1": _lot("h1", "Bathla Group"),
                                    "h2": _lot("h2", "Hermitage Homes")},
                                row=lambda h: None)

    def cells(rec):
        return {"price_kind": "package", "price_in_text": True}

    def overseas(rec, snap):
        return False

    import search_regressions as SR
    today = date(2026, 10, 23)
    every = P.obvious_lots(catalogue, today, cells, overseas)
    assert {lot["hash"] for lot in every} == {"h1", "h2"}, "premise"
    kept = P.obvious_lots(catalogue, today, cells, overseas,
                          withdrawn=SR.withdrawn_rule(C, catalogue))
    assert {lot["hash"] for lot in kept} == {"h2"}


def _read_tonight():
    """One E-Agent link read tonight, as the harvest's manifest writes it (schema 1)."""
    return {"entries": [{
        "id": 1, "channel": "E-Agent", "kind": "stock link", "name": "Parity Rise",
        "page": "house-and-land-vic", "state": "VIC", "heading": "Parity Homes",
        "label": "Price List", "stocklist_key": "0123456789abcdef", "outcome": "read",
        "printed": 40, "stored": {"new": 2, "updated": 3, "unchanged": 35, "rejected": 0},
        "pieces": [{"name": "Parity Rise Price List.pdf", "outcome": "read", "printed": 40,
                    "why": ""}]}]}


def _last_night():
    """Bathla's portal and an ordinary E-Agent link both stored lots last night."""
    return [{"night": "22/10/2026", "states": {},
             "manifest": {"bathla": 8, "gone": 120},
             "manifest_labels": {"bathla": "Direct Builder Portal (live) - Bathla",
                                 "gone": "E-Agent - house-and-land-vic - Gone Homes"}}]


def test_parity_names_a_withdrawn_portal_it_stopped_reading_instead_of_failing(monkeypatch):
    """The harvest stops reading Bathla's portal, so for a week its link "yielded lots in
    the last week and 0 tonight". That is the point, not a fault: named, not failed. An
    ordinary link that goes quiet still fails."""
    import parity_check as P
    monkeypatch.setattr(builder_names, "is_withdrawn_builder", _never)
    failures, _s, _n = P.read_bar(_read_tonight(), _last_night(), "23/10/2026")
    assert "Bathla" in failures[0]["text"], "premise: without the rule it fails"
    monkeypatch.undo()
    failures, summary, notes = P.read_bar(_read_tonight(), _last_night(), "23/10/2026")
    assert len(failures) == 1 and "Gone Homes" in failures[0]["text"], failures
    assert "Bathla" not in failures[0]["text"]
    assert summary["vanished"] == 1
    assert any("withdrawn builder" in n and "Bathla" in n for n in notes), notes


def test_parity_gets_the_rule_from_the_search_bundle():
    """measure() and the replay's obvious-rows check ask the bundle's _candidates for
    is_withdrawn_row, withdrawn_projects and withdrawn_captures
    (search_regressions.withdrawn_rule), and fall back to is_withdrawn_builder for an older
    bundle. A rename there would quietly switch the checkers' half of the rule off."""
    assert C.is_withdrawn_builder("Bathla Group")
    projects = C.withdrawn_projects([{"builder_name": "Bathla Group",
                                      "source_channel": "Proxima", "source_project_id": "499"}])
    assert C.is_withdrawn_row({"builder_name": "", "source_channel": "Proxima",
                               "source_project_id": "499"}, projects)
    rows = [{"builder_name": "Bathla Group", "content_hash": "v2:named",
             "superseded_by": "v2:capture"}, {"builder_name": "", "content_hash": "v2:capture"}]
    captures = C.withdrawn_captures(rows, C.withdrawn_projects(rows))
    assert C.is_withdrawn_row(rows[1], frozenset(), captures)


def test_the_search_function_bundle_ships_the_rule():
    """_candidates.py imports builder_names at module level. Left out of the bundle, every
    deployed search would 500 (the build's import probe refuses that before it ships)."""
    import build_web
    assert "builder_names.py" in build_web._FUNCTION_ROOT_MODULES


# ------------------------------------------- a withdrawn builder's project, named blank
#
# Proxima's project 499 "Kensington Lot1 Townhouse" (Riverstone) named "Bathla Development"
# on 9 Sep. From 22 Sep it returned the same lots at the same prices with NO builder, and on
# the night the name rule went live six of them were on the live NSW answer, one ranked #2
# for Riverstone. Every test above keys on the name, so none of them could see those lots.
# The new names are reached through their modules inside each test, so on a tree without
# them each test fails on its own rather than the whole file failing to import.

PROXIMA = "Proxima"
PORTAL = "Direct Builder Portal (live)"


def _rec(builder, project, channel=PROXIMA, **kw):
    return dict({"builder_name": builder, "source_channel": channel,
                 "source_project_id": project, "superseded_by": None}, **kw)


def _kensington():
    """Project 499 as the database held it on 23 Oct: the 9 Sep captures naming Bathla are
    all superseded, and the blank-builder captures of the same lots are what is live."""
    return [_rec("Bathla Development", "499", lot_number="53", superseded_by="v2:blank-53"),
            _rec("Bathla Development", "499", lot_number="64", superseded_by="v2:blank-64"),
            _rec("", "499", lot_number="53"),
            _rec("", "499", lot_number="64")]


def test_a_blank_builder_lot_in_a_withdrawn_builders_project_is_withdrawn():
    rows = _kensington()
    live = [r for r in rows if not r["superseded_by"]]
    assert not any(is_withdrawn_builder(r["builder_name"]) for r in live), (
        "premise: the name rule lets every live lot of the project through")
    projects = builder_names.withdrawn_projects(rows)
    assert projects == {(PROXIMA, "499")}
    assert all(builder_names.is_withdrawn_row(r, projects) for r in rows)
    # Found only by reading the superseded captures: the live rows name nobody.
    assert builder_names.withdrawn_projects(live) == frozenset()


def test_a_blank_builder_lot_in_a_project_nobody_withdrew_is_kept():
    projects = builder_names.withdrawn_projects(_kensington())
    for rec in (_rec("", "172"),                      # another Proxima project
                _rec("", "499", channel="E-Agent"),   # the same number on another channel
                _rec("", None)):                      # no project at all
        assert not builder_names.is_withdrawn_row(rec, projects), rec


def test_a_lot_naming_another_builder_in_a_withdrawn_project_is_kept():
    """The project only fills in a BLANK builder; a row that names someone else is that
    builder's lot, whatever project it shares."""
    other = _rec("Hermitage Homes", "499")
    projects = builder_names.withdrawn_projects(_kensington() + [other])
    assert (PROXIMA, "499") in projects, "premise: 499 is a withdrawn project"
    assert not builder_names.is_withdrawn_row(other, projects)


def test_a_blank_project_id_never_withdraws_anything():
    """18 of Bathla's Proxima rows and all 28 of its portal rows had no project id on 12 Oct.
    Keyed on (channel, blank) they would withdraw every blank-builder row on the channel
    that names no project either."""
    named = [_rec("Bathla", None, channel=PORTAL), _rec("Bathla Development", ""),
             _rec("Bathla Group", "   ")]
    blank = [_rec("", None, channel=PORTAL), _rec("", ""), _rec("", "   "), _rec("", None)]
    projects = builder_names.withdrawn_projects(named + blank)
    assert projects == frozenset()
    assert all(builder_names.is_withdrawn_row(r, projects) for r in named)   # by name, still
    assert not any(builder_names.is_withdrawn_row(r, projects) for r in blank)
    # Nor can a published pair with a blank id stand in for one (see load_snapshot).
    assert builder_names.project_key({"source_channel": PROXIMA, "source_project_id": " "}) \
        is None


# ------------------------------------- the search: Clare's hook, Agent 2, the Research tab

KENSINGTON_BLANK = ("v2:fresh-00002", "v2:fresh-00003", "v2:fresh-00004", "v2:fresh-00005")
KEPT_IN_PROJECTS = ("v2:fresh-00006", "v2:fresh-00007")   # Hermitage in 499; blank in 172


def _kensington_stock():
    """40 synthetic lots; eight are on Proxima. Lots 0-1 name Bathla in project 499 and are
    superseded; lots 2-5 are 499's blank-builder lots; lot 6 is Hermitage's in 499; lot 7
    is a blank-builder lot in project 172, which nobody withdrew."""
    rows = S.stock(fresh=40, estates=4)
    for i, r in enumerate(rows[:8]):
        r.update(source_channel=PROXIMA, source_project_id="172" if i == 7 else "499",
                 builder_name="Bathla Development" if i < 2 else
                 "Hermitage Homes" if i == 6 else "")
        if i < 2:
            r.update(superseded_by=rows[i + 2]["content_hash"], date_checked="09/09/2026")
    return rows


def _hashes(resp):
    return {str(c.get("content_hash") or "") for c in _cards(resp)}


@pytest.fixture
def kensington_search(tmp_path, monkeypatch):
    return _search_over(_kensington_stock(), tmp_path, monkeypatch)


def _search_over(rows, tmp_path, monkeypatch):
    """The research module, searching a snapshot of `rows`."""
    path = S.write(tmp_path, rows)
    monkeypatch.setenv("SPB_SNAPSHOT_JSON", str(path))
    monkeypatch.delenv("SPB_SNAPSHOT_DB", raising=False)
    monkeypatch.delenv("SPB_MAX_CANDIDATES", raising=False)
    monkeypatch.delenv("SPB_SHORTLIST_MAX", raising=False)
    monkeypatch.setattr(C, "datetime", S.Sep16)
    import research as module
    monkeypatch.setattr(module, "EXPLAIN_BUDGET_MS", 120000)
    monkeypatch.setattr(module, "REQUEST_DEADLINE_MS", 600000)
    return module


def test_no_caller_of_the_search_is_offered_a_blank_lot_of_a_withdrawn_project(
        kensington_search, monkeypatch):
    def by_name_only(row, _projects=frozenset(), _captures=frozenset()):
        return is_withdrawn_builder(row.get("builder_name"))

    # raising=False: on a tree with no project rule this patches nothing, and the premise
    # is simply what that tree does.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(C, "is_withdrawn_row", by_name_only, raising=False)
        before = S.run(kensington_search)
    assert set(KENSINGTON_BLANK) <= _hashes(before), "premise: the name rule offers them"
    unpaged = S.run(kensington_search)                                       # Clare's hook
    paged = S.run(kensington_search, explain=True, offset=0, limit=100)     # Research tab
    for name, resp in (("unpaged (Clare's hook)", unpaged), ("paged (Research tab)", paged)):
        assert not _hashes(resp) & set(KENSINGTON_BLANK), name
        assert set(KEPT_IN_PROJECTS) <= _hashes(resp), name
        # 40 lots: 2 superseded captures, 4 blank lots of the withdrawn project.
        assert resp["shortlist_count"] == before["shortlist_count"] - 4 == 34, name


def _write_columnar(path, rows, **top):
    """stock.json in build_web's columnar shape, with any top-level keys given."""
    keys = list(rows[0])
    path.write_text(json.dumps(dict({"keys": keys, "rows": [[r.get(k) for k in keys]
                                                           for r in rows]}, **top)),
                    encoding="utf-8")
    return path


def test_load_snapshot_applies_the_projects_build_web_published(tmp_path, monkeypatch):
    """build_web now leaves the rows naming Bathla out of stock.json, so nothing left in
    the rows says whose project 499 is: the projects published beside them have to."""
    published = [r for r in _kensington_stock() if not is_withdrawn_builder(r["builder_name"])]
    path = tmp_path / "stock.json"
    monkeypatch.setenv("SPB_SNAPSHOT_JSON", str(path))
    monkeypatch.delenv("SPB_SNAPSHOT_DB", raising=False)
    _write_columnar(path, published)
    loaded = {r["content_hash"] for r in C.load_snapshot()[0]}
    assert set(KENSINGTON_BLANK) <= loaded, "premise: the rows alone do not withdraw them"
    _write_columnar(path, published, withdrawn_projects=[[PROXIMA, "499"], [PROXIMA, " "]])
    loaded = {r["content_hash"] for r in C.load_snapshot()[0]}
    assert not loaded & set(KENSINGTON_BLANK)
    assert set(KEPT_IN_PROJECTS) <= loaded
    assert len(loaded) == len(published) - len(KENSINGTON_BLANK)


def _snapshot_db(path, rows):
    """A snapshot database with every SNAPSHOT_FIELDS column, for the SQLite reader."""
    cols = list(C.SNAPSHOT_FIELDS)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE buildings (%s)" % ", ".join('"%s"' % c for c in cols))
        conn.executemany("INSERT INTO buildings VALUES (%s)" % ", ".join("?" * len(cols)),
                         [tuple(r.get(c) for c in cols) for r in rows])
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.mark.parametrize("reader", ["json", "sqlite"])
def test_either_reader_finds_the_projects_in_rows_that_still_name_the_builder(
        reader, tmp_path, monkeypatch):
    """A snapshot built before 24 Oct, or a database read directly, still holds the rows
    naming Bathla, so the reader finds project 499 there with nothing published."""
    rows = _kensington_stock()
    monkeypatch.delenv("SPB_SNAPSHOT_JSON", raising=False)
    monkeypatch.delenv("SPB_SNAPSHOT_DB", raising=False)
    if reader == "json":
        monkeypatch.setenv("SPB_SNAPSHOT_JSON", str(S.write(tmp_path, rows)))
    else:
        monkeypatch.setenv("SPB_SNAPSHOT_DB", str(_snapshot_db(tmp_path / "snap.db", rows)))
    loaded = {r["content_hash"] for r in C.load_snapshot()[0]}
    assert not loaded & set(KENSINGTON_BLANK)
    assert not loaded & {"v2:fresh-00000", "v2:fresh-00001"}           # named, by name
    assert set(KEPT_IN_PROJECTS) <= loaded and len(loaded) == len(rows) - 6


# ---------------------------------------------------------------- publish: build_web

def _harvest_db(path, rows):
    from database import ResearchDatabase
    ResearchDatabase(path)
    conn = sqlite3.connect(str(path))
    try:
        for r in rows:
            conn.execute("INSERT INTO buildings (%s) VALUES (%s)"
                         % (", ".join(r), ", ".join("?" * len(r))), list(r.values()))
        conn.commit()
    finally:
        conn.close()
    return path


def _harvested_kensington():
    """Project 499 in a harvest database, plus Hermitage's lot in it, a blank lot in 172,
    and one Bathla portal lot with no project id."""
    out = []
    for i, (h, builder, project, channel, sup) in enumerate((
            ("v2:named-53", "Bathla Development", "499", PROXIMA, "v2:blank-53"),
            ("v2:blank-53", "", "499", PROXIMA, None),
            ("v2:blank-64", "", "499", PROXIMA, None),
            ("v2:hermitage-499", "Hermitage Homes", "499", PROXIMA, None),
            ("v2:blank-172", "", "172", PROXIMA, None),
            ("v2:bathla-portal", "Bathla", None, PORTAL, None))):
        out.append({"content_hash": h, "dedup_key": h, "builder_name": builder,
                    "source_channel": channel, "source_project_id": project,
                    "superseded_by": sup, "lot_number": str(50 + i),
                    "lot_address": "Lot %d, 20 Wonson Parade, Riverstone NSW 2765" % (50 + i),
                    "suburb": "Riverstone", "state": "NSW", "availability_status": "For Sale",
                    "price": 999990.0, "bedrooms": 3,
                    "date_checked": datetime.now().strftime("%d/%m/%Y")})
    return out


def test_the_build_leaves_the_project_out_and_publishes_it(tmp_path, monkeypatch):
    import build_web
    import config
    monkeypatch.setattr(config, "DATABASE_PATH",
                        _harvest_db(tmp_path / "harvest.db", _harvested_kensington()))
    monkeypatch.setattr(build_web, "_login_status", lambda: {})
    out = tmp_path / "site"
    build_web.build(out, with_assets=False)
    stock = json.loads((out / "stock.json").read_text(encoding="utf-8"))
    hashes = {r[stock["keys"].index("content_hash")] for r in stock["rows"]}
    assert hashes == {"v2:hermitage-499", "v2:blank-172"}, hashes
    assert stock["withdrawn_projects"] == [[PROXIMA, "499"]]
    # And the search, reading what was published, offers exactly those two.
    monkeypatch.setenv("SPB_SNAPSHOT_JSON", str(out / "stock.json"))
    monkeypatch.delenv("SPB_SNAPSHOT_DB", raising=False)
    assert {r["content_hash"] for r in C.load_snapshot()[0]} == hashes
    # The Settings tab counts the same two of Proxima's four live lots.
    channels = json.loads((out / "channels.json").read_text(encoding="utf-8"))
    proxima = next(c for c in channels["channels"] if c["channel"] == PROXIMA)
    assert proxima["live"] == 2


# ---------------------------------------------------------- the Settings tab's counts

def _project_db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE buildings (source_channel TEXT, builder_name TEXT, "
                 "source_project_id TEXT, superseded_by TEXT, date_checked TEXT, "
                 "checked_at TEXT)")
    conn.executemany("INSERT INTO buildings VALUES (?,?,?,?,?,?)", rows)
    return conn


def _channel_rows():
    today = datetime.now().strftime("%d/%m/%Y")
    return ([(PROXIMA, "Bathla Development", "499", "v2:x", "09/09/2026", None)] * 3
            # 499's blank lots stopped being refreshed with the rest of Proxima
            + [(PROXIMA, "", "499", None, "01/10/2026", None)] * 20
            + [(PROXIMA, "Hermitage Homes", "499", None, today, None)] * 10
            + [(PROXIMA, "", "172", None, today, None)] * 40
            # Bathla's portal lots, unread since its portal was switched off
            + [(PORTAL, "Bathla", None, None, "01/09/2026", None)] * 14
            + [(PORTAL, "Hermitage Homes", None, None, today, None)] * 30)


def _held(payload, name):
    row = next(c for c in payload["channels"] if c["channel"] == name)
    return row["live"], row["refreshed_pct"]


def test_the_settings_counts_leave_a_withdrawn_builders_lots_out(tmp_path, monkeypatch):
    """Held and Refreshed describe the stock the app can offer: on 23 Oct "Direct Builder
    Portal (live)" held 453 with 14 of them Bathla's, unread, dragging Refreshed down."""
    import build_web
    monkeypatch.setattr(build_web, "_login_status", lambda: {})
    monkeypatch.setattr(builder_names, "is_withdrawn_builder", _never)
    before = build_web._export_channel_health(tmp_path, _project_db(_channel_rows()))
    assert _held(before, PROXIMA) == (70, 71), "premise: every live lot is counted"
    assert _held(before, PORTAL) == (44, 68), "premise"
    monkeypatch.undo()
    monkeypatch.setattr(build_web, "_login_status", lambda: {})
    payload = build_web._export_channel_health(tmp_path, _project_db(_channel_rows()))
    assert _held(payload, PROXIMA) == (50, 100)
    assert _held(payload, PORTAL) == (30, 100)
    assert [p["channel"] for p in payload["portals"]] == ["Hermitage Homes"]


# ------------------------------------------------- parity and the replay's obvious rows

def _obvious(h, builder, project, channel=PROXIMA, superseded=None):
    return {"content_hash": h, "builder_name": builder, "source_channel": channel,
            "source_project_id": project, "superseded_by": superseded,
            "date_checked": "23/10/2026" if not superseded else "09/09/2026",
            "availability_status": "For Sale", "price": 999990.0, "state": "NSW",
            "bedrooms": 3, "lot_number": h[-2:]}


def _obvious_catalogue():
    rows = [_obvious("v2:named-53", "Bathla Development", "499", superseded="v2:blank-53"),
            _obvious("v2:blank-53", "", "499"), _obvious("v2:blank-64", "", "499"),
            _obvious("v2:hermitage-99", "Hermitage Homes", "499"),
            _obvious("v2:blank-72", "", "172"),
            _obvious("v2:portal-11", "", None, channel=PORTAL),
            _obvious("v2:bathla-12", "Bathla", None, channel=PORTAL)]
    return SimpleNamespace(db={r["content_hash"]: r for r in rows}, row=lambda h: None)


def _cells(rec):
    return {"price_kind": "package", "price_in_text": True}


def _not_overseas(rec, snap):
    return False


def test_parity_and_the_replay_check_skip_the_same_rows():
    """Both read the DATABASE, superseded captures included, and take the rule from the
    bundle the way the search does. Without the project rule, parity would report 499's
    blank lots as listed by Proxima and hidden by the app, every night."""
    import parity_check as P
    import search_regressions as SR
    catalogue = _obvious_catalogue()
    today = date(2026, 10, 23)

    def parity(rule):
        return {lot["hash"] for lot in P.obvious_lots(catalogue, today, _cells, _not_overseas,
                                                      withdrawn=rule)}

    def replay(rule):
        return {SR._hash(r) for r in SR.obvious_candidates(catalogue, "NSW", 0, None, today,
                                                           _cells, withdrawn=rule)}

    # The bundle that shipped on 23 Oct has the name rule only.
    older = SR.withdrawn_rule(SimpleNamespace(is_withdrawn_builder=is_withdrawn_builder),
                              catalogue)
    assert {"v2:blank-53", "v2:blank-64"} <= parity(older), "premise"
    assert {"v2:blank-53", "v2:blank-64"} <= replay(older), "premise"
    rule = SR.withdrawn_rule(C, catalogue)
    kept = {"v2:hermitage-99", "v2:blank-72", "v2:portal-11"}
    assert parity(rule) == replay(rule) == kept


def _parity_lots():
    """A clean catalogue (tests/_parity_fixture) plus project 499 in NSW: three captures
    naming its owner, superseded, and the three blank-builder lots that replaced them,
    read tonight."""
    from tests import _parity_fixture as F

    def kensington(owner, blank_project="499"):
        # blank_project="724": the blank captures filed under another project, as Bathla's
        # Box Hill lots were, so only superseded_by joins them to the rows naming the owner.
        named = [F.lot(500 + n, state="NSW", estate="Kensington Lot1", channel=PROXIMA,
                       builder_name=owner, source_project_id="499", day="09/09/2026",
                       superseded_by="v2:%064x" % (0xA11CE000 + 510 + n)) for n in range(3)]
        blank = [F.lot(510 + n, state="NSW", estate="Kensington Lot1", channel=PROXIMA,
                       builder_name="", source_project_id=blank_project) for n in range(3)]
        return named + blank
    return F, F.clean_catalogue(), kensington


@pytest.fixture(scope="module")
def parity_bundle(tmp_path_factory):
    """The research bundle build_web writes, built from this tree."""
    site = tmp_path_factory.mktemp("withdrawn_bundle")
    done = subprocess.run(
        [sys.executable, "-X", "utf8", "-c",
         "import sys; from pathlib import Path; import build_web; "
         "build_web._bundle_research_function(Path(sys.argv[1]), Path(sys.argv[2]))",
         str(APP), str(site)],
        cwd=str(APP), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    return site / "api"


def _parity_night(bundle, base, db_rows, snapshot_rows, published=(), captures=()):
    """parity_check.py over a database and the stock.json build_web would publish from it:
    the rows the publish keeps, and the withdrawn projects and captures beside them."""
    F, _clean, _k = _parity_lots()
    snapshot, db = F.write(base, db_rows)
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    keep = {r["content_hash"] for r in snapshot_rows}
    at = data["keys"].index("content_hash")
    data["rows"] = [row for row in data["rows"] if row[at] in keep]
    data["withdrawn_projects"] = [list(k) for k in published]
    data["withdrawn_captures"] = sorted(captures)
    snapshot.write_text(json.dumps(data), encoding="utf-8")
    report = base / "parity_report.json"
    done = subprocess.run(
        [sys.executable, "-X", "utf8", str(APP / "parity_check.py"), "--snapshot", str(snapshot),
         "--db", str(db), "--bundle", str(bundle), "--today", "06/10/2026",
         "--history", str(base / "parity_history.json"), "--report", str(report),
         "--manifest", str(base / "harvest_manifest.json"),
         "--degraded-marker", str(base / "_run_degraded")],
        cwd=str(APP), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={k: v for k, v in dict(os.environ, PYTHONDONTWRITEBYTECODE="1").items()
             if k != "SPB_MAX_CANDIDATES"}, timeout=600)
    body = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None
    return done.returncode, done.stdout + done.stderr, body


def test_a_night_that_publishes_without_a_withdrawn_project_passes_parity(parity_bundle,
                                                                        tmp_path):
    """End to end, the way the nightly runs it: the real bundle, the database holding
    project 499's blank lots, and a snapshot the publish has left them out of. A check
    that still counted them would fail NSW every night from the first build."""
    _F, clean, kensington = _parity_lots()
    # PREMISE: a project nobody withdrew, missing from the snapshot the same way, fails
    # NSW -- the bar sees these lots, so the pass below is the rule's doing.
    code, out, body = _parity_night(parity_bundle, tmp_path / "control",
                                    clean + kensington("Hermitage Homes"), clean)
    assert code == 1, out
    nsw = next(r for r in body["rows"] if r["state"] == "NSW")
    assert nsw["lots"] == [15, 12], nsw                      # [obvious, shown]
    code, out, body = _parity_night(parity_bundle, tmp_path / "withdrawn",
                                    clean + kensington("Bathla Development"), clean,
                                    published=[(PROXIMA, "499")])
    assert code == 0, out
    nsw = next(r for r in body["rows"] if r["state"] == "NSW")
    assert nsw["lots"] == [12, 12], nsw


# ------------------------------- a withdrawn row's lot, captured blank under another project
#
# Bathla's project 593 "124 Old Pitt Town Road Box Hill Land Only" listed Lot 128, 65
# Futurity Street and Lot 119, 27 Mount Carmel Drive at $819,990 on 30 Aug. The captures
# that superseded those rows carry the same lots at the same price with no builder, filed
# under project 724 "Lindfield House - Lindfield", which no row naming Bathla is filed
# under, so every test above passed while they stayed live. On the 12 Oct snapshot the
# Research tab put them first and second under Needs confirming for Box Hill.

def _by_project_only(row, projects=frozenset(), _captures=frozenset()):
    """The rule before captures: by name, or a blank builder in a withdrawn project."""
    return builder_names.is_withdrawn_row(row, projects)


def _box_hill():
    """Project 593's two lots naming Bathla and the blank captures under 724 that replaced
    them, as the 26 Sep database holds them, plus a lot of 724 nothing joins to Bathla."""
    return [_rec("Bathla Development", "593", content_hash="v2:named-128",
                 superseded_by="v2:lindfield-128"),
            _rec("Bathla Development", "593", content_hash="v2:named-119",
                 superseded_by="v2:lindfield-119"),
            _rec("", "724", content_hash="v2:lindfield-128"),
            _rec("", "724", content_hash="v2:lindfield-119"),
            _rec("", "724", content_hash="v2:lindfield-130")]


def test_a_blank_capture_of_a_withdrawn_lot_under_another_project_is_withdrawn():
    rows = _box_hill()
    projects = builder_names.withdrawn_projects(rows)
    assert projects == {(PROXIMA, "593")}
    assert not any(_by_project_only(r, projects) for r in rows if not r["builder_name"]), (
        "premise: the project rule keeps every lot of 724")
    captures = builder_names.withdrawn_captures(rows, projects)
    assert captures == {"v2:lindfield-128", "v2:lindfield-119"}
    kept = {r["content_hash"] for r in rows
            if not builder_names.is_withdrawn_row(r, projects, captures)}
    # A capture withdraws its lot, not its project: 724's other lot stays.
    assert kept == {"v2:lindfield-130"}


def test_the_capture_rule_follows_superseded_by_both_ways_through_blank_rows_only():
    """481 rows of the 26 Sep database point at a row that is itself superseded, so the
    end of a chain is reached as well as its first link, and an older blank capture that a
    Bathla row replaced is the same lot too. A row naming another builder is that
    builder's lot: never withdrawn, and nothing is reached through it."""
    rows = [_rec("", "724", content_hash="v2:older-128", superseded_by="v2:named-128"),
            _rec("Bathla Development", "593", content_hash="v2:named-128",
                 superseded_by="v2:blank-128a"),
            _rec("", "724", content_hash="v2:blank-128a", superseded_by="v2:blank-128b"),
            _rec("", "724", content_hash="v2:blank-128b"),
            _rec("Bathla Development", "593", content_hash="v2:named-119",
                 superseded_by="v2:hermitage-119"),
            _rec("Hermitage Homes", "724", content_hash="v2:hermitage-119",
                 superseded_by="v2:after-hermitage"),
            _rec("", "724", content_hash="v2:after-hermitage"),
            _rec("", "724", content_hash="v2:before-hermitage",
                 superseded_by="v2:hermitage-119"),
            # A blank lot withdrawn by its project joins its own captures in turn.
            _rec("", "593", content_hash="v2:blank-593-121", superseded_by="v2:blank-121"),
            _rec("", "724", content_hash="v2:blank-121")]
    projects = builder_names.withdrawn_projects(rows)
    captures = builder_names.withdrawn_captures(rows, projects)
    assert captures == {"v2:older-128", "v2:blank-128a", "v2:blank-128b", "v2:blank-121"}
    kept = {r["content_hash"] for r in rows
            if not builder_names.is_withdrawn_row(r, projects, captures)}
    assert kept == {"v2:hermitage-119", "v2:after-hermitage", "v2:before-hermitage"}


def test_a_blank_hash_never_joins_anything():
    """The capture rule's version of the blank project id: a Bathla row that names no newer
    capture must not withdraw every blank-builder row that has no hash of its own."""
    named = _rec("Bathla Development", "593", content_hash="v2:named-120", superseded_by="  ")
    unhashed = [_rec("", "724", content_hash=h) for h in (None, "", "  ")]
    projects = builder_names.withdrawn_projects([named] + unhashed)
    captures = builder_names.withdrawn_captures([named] + unhashed, projects)
    assert captures == frozenset()
    assert not any(builder_names.is_withdrawn_row(r, projects, captures) for r in unhashed)
    # Nor can a published blank hash stand in for one (see load_snapshot).
    assert not any(builder_names.is_withdrawn_row(r, projects, frozenset({"", "  "}))
                   for r in unhashed)


BOX_HILL_CAPTURES = ("v2:fresh-00002", "v2:fresh-00003")
BOX_HILL_KEPT = ("v2:fresh-00004",)    # a lot of 724 that nothing joins to Bathla


def _box_hill_stock():
    """40 synthetic lots; five are on Proxima. Lots 0-1 name Bathla in project 593 and are
    superseded by lots 2-3, blank-builder captures filed under 724; lot 4 is another blank
    lot of 724."""
    rows = S.stock(fresh=40, estates=4)
    for i, r in enumerate(rows[:5]):
        r.update(source_channel=PROXIMA, source_project_id="593" if i < 2 else "724",
                 builder_name="Bathla Development" if i < 2 else "")
        if i < 2:
            r.update(superseded_by=rows[i + 2]["content_hash"], date_checked="30/08/2026")
    return rows


@pytest.fixture
def box_hill_search(tmp_path, monkeypatch):
    return _search_over(_box_hill_stock(), tmp_path, monkeypatch)


def test_no_caller_of_the_search_is_offered_a_blank_capture_of_a_withdrawn_lot(
        box_hill_search):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(C, "is_withdrawn_row", _by_project_only)
        before = S.run(box_hill_search)
    assert set(BOX_HILL_CAPTURES) <= _hashes(before), "premise: the project rule offers them"
    unpaged = S.run(box_hill_search)                                         # Clare's hook
    paged = S.run(box_hill_search, explain=True, offset=0, limit=100)       # Research tab
    for name, resp in (("unpaged (Clare's hook)", unpaged), ("paged (Research tab)", paged)):
        assert not _hashes(resp) & set(BOX_HILL_CAPTURES), name
        assert set(BOX_HILL_KEPT) <= _hashes(resp), name
        # 40 lots: 2 superseded captures naming Bathla, 2 blank captures of its lots.
        assert resp["shortlist_count"] == before["shortlist_count"] - 2 == 36, name


def test_load_snapshot_applies_the_captures_build_web_published(tmp_path, monkeypatch):
    """build_web leaves the rows naming Bathla out of stock.json, and with them the
    superseded_by that joined 724's two lots to Bathla: the captures published beside the
    rows have to say so."""
    published = [r for r in _box_hill_stock() if not is_withdrawn_builder(r["builder_name"])]
    path = tmp_path / "stock.json"
    monkeypatch.setenv("SPB_SNAPSHOT_JSON", str(path))
    monkeypatch.delenv("SPB_SNAPSHOT_DB", raising=False)
    _write_columnar(path, published, withdrawn_projects=[[PROXIMA, "593"]])
    loaded = {r["content_hash"] for r in C.load_snapshot()[0]}
    assert set(BOX_HILL_CAPTURES) <= loaded, (
        "premise: the rows and the projects alone do not withdraw them")
    _write_columnar(path, published, withdrawn_projects=[[PROXIMA, "593"]],
                    withdrawn_captures=list(BOX_HILL_CAPTURES))
    loaded = {r["content_hash"] for r in C.load_snapshot()[0]}
    assert not loaded & set(BOX_HILL_CAPTURES)
    assert set(BOX_HILL_KEPT) <= loaded
    assert len(loaded) == len(published) - len(BOX_HILL_CAPTURES)


@pytest.mark.parametrize("reader", ["json", "sqlite"])
def test_either_reader_finds_the_captures_in_rows_that_still_name_the_builder(
        reader, tmp_path, monkeypatch):
    """A snapshot built before 24 Oct, or a database read directly, still holds the rows
    naming Bathla and their superseded_by, so the reader joins the captures itself."""
    rows = _box_hill_stock()
    monkeypatch.delenv("SPB_SNAPSHOT_JSON", raising=False)
    monkeypatch.delenv("SPB_SNAPSHOT_DB", raising=False)
    if reader == "json":
        monkeypatch.setenv("SPB_SNAPSHOT_JSON", str(S.write(tmp_path, rows)))
    else:
        monkeypatch.setenv("SPB_SNAPSHOT_DB", str(_snapshot_db(tmp_path / "snap.db", rows)))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(C, "is_withdrawn_row", _by_project_only)
        before = {r["content_hash"] for r in C.load_snapshot()[0]}
    assert set(BOX_HILL_CAPTURES) <= before, "premise: the project rule keeps them"
    loaded = {r["content_hash"] for r in C.load_snapshot()[0]}
    assert not loaded & set(BOX_HILL_CAPTURES)
    assert not loaded & {"v2:fresh-00000", "v2:fresh-00001"}           # named, by name
    assert set(BOX_HILL_KEPT) <= loaded and len(loaded) == len(rows) - 4


def _harvested_box_hill():
    """Project 593's two lots naming Bathla in a harvest database, superseded by blank
    captures under 724, and a third lot of 724 that nothing joins to Bathla."""
    out = []
    for i, (h, builder, project, sup) in enumerate((
            ("v2:named-128", "Bathla Development", "593", "v2:lindfield-128"),
            ("v2:named-119", "Bathla Development", "593", "v2:lindfield-119"),
            ("v2:lindfield-128", "", "724", None),
            ("v2:lindfield-119", "", "724", None),
            ("v2:lindfield-130", "", "724", None))):
        out.append({"content_hash": h, "dedup_key": h, "builder_name": builder,
                    "source_channel": PROXIMA, "source_project_id": project,
                    "superseded_by": sup, "lot_number": str(110 + i),
                    "lot_address": "Lot %d, 65 Futurity Street, Box Hill NSW 2765" % (110 + i),
                    "suburb": "Box Hill", "state": "NSW", "availability_status": "For Sale",
                    "price": 819990.0, "bedrooms": 3,
                    "date_checked": datetime.now().strftime("%d/%m/%Y")})
    return out


def _published_hashes(out):
    stock = json.loads((out / "stock.json").read_text(encoding="utf-8"))
    return stock, {r[stock["keys"].index("content_hash")] for r in stock["rows"]}


def test_the_build_leaves_a_blank_capture_out_and_publishes_it(tmp_path, monkeypatch):
    import build_web
    import config
    monkeypatch.setattr(config, "DATABASE_PATH",
                        _harvest_db(tmp_path / "harvest.db", _harvested_box_hill()))
    monkeypatch.setattr(build_web, "_login_status", lambda: {})
    # raising=False: on a tree with no capture rule this adds a name nothing reads, and the
    # premise is simply what that tree builds.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(builder_names, "withdrawn_captures",
                   lambda rows, projects=frozenset(): frozenset(), raising=False)
        build_web.build(tmp_path / "before", with_assets=False)
    _stock, before = _published_hashes(tmp_path / "before")
    assert {"v2:lindfield-128", "v2:lindfield-119"} <= before, "premise: published"
    out = tmp_path / "site"
    build_web.build(out, with_assets=False)
    stock, hashes = _published_hashes(out)
    assert hashes == {"v2:lindfield-130"}, hashes
    assert stock["withdrawn_projects"] == [[PROXIMA, "593"]]
    assert stock["withdrawn_captures"] == ["v2:lindfield-119", "v2:lindfield-128"]
    # And the search, reading what was published, offers exactly that one.
    monkeypatch.setenv("SPB_SNAPSHOT_JSON", str(out / "stock.json"))
    monkeypatch.delenv("SPB_SNAPSHOT_DB", raising=False)
    assert {r["content_hash"] for r in C.load_snapshot()[0]} == hashes
    # The Settings tab counts the same one of Proxima's three live lots.
    channels = json.loads((out / "channels.json").read_text(encoding="utf-8"))
    proxima = next(c for c in channels["channels"] if c["channel"] == PROXIMA)
    assert proxima["live"] == 1


def _capture_db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE buildings (content_hash TEXT, source_channel TEXT, "
                 "builder_name TEXT, source_project_id TEXT, superseded_by TEXT, "
                 "date_checked TEXT, checked_at TEXT)")
    conn.executemany("INSERT INTO buildings VALUES (?,?,?,?,?,?,?)", rows)
    return conn


def _capture_channel_rows():
    today = datetime.now().strftime("%d/%m/%Y")
    return ([("v2:named-%d" % n, PROXIMA, "Bathla Development", "593", "v2:lindfield-%d" % n,
              "30/08/2026", None) for n in range(10)]
            # their blank captures under 724, read once on 30 Aug and not since
            + [("v2:lindfield-%d" % n, PROXIMA, "", "724", None, "30/08/2026", None)
               for n in range(10)]
            + [("v2:other-%d" % n, PROXIMA, "", "724", None, today, None) for n in range(30)])


def test_the_settings_counts_leave_a_withdrawn_lots_blank_captures_out(tmp_path, monkeypatch):
    import build_web
    monkeypatch.setattr(build_web, "_login_status", lambda: {})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(builder_names, "withdrawn_captures",
                   lambda rows, projects=frozenset(): frozenset(), raising=False)
        before = build_web._export_channel_health(tmp_path, _capture_db(_capture_channel_rows()))
    assert _held(before, PROXIMA) == (40, 75), "premise: the captures are counted"
    payload = build_web._export_channel_health(tmp_path, _capture_db(_capture_channel_rows()))
    assert _held(payload, PROXIMA) == (30, 100)


def _capture_catalogue():
    rows = [_obvious("v2:named-28", "Bathla Development", "593", superseded="v2:capture-28"),
            _obvious("v2:capture-28", "", "724"), _obvious("v2:other-30", "", "724"),
            _obvious("v2:hermitage-31", "Hermitage Homes", "724")]
    return SimpleNamespace(db={r["content_hash"]: r for r in rows}, row=lambda h: None)


def test_parity_and_the_replay_check_skip_the_same_captures():
    import parity_check as P
    import search_regressions as SR
    catalogue = _capture_catalogue()
    today = date(2026, 10, 23)

    def parity(rule):
        return {lot["hash"] for lot in P.obvious_lots(catalogue, today, _cells, _not_overseas,
                                                      withdrawn=rule)}

    def replay(rule):
        return {SR._hash(r) for r in SR.obvious_candidates(catalogue, "NSW", 0, None, today,
                                                           _cells, withdrawn=rule)}

    project_only = SR.withdrawn_rule(SimpleNamespace(
        is_withdrawn_row=builder_names.is_withdrawn_row,
        withdrawn_projects=builder_names.withdrawn_projects), catalogue)
    assert "v2:capture-28" in parity(project_only), "premise"
    assert "v2:capture-28" in replay(project_only), "premise"
    rule = SR.withdrawn_rule(C, catalogue)
    assert parity(rule) == replay(rule) == {"v2:other-30", "v2:hermitage-31"}


def test_a_night_that_publishes_without_a_withdrawn_capture_passes_parity(parity_bundle,
                                                                        tmp_path):
    """End to end, as for project 499 above, with the blank captures filed under 724 so
    only superseded_by joins them to the rows naming the owner."""
    _F, clean, kensington = _parity_lots()
    # PREMISE: the same shape for a builder nobody withdrew, missing from the snapshot the
    # same way, fails NSW.
    code, out, body = _parity_night(parity_bundle, tmp_path / "control",
                                    clean + kensington("Hermitage Homes", "724"), clean)
    assert code == 1, out
    nsw = next(r for r in body["rows"] if r["state"] == "NSW")
    assert nsw["lots"] == [15, 12], nsw                      # [obvious, shown]
    rows = kensington("Bathla Development", "724")
    code, out, body = _parity_night(parity_bundle, tmp_path / "withdrawn", clean + rows, clean,
                                    published=[(PROXIMA, "499")],
                                    captures={r["content_hash"] for r in rows
                                              if not r["builder_name"]})
    assert code == 0, out
    nsw = next(r for r in body["rows"] if r["state"] == "NSW")
    assert nsw["lots"] == [12, 12], nsw
