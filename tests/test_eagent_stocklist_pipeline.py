"""EAgent Stocklist rows through the nightly pipeline (7 Oct 2026).

The reader itself is pinned in tests/test_eagent_stocklist.py. These pin what the rest of
the night does with its rows, because a new channel inherits every rule written for the
old ones and a few of them would have mistreated it:

  * THE SAME LOT FROM TWO CHANNELS SHOWS ONCE. E-Agent publishes most of this stock twice --
    as a builder's price list on its category pages and again in the Stocklist search.
    supersede_duplicates joins a Stocklist row to its price-list twin when the lot number,
    the estate, the price and the state agree, keeps the fresher (then the fuller) capture,
    and never joins two different lots or two lines of one document. This is the search
    lane's rule for the cross-channel merge (7 Oct 2026: "across channels only when lot
    number and estate match"), applied where it also fixes the counts.
  * A LOT THE STOCKLIST STOPS LISTING IS RETIRED like any re-listing channel's.
  * THE CARD'S LINK opens the Stocklist site with a term to search for -- never the JSON
    API endpoint the row was read from, which answers 401 to a browser.
  * THE SETTINGS TAB names the missing login even before the channel has any stock.
  * THE NIGHTLY SHEET CHECK does not open the Stocklist anonymously: it would only ever
    get the sign-in page, which says nothing about any lot.
"""

import json
from datetime import datetime, timedelta

from supersede_duplicates import find_superseded

STOCKLIST = "E-Agent Stocklist"
API = "https://eagent.mekooflow.com/api/packages"
TODAY = datetime.now().strftime("%d/%m/%Y")


def _days_ago(n):
    return (datetime.now() - timedelta(days=n)).strftime("%d/%m/%Y")


def _row(i, channel, lot="100", estate="Aurora", price=650_000.0, state="VIC",
         date=TODAY, suburb="Wollert", text=None, url=None, **kw):
    # The address label is unique per row unless a test sets one, so the three existing
    # passes (which need an identical label, or an identical line) cannot join these rows
    # and only the cross-channel twin rule is being tested.
    r = {"id": i, "source_channel": channel, "lot_number": lot, "estate_name": estate,
         "price": price, "state": state, "suburb": suburb, "date_checked": date,
         "last_seen": date, "lot_address": "%s: Lot %s, %s (#%d)" % (channel, lot, estate, i),
         "bedrooms": 4, "bathrooms": 2, "car_spaces": 2, "house_sqm": None,
         "land_sqm": None, "postcode": None, "builder_name": "Hudson Homes",
         "benchmark_median": None, "content_hash": "v2:h%d" % i,
         "source_text": text or "%s line %d" % (channel, i),
         "source_url": url or ("https://e-agent.example/doc-%d.pdf" % i)}
    r.update(kw)
    return r


# ------------------------------------------------------------------ cross-channel twins

def test_a_stocklist_row_and_its_price_list_twin_show_once():
    pdf = _row(1, "E-Agent", lot_address="AVAILABLE 100 Aurora 448 Ascot 25 $650,000")
    sl = _row(2, STOCKLIST, url=API, land_sqm=448.0, house_sqm=210.0, postcode="3750")
    pairs = find_superseded([pdf, sl])
    assert [(l["id"], w["id"]) for l, w in pairs] == [(1, 2)], "the fuller row survives"


def test_the_fresher_capture_survives_even_when_the_older_one_knows_more():
    pdf = _row(1, "E-Agent", date=_days_ago(4), land_sqm=448.0, house_sqm=210.0,
               postcode="3750")
    sl = _row(2, STOCKLIST, url=API)
    assert [(l["id"], w["id"]) for l, w in find_superseded([pdf, sl])] == [(1, 2)]


