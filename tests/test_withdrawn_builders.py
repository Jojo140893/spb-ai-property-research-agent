"""
Withdrawn builders: Bathla, 23 Oct 2026.

Colin, call of 23 Oct 2026: "Take off Butler. They went off bust. We're no longer using
them. So just, just delete them." Bathla Group entered voluntary administration on 25 Aug.

The trap these tests exist for: most of Bathla's stock came through PROXIMA (198 of its
226 rows in the 26 Sep database), not Bathla's own portal. Switching the portal off alone
would have left those 198 recommendable. So the rule is about the builder, and the search
test puts Bathla lots on Proxima as well as on the portal.

Each test first shows the same input WITHOUT the rule reaching the place it guards, so a
pass means the rule did the work and not that the input never got there.
"""

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


def test_without_the_rule_the_search_would_offer_bathla(research, monkeypatch):
    monkeypatch.setattr(C, "is_withdrawn_builder", _never)
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

    today = date(2026, 10, 23)
    every = P.obvious_lots(catalogue, today, cells, overseas)
    assert {lot["hash"] for lot in every} == {"h1", "h2"}, "premise"
    kept = P.obvious_lots(catalogue, today, cells, overseas, withdrawn=is_withdrawn_builder)
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
    """measure() asks the bundle's _candidates for is_withdrawn_builder and treats its
    absence as an older bundle. A rename there would quietly switch parity's half off."""
    assert C.is_withdrawn_builder("Bathla Group")


def test_the_search_function_bundle_ships_the_rule():
    """_candidates.py imports builder_names at module level. Left out of the bundle, every
    deployed search would 500 (the build's import probe refuses that before it ships)."""
    import build_web
    assert "builder_names.py" in build_web._FUNCTION_ROOT_MODULES