def test_a_night_one_channel_missed_does_not_swap_the_card():
    """Repair round 1, 7 Oct 2026 (data verifier, six simulated nights): with the price list
    skipped one night, its Stocklist twin was the fresher capture and won; the next night
    the fuller price-list row won back. 24 lots changed card and content_hash twice, and a
    lead pick saved under one hash stopped matching. Last night's survivor keeps the card
    unless the other capture is newer by MORE than one night."""
    pdf = _row(1, "E-Agent", date=_days_ago(1), land_sqm=448.0, house_sqm=210.0,
               postcode="3750", superseded_by=None)
    sl = _row(2, STOCKLIST, url=API, superseded_by="v2:h1")
    assert [(l["id"], w["id"]) for l, w in find_superseded([pdf, sl])] == [(2, 1)]
    # Two nights behind is a list that stopped being read: the fresher capture takes over.
    pdf = dict(pdf, date_checked=_days_ago(2), last_seen=_days_ago(2))
    assert [(l["id"], w["id"]) for l, w in find_superseded([pdf, sl])] == [(1, 2)]
    # And the other way round: a Stocklist survivor is kept the same way.
    sl_live = dict(sl, superseded_by=None, date_checked=_days_ago(1), last_seen=_days_ago(1))
    pdf_today = dict(pdf, superseded_by="v2:h2", date_checked=TODAY, last_seen=TODAY)
    assert [(l["id"], w["id"]) for l, w in find_superseded([pdf_today, sl_live])] == [(1, 2)]


def test_lot_and_estate_are_matched_as_written_not_byte_for_byte():
    pdf = _row(1, "E-Agent", lot="Lot 0100", estate="Aurora Estate")
    sl = _row(2, STOCKLIST, url=API, lot="100", estate="AURORA")
    assert len(find_superseded([pdf, sl])) == 1


def test_different_lots_are_never_joined():
    pdf = _row(1, "E-Agent", lot="101")
    sl = _row(2, STOCKLIST, url=API, lot="100")
    assert find_superseded([pdf, sl]) == []


def test_the_same_lot_number_in_another_estate_is_another_lot():
    pdf = _row(1, "E-Agent", estate="Riverdale")
    sl = _row(2, STOCKLIST, url=API)
    assert find_superseded([pdf, sl]) == []


def test_a_different_price_is_a_different_package():
    """Two designs on one lot are two packages at two prices (the VIC Regional list's
    Lot 414: Arklay 17 and Dunestone 22). Only the same price is the same package."""
    pdf = _row(1, "E-Agent", price=640_000.0)
    sl = _row(2, STOCKLIST, url=API)
    assert find_superseded([pdf, sl]) == []


def test_two_lines_of_one_document_are_never_joined_through_a_stocklist_row():
    """Two lines of one price list with the same lot, estate and price (two facades, say)
    are two lots or two packages. A Stocklist row matching both cannot say which one it
    is, so nothing is joined -- least of all the two lines to each other."""
    doc = "https://e-agent.example/hudson.pdf"
    a = _row(1, "E-Agent", url=doc, text="100 Aurora Ascot 25 Facade A $650,000")
    b = _row(2, "E-Agent", url=doc, text="100 Aurora Ascot 25 Facade B $650,000")
    sl = _row(3, STOCKLIST, url=API)
    assert find_superseded([a, b, sl]) == []


def test_two_stocklist_rows_are_never_joined_to_one_price_list_line():
    pdf = _row(1, "E-Agent")
    s1 = _row(2, STOCKLIST, url=API, text="Lot 100 | Aurora | Ascot 25")
    s2 = _row(3, STOCKLIST, url=API, text="Lot 100 | Aurora | Bayview 26")
    assert find_superseded([pdf, s1, s2]) == []


def test_a_contradiction_on_state_or_rooms_stops_the_join():
    assert find_superseded([_row(1, "E-Agent", state="QLD"),
                            _row(2, STOCKLIST, url=API)]) == []
    assert find_superseded([_row(1, "E-Agent", bedrooms=3),
                            _row(2, STOCKLIST, url=API)]) == []
    # A blank is a capture knowing less, not a contradiction.
    assert len(find_superseded([_row(1, "E-Agent", state="", bedrooms=None),
                                _row(2, STOCKLIST, url=API)])) == 1


def test_older_captures_of_the_twin_do_not_block_the_join():
    """The price-list line was read on three nights and stored as three captures, which
    the same-line pass already joins. They are one lot, so the Stocklist row still finds
    exactly one twin, and the whole group keeps one survivor."""
    line = "AVAILABLE 100 Aurora Wollert 448 Ascot 25 $650,000"
    old = [_row(i, "E-Agent", date=_days_ago(10 - i), text=line,
                url="https://e-agent.example/hudson.pdf") for i in (1, 2, 3)]
    sl = _row(4, STOCKLIST, url=API, land_sqm=448.0)
    pairs = find_superseded(old + [sl])
    assert {l["id"] for l, _ in pairs} == {1, 2, 3}
    assert {w["id"] for _, w in pairs} == {4}


def test_rows_without_a_stocklist_twin_keep_todays_rule_exactly():
    """The pass only ever joins a Stocklist row. Two price-list channels carrying the same
    lot number and estate at the same price are left to the three existing passes."""
    a = _row(1, "E-Agent")
    b = _row(2, "Proxima")
    assert find_superseded([a, b]) == []


# ------------------------------------------------------------------ withdrawal

def test_a_lot_the_stocklist_stopped_listing_is_retired():
    import repair_rows
    fresh = [dict(_row(i, STOCKLIST, url=API, lot=str(100 + i)), superseded_by=None,
                  stocklist_file=API, source_project_id=None) for i in range(1, 31)]
    gone = dict(_row(99, STOCKLIST, url=API, lot="999", date=_days_ago(20)),
                superseded_by=None, stocklist_file=API, source_project_id=None)
    out = repair_rows.find_withdrawn_from_source(fresh + [gone])
    assert [r["id"] for r, _days in out] == [99]


def test_the_nightly_repair_keeps_a_stated_postcode():
    """repair_rows clears every stored postcode the row's own text cannot reproduce. A
    Stocklist row's text is composed by the reader, so it must reproduce the postcode the
    source stated -- even with a four-digit design name in the line."""
    import repair_rows
    from sources import eagent_stocklist as es
    rec = {"id": 7, "lotNumber": "100", "estateName": "Aurora", "suburb": "Wollert",
           "state": "VIC", "postcode": "3750", "designName": "Hamilton 2400",
           "packagePrice": 650_000, "landSize": 448, "status": "Available"}
    row, _ = es.map_record(rec, "https://eagent.mekooflow.com", API)
    stored = {"id": 7, "postcode": row["postcode"], "lot_address": row["lot_address"],
              "suburb": row["suburb"], "source_text": row["source_text"],
              "state": "VIC", "state_source": "postcode"}
    assert stored["postcode"] == "3750"
    assert repair_rows.find_fabricated_postcodes([stored]) == []


# ------------------------------------------------------------------ the card's link

def test_the_card_links_to_the_stocklist_site_never_to_its_api():
    from provenance import primary_source_link, source_links
    row = {"source_channel": STOCKLIST, "lot_number": "100", "estate_name": "Aurora",
           "suburb": "Wollert", "lot_address": "Lot 100, Aurora, Wollert VIC 3750",
           "stocklist_file": API, "source_url": API, "date_checked": TODAY}
    link = primary_source_link(row)
    assert link["url"] == "https://eagent.mekooflow.com/"
    assert link["opens"] == "portal"
    assert link["search"] == "Lot 100, Aurora"
    assert not any("/api/" in l["url"] for l in source_links(row))
    # Wherever the API lives: the end-to-end run read it from 127.0.0.1.
    moved = dict(row, stocklist_file="http://127.0.0.1:8080/api/v2/lots",
                 source_url="http://127.0.0.1:8080/api/v2/lots")
    assert [l["url"] for l in source_links(moved)] == ["https://eagent.mekooflow.com/"]


def test_a_public_brochure_still_comes_first():
    from provenance import primary_source_link
    row = {"source_channel": STOCKLIST, "lot_number": "100", "estate_name": "Aurora",
           "brochure_url": "https://cdn.example.com/aurora-100.pdf",
           "listing_url": "https://packages.example.com/aurora/100",
           "stocklist_file": API, "source_url": API}
    assert primary_source_link(row)["url"] == "https://packages.example.com/aurora/100"


# ------------------------------------------------------------------ the Settings tab

def _db(rows):
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE buildings (source_channel TEXT, builder_name TEXT, "
                 "superseded_by TEXT, date_checked TEXT, checked_at TEXT)")
    conn.executemany("INSERT INTO buildings VALUES (?,?,?,?,?)", rows)
    return conn


def test_the_settings_tab_names_the_missing_login_before_any_stock_arrives(
        tmp_path, monkeypatch):
    import build_web
    from sources import eagent_stocklist as es
    stocklist_login = es.login_row(None, configured=False)
    logins = {build_web._norm_source("E-Agent"): {"label": "E-Agent", "state": "ok"},
              build_web._norm_source(stocklist_login["label"].split("(")[0]): stocklist_login}
    monkeypatch.setattr(build_web, "_login_status", lambda: logins)
    payload = build_web._export_channel_health(
        tmp_path, _db([("E-Agent", "", None, TODAY, None)] * 3))
    row = next(c for c in payload["channels"] if c["channel"] == STOCKLIST)
    assert row["live"] == 0
    assert row["login"]["state"] == "missing"
    assert row["login"]["blocked_by"] == ("Needs the EAgent Stocklist company login "
                                          "from your E-Agent BDM")
    assert STOCKLIST in payload["needs_login"]
    eagent = next(c for c in payload["channels"] if c["channel"] == "E-Agent")
    assert eagent["login"]["state"] == "ok"
    written = json.loads((tmp_path / "channels.json").read_text(encoding="utf-8"))
    assert any(c["channel"] == STOCKLIST for c in written["channels"])


def test_the_e_agent_row_never_borrows_the_stocklist_login(tmp_path, monkeypatch):
    """Integration, 7 Oct 2026 (UI drive of the merged tree). _login_for falls back to a
    normalised PREFIX match, and "eagent" is a prefix of "eagentstocklist": on a night with
    no E-Agent session row (portal_login --check reads the vendor sheet for it), the E-Agent
    line borrowed the Stocklist's row and told the owner E-Agent "Needs the EAgent Stocklist
    company login from your E-Agent BDM". A row that names its channel belongs to that
    channel only."""
    import build_web
    from sources import eagent_stocklist as es
    stocklist_login = es.login_row(None, configured=False)
    only = {build_web._norm_source(stocklist_login["label"].split("(")[0]): stocklist_login}
    monkeypatch.setattr(build_web, "_login_status", lambda: only)
    payload = build_web._export_channel_health(
        tmp_path, _db([("E-Agent", "", None, TODAY, None)] * 3))
    eagent = next(c for c in payload["channels"] if c["channel"] == "E-Agent")
    assert eagent["login"]["state"] == "unknown", eagent["login"]
    assert "Stocklist" not in json.dumps(eagent["login"])
    stock = next(c for c in payload["channels"] if c["channel"] == STOCKLIST)
    assert stock["login"]["state"] == "missing"
    # The prefix match still serves the rows it was written for ('Bathla Development' and
    # its 'Bathla' session).
    assert build_web._login_for("Bathla Development",
                                {"bathla": {"label": "Bathla", "state": "ok"}})["state"] == "ok"
    # With both rows, each line takes its own.
    both = dict(only, **{build_web._norm_source("E-Agent"): {"label": "E-Agent",
                                                              "state": "ok"}})
    assert build_web._login_for("E-Agent", both)["state"] == "ok"
    assert build_web._login_for("E-Agent Stocklist", both)["state"] == "missing"


def test_once_it_has_stock_the_channel_row_carries_its_own_login(tmp_path, monkeypatch):
    import build_web
    from sources import eagent_stocklist as es
    ok = es.login_row({"state": "ok", "signed_in": True,
                       "checked_at": datetime.now().isoformat(timespec="seconds")},
                      configured=True)
    logins = {build_web._norm_source(ok["label"].split("(")[0]): ok}
    monkeypatch.setattr(build_web, "_login_status", lambda: logins)
    payload = build_web._export_channel_health(
        tmp_path, _db([(STOCKLIST, "Hudson Homes", None, TODAY, None)] * 30))
    rows = [c for c in payload["channels"] if c["channel"] == STOCKLIST]
    assert len(rows) == 1 and rows[0]["live"] == 30
    assert rows[0]["login"]["state"] == "ok"


# ------------------------------------------------------------------ the sheet check

def test_the_sheet_check_never_opens_the_stocklist_anonymously():
    import verify_sheet_positions as vsp
    asked = []

    def fetch(url):
        asked.append(url)
        return None

    rows = [dict(_row(i, STOCKLIST, url=API), superseded_by=None) for i in range(1, 12)]
    rows += [dict(_row(50, "E-Agent", url="https://e-agent.example/a.pdf"),
                  superseded_by=None)]
    vsp.verify(rows, fetch=fetch, extract=lambda *a, **k: [])
    assert asked == ["https://e-agent.example/a.pdf"]
