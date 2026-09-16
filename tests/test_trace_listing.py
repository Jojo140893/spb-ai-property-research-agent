"""trace_listing.py: explain any listing Colin names, read-only, never printing a link. (W16)

When Colin names the QLD listings he knows exist, each one must come back with where it
was lost -- not in the catalogue, a parse gap, or a search rule -- and the answer must be
the search's own. These run on tests/fixtures/colin_17sep_subset.json with today frozen at
16/09/2026, the day the live replays matched, plus the one row the plan names that the
subset does not hold (Torsion id 12739) and a small database holding the database-only
facts for the named rows as the 17 Sep database has them. Web addresses in that database
are stand-ins: the real ones are capability links and are never committed.

The last test repeats the four named cases on the 17 Sep snapshot and database themselves
when SPB_SNAPSHOT_JSON and SPB_DATABASE_PATH point at them, and skips saying why when not.
"""
import hashlib
import importlib.util
import io
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _candidates as C  # noqa: E402
import trace_listing as T  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "make_colin_subset", APP / "tests" / "fixtures" / "make_colin_subset.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

TODAY = gen.TODAY
TODAY_TEXT = gen.TODAY_TEXT
REGISTRY_CSV = APP / "api" / "_data" / "builders_public.csv"
QLD_750K = {"client_name": "trace_listing", "state": "QLD", "budget_max": 750000}

DAWN_1228 = ("v2:aec543504396f6d91db67fdeda1594f2a82cf326b69f9adeae1589dcb84afafa",
             "v2:c48b45992d8c5d69d81b986a3bb2baa44cdda6fc19ed4bcf550a4fd6b910e249")
DAWN_STALE = "stale -> needs confirming (checked 20/08/2026)"
DAWN_1229 = ("v2:30440e5989d86c38a65e24ee0d5412553c59583ac32f8a7423b6867b8b737416",
             "v2:9fb1f79e894b1e141248a4c2cdfa3dbd08ac540346632108bc9a0da838ac233b")
JUBILEE_4402 = "v2:0e9a289e6b10e3a3bdc69014d09fe38e902c18789eeb819f2bc760557f15b57f"

# Torsion's filter-row fragment, as the 17 Sep snapshot publishes it. Not in the subset:
# its document is not one of the seed documents.
TORSION_12739 = {
    "builder_name": "Torsion Homes",
    "lot_address": "HOUSE SIZE : 160 m2 HOUSE PRICE : $ 409,695",
    "state": "QLD", "price": 409695.0, "build_price": 409695.0, "house_sqm": 160.0,
    "source_channel": "Direct Builder Portal (live)", "date_checked": "14/09/2026",
    "content_hash": "v2:926f8165960bed50315e199953ccb83fb544f7f5e241b4a239fb6a48095a9b3f",
    "superseded_by": "spreadsheet filter row, not a listing",
    "suburb_source": "the source did not state one", "source_doc": "",
    "price_kind": "unknown", "text_price_max": 409695, "price_in_text": True,
}

# Stand-ins for capability links. Never the real ones.
FAKE_SHEET = "https://docs.google.com/spreadsheets/d/NOT-A-REAL-SHEET/edit#gid=0"
FAKE_SMARTSHEET = "https://app.smartsheet.com/b/publish?EQBCT=NOT-A-REAL-SHEET"
NOT_IN_SNAPSHOT = "v2:trace-listing-test-row-the-snapshot-does-not-hold"
DAWN_EMAIL = "email:Fwd: Shape Homes - Stock List 13.08.2026"

DB_COLUMNS = ("id", "content_hash", "checked_at", "first_seen", "last_seen", "source_url",
              "stocklist_file", "source_sheet", "source_row", "source_page", "source_text",
              "superseded_at", "builder_name", "estate_name", "lot_number", "lot_address",
              "street_address", "suburb", "state", "state_source", "price",
              "availability_status", "date_checked", "source_channel", "superseded_by")


def _db_row(**cols):
    return tuple(cols.get(c) for c in DB_COLUMNS)


DB_ROWS = [
    _db_row(id=11878, content_hash=DAWN_1228[0], checked_at="2026-08-20T09:46:43",
            first_seen="17/08/2026", last_seen="20/08/2026", source_url=DAWN_EMAIL,
            source_page=2),
    _db_row(id=14940, content_hash=DAWN_1228[1], checked_at="2026-08-20T09:46:43",
            first_seen="19/08/2026", last_seen="20/08/2026", source_url=DAWN_EMAIL,
            source_page=2),
    _db_row(id=11879, content_hash=DAWN_1229[0], checked_at="2026-08-20T09:46:43",
            first_seen="17/08/2026", source_url=DAWN_EMAIL, source_page=2),
    _db_row(id=11880, content_hash="v2:325f845742d7cb3c7806e9d7ae3e65dd04685c22bbeec6b3d8"
            "5759db8b44a645", checked_at="2026-08-20T09:46:43", first_seen="17/08/2026",
            source_url=DAWN_EMAIL, source_page=2),
    _db_row(id=14941, content_hash=DAWN_1229[1], checked_at="2026-08-20T09:46:43",
            first_seen="19/08/2026", source_url=DAWN_EMAIL, source_page=2),
    # The same e-mail's VIC page: "Lot 4402, Jubilee, Wyndham Vale", $716,900, no lot column.
    _db_row(id=11871, content_hash=JUBILEE_4402, checked_at="2026-08-20T09:46:43",
            first_seen="17/08/2026", source_url=DAWN_EMAIL, source_page=1),
    _db_row(id=15899, content_hash="v2:3b141dd88332708082ae739c3f81e273bac545ecbff7fd9a83"
            "7dc1e1a1bfdb46", checked_at="2026-09-01T11:56:53", first_seen="22/08/2026",
            last_seen="01/09/2026", source_url=FAKE_SHEET, stocklist_file=FAKE_SHEET,
            source_text="ENQUIRE NOW 5 Wacol Maplewood Estate Registered $515.00 $595,000"),
    _db_row(id=14905, content_hash="v2:1c5ca48e5472180f0e458762a153aab5d60362a04578863c4f"
            "fcd044f16be5f9", checked_at="2026-09-15T11:59:06", first_seen="19/08/2026",
            last_seen="15/09/2026", source_page=4,
            source_url="email:[Hattan weekly update] High-yield sub 600k packages from "
                       "$501,350",
            source_text="Available Wollert Titled Townhouse 618 112 4.5 14.07 2 2 1 "
                        "$143,389 $409,895 $553,284"),
    _db_row(id=12739, content_hash=TORSION_12739["content_hash"],
            checked_at="2026-09-14T11:20:23", first_seen="19/08/2026",
            last_seen="14/09/2026", source_url="", superseded_at="2026-09-16T12:17:11",
            source_text="HOUSE SIZE : 160 m2 HOUSE PRICE : $ 409,695"),
    _db_row(id=18491, content_hash="v2:43802e2db5012419a3dd96848d151add4c2dd3794c6c04bb5b"
            "ee85db9ef08041", checked_at="2026-09-15T11:40:07", first_seen="03/09/2026",
            source_url=FAKE_SMARTSHEET, stocklist_file=FAKE_SMARTSHEET),
    _db_row(id=15953, content_hash="v2:d9553ad6a938c1b9f9eac5c1e81b935dd01e5408c44794afc0"
            "cbdd214ef4e498", checked_at="2026-09-16T11:41:43", first_seen="22/08/2026",
            source_url=FAKE_SMARTSHEET, source_row=21),
    # A row the database holds and the snapshot does not.
    _db_row(id=99001, content_hash=NOT_IN_SNAPSHOT, builder_name="Example Homes",
            estate_name="Nowhere Rise", lot_number="7", lot_address="Lot 7 Nowhere Rise",
            suburb="Springfield", state="QLD", state_source="listing locality",
            price=700000.0, availability_status="Available", date_checked="16/09/2026",
            source_channel="E-Agent", source_url=FAKE_SHEET, first_seen="16/09/2026"),
]


def _write_db(path):
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE buildings (%s)" % ", ".join(DB_COLUMNS))
        conn.executemany("INSERT INTO buildings VALUES (%s)"
                         % ", ".join("?" * len(DB_COLUMNS)), DB_ROWS)
        conn.commit()
    finally:
        conn.close()


def _write_snapshot(path, rows, keys):
    path.write_text(json.dumps({"keys": list(keys),
                                "rows": [[r.get(k) for k in keys] for r in rows]}),
                    encoding="utf-8")


def _fingerprint_file(path):
    return Path(path).stat().st_mtime_ns, hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    """(stock.json, database): the subset plus Torsion 12739, and the small database."""
    work = tmp_path_factory.mktemp("trace_listing")
    raw = json.loads(gen.FIXTURE.read_text(encoding="utf-8"))
    keys = list(raw["keys"])
    rows = [dict(zip(keys, r)) for r in raw["rows"]] + [TORSION_12739]
    stock = work / "stock.json"
    _write_snapshot(stock, rows, keys)
    db = work / "catalogue.db"
    _write_db(db)
    return stock, db


@pytest.fixture(scope="module")
def catalogue(files):
    return T.load_catalogue(*files)


@pytest.fixture(scope="module")
def agent_factory(tmp_path_factory):
    """The pipeline object with the committed public registry, no comparables, disarmed
    sources, and its audit database in a temp dir (never a *.db inside the repo)."""
    import _bootstrap
    import config
    from kommo_agent import KommoPropertyResearchAgent
    audit = tmp_path_factory.mktemp("trace_listing_audit") / "audit.db"
    saved = config.DATABASE_PATH
    config.DATABASE_PATH = audit
    try:
        agent = KommoPropertyResearchAgent(csv_filepath=str(REGISTRY_CSV))
    finally:
        if config.DATABASE_PATH == audit:
            config.DATABASE_PATH = saved
    _bootstrap._disarm_sources(agent)
    agent.benchmark_engine.comparables = []
    taught = set()

    def factory(rows):
        if id(rows) not in taught:
            agent.geo.know_estates([(r.get("estate_name"), r.get("suburb"), r.get("state"))
                                    for r in rows
                                    if r.get("estate_name") and not r.get("superseded_by")])
            taught.add(id(rows))
        return agent
    return factory


def _trace(catalogue, agent_factory, brief=QLD_750K, **query):
    return T.run_trace(catalogue, T.Query(**query), dict(brief), TODAY,
                       agent_factory=agent_factory)


def _by_id(report):
    return {item["id"]: item for item in report["matches"]}


def _dawn_verdicts(report):
    return sorted(item["verdict"] for item in report["matches"]
                  if item["content_hash"] in DAWN_1228)


def _cli(argv, agent_factory):
    out = io.StringIO()
    code = T.main(argv, out=out, agent_factory=agent_factory)
    return code, out.getvalue()


# ------------------------------------------------------------------ the four named cases

def test_dawn_lot_1228_is_stale_and_needs_confirming(catalogue, agent_factory):
    report = _trace(catalogue, agent_factory, builder="Shape Homes", estate="Dawn",
                    lot="1228", price=721900)
    assert not report["not_found"]
    # Both captures of the lot, and nothing else. Either twin may be the one kept
    # (critique #3): exactly one needs confirming, the other is merged into it.
    assert {item["content_hash"] for item in report["matches"]} == set(DAWN_1228)
    verdicts = _dawn_verdicts(report)
    assert verdicts.count(DAWN_STALE) == 1, verdicts
    merged = [v for v in verdicts if v != DAWN_STALE]
    assert merged[0].startswith("merged into id 1") and merged[0].endswith(DAWN_STALE)
    for item in report["matches"]:
        assert item["kind"] == "confirm"
        assert item["confirm_guard"] is None
    # The row names no builder; its e-mail does.
    assert all('email "Fwd: Shape Homes - Stock List 13.08.2026"'
               in T.document_label(item["row"]) for item in report["matches"])


def test_torsion_12739_is_a_filter_row_not_a_listing(catalogue, agent_factory):
    item = _by_id(_trace(catalogue, agent_factory, ids=[12739]))[12739]
    assert item["verdict"] == "superseded: spreadsheet filter row, not a listing"
    assert item["kind"] == "parse"


def test_wacol_15899_is_hidden_because_its_document_was_reread(catalogue, agent_factory):
    item = _by_id(_trace(catalogue, agent_factory, ids=[15899]))[15899]
    assert item["verdict"].startswith("hidden: document re-read without this row"), item
    assert item["kind"] == "gate"
    assert item["confirm_guard"] == "document_reread_without_row"
    # Its row records no state; the search borrows QLD, and the trace says so.
    assert "borrows QLD" in item["state_note"]


def test_14905_is_shortlisted_with_a_warning_that_its_state_rests_on_the_suburb_column(
        catalogue, agent_factory):
    item = _by_id(_trace(catalogue, agent_factory, ids=[14905]))[14905]
    assert item["verdict"] == "shortlisted"
    assert item["kind"] == "shown"
    assert any("rests on the suburb column" in w for w in item["warnings"]), item["warnings"]


def test_another_state_is_a_parse_gap_only_when_the_text_says_so(catalogue, agent_factory):
    """14905 is filed QLD on a suburb match while its line names Wollert: a VIC client is
    denied it by a parse error. Hattan's correct VIC copy of the lot, 19510, rests on a
    suburb match too, but nothing in it says QLD -- to a QLD client it is simply elsewhere."""
    vic = dict(QLD_750K, state="VIC")
    item = _by_id(_trace(catalogue, agent_factory, brief=vic, ids=[14905]))[14905]
    assert item["kind"] == "parse", item
    assert item["verdict"] == ("hidden: filed QLD, the brief asks for VIC (its text names "
                               "Wollert, a VIC locality)")
    report = _trace(catalogue, agent_factory, hashes=["v2:303d19e1"])
    twin = report["matches"][0]
    assert str(twin["row"].get("state_source")).startswith("suburb")
    assert (twin["kind"], twin["verdict"]) == (
        "brief", "hidden: filed VIC, the brief asks for QLD")


def test_the_elsewhere_warning_asks_what_enrich_asks():
    """W02's question, asked the way enrich_buildings asks it: over the row's source_text
    as well as its lot_address, with a place counted as stocked unless its own state rests
    on the suburb column alone ('suburb + e-agent page' still vouches)."""
    target = {"lot_address": "Lot 618, Riverhills QLD", "suburb": "Riverhills",
              "state": "QLD", "state_source": "suburb", "estate_name": "Riverhills",
              "db": {"source_text": "Available Wollert Titled Townhouse 618 $553,284"}}

    def warnings(stocking_source):
        wollert = {"suburb": "Wollert", "state": "VIC", "state_source": stocking_source,
                   "lot_address": "Lot 618, Riverhills", "estate_name": "Riverhills"}
        catalogue = T.Catalogue([wollert, target], [], [], "test", "test.db")
        return T.Tracer(catalogue, dict(QLD_750K), TODAY).warnings(target)

    found = warnings("suburb + e-agent page")
    assert found and found[0].endswith("; its text names Wollert, a VIC locality"), found
    assert "Wollert" not in " ".join(warnings("suburb"))


def test_the_dawn_verdict_is_the_searchs_own_and_can_go_red(catalogue, agent_factory,
                                                           monkeypatch):
    """Stub the search's explanation to call every passing row verified and scored, and
    the Dawn assertion above no longer holds: the trace does not decide on its own."""
    real = C.explain_rows

    def everything_verified(brief, rows, **kwargs):
        out = []
        for row, verdict in real(brief, rows, **kwargs):
            if verdict["package"] is not None:
                package = dict(verdict["package"], verified=True)
                package.pop("confirm", None)
                verdict = dict(verdict, package=package)
            out.append((row, verdict))
        return out

    monkeypatch.setattr(T.C, "explain_rows", everything_verified)
    report = _trace(catalogue, agent_factory, builder="Shape Homes", estate="Dawn",
                    lot="1228", price=721900)
    assert DAWN_STALE not in _dawn_verdicts(report)


# ------------------------------------------------------------------ absent, and superseded

def test_a_listing_not_in_the_catalogue_prints_the_nearest_rows(catalogue, agent_factory):
    report = _trace(catalogue, agent_factory, builder="Shape Homes", estate="Dawn",
                    lot="1230", price=723900)
    assert report["not_found"] and not report["matches"]
    near = {item["row"].get("lot_number") for item in report["nearest"]}
    assert {"1228", "1229", "1231"} <= near, near
    for item in report["nearest"]:
        assert item["near"] and item["verdict"]
    text = "\n".join(T.render(report))
    assert "NOT IN THE CATALOGUE (intake gap)" in text
    assert "Nearest rows:" in text


def test_a_price_alone_is_not_a_listing():
    query = T.Query(builder="Shape Homes", estate="Dawn", lot="1228", price=721900)
    mernda = {"lot_address": "2-Part Townhome North 29 Havenwood Mernda $725,000",
              "price": 725000.0, "db": None}
    assert not T.is_match(query, T.match_statuses(query, mernda))
    howard = {"builder_name": "G Developments", "estate_name": "BRISBANE EAST",
              "lot_address": "Howard Available Lot 21 Howard Crossing", "price": 746632.0,
              "db": None}
    q21 = T.Query(builder="G Developments", lot="21", price=746632)
    assert T.is_match(q21, T.match_statuses(q21, howard))
    other_lot = dict(howard, lot_number="22")
    assert not T.is_match(q21, T.match_statuses(q21, other_lot))


def test_a_builder_or_estate_said_another_way_is_still_the_listing():
    hattan = {"builder_name": "hattan.com.au", "lot_number": "618", "price": 553284.0,
              "db": None}
    q618 = T.Query(builder="Hattan Homes", lot="618", price=553284)
    assert T.is_match(q618, T.match_statuses(q618, hattan))
    assert T.match_statuses(T.Query(builder="Shape Homes"), hattan)["builder"] == "no"
    highland = {"builder_name": "Torsion Homes", "estate_name": "Highland",
                "suburb": "Walloon", "lot_number": "224", "price": 799995.0, "db": None}
    q224 = T.Query(builder="Torsion", estate="Walloon Highlands", lot="224", price=799995)
    assert T.is_match(q224, T.match_statuses(q224, highland))
    # Filed under another name: offered as near only when estate, lot and price say so.
    developer = dict(highland, builder_name="Walloon Land Co")
    assert T.nearness(T.Query(builder="Torsion", price=790000), developer) == (0, [])
    score, reasons = T.nearness(q224, developer)
    assert score and reasons[0].startswith("another builder")


def test_a_lot_its_address_states_is_a_fact_without_a_lot_column(catalogue, agent_factory):
    """Shape Homes lot 1229 at $721,900 is the two Dawn captures -- not "Lot 4402, Jubilee,
    Wyndham Vale" at $716,900 from the same e-mail, whose lot column is blank and which
    agreed on builder (its e-mail names Shape Homes) and price (0.7% away)."""
    report = _trace(catalogue, agent_factory, builder="Shape Homes", lot="1229",
                    price=721900)
    assert {item["content_hash"] for item in report["matches"]} == set(DAWN_1229)
    jubilee = next(r for r in catalogue.rows if r["content_hash"] == JUBILEE_4402)
    assert not jubilee.get("lot_number") and "Lot 4402" in jubilee["lot_address"]
    assert T.match_statuses(T.Query(lot="1229"), jubilee)["lot"] == "no"
    assert T.match_statuses(T.Query(lot="4402"), jubilee)["lot"] == "yes"
    assert T.match_statuses(T.Query(lot="7"), {"lot_address": "Somewhere St"})["lot"] == (
        "unknown")
    # And near, by the lot its address states.
    _score, reasons = T.nearness(T.Query(builder="Shape Homes", lot="4401"), jubilee)
    assert "lot 4402 (+1)" in reasons


def test_json_lines_stay_json_when_a_value_holds_a_link():
    item = {"row": {"content_hash": "v2:x", "db": {"id": 7}}, "content_hash": "v2:x",
            "kind": "gate", "gate": "superseded", "confirm_guard": None,
            # A link that ENDS a value: scrubbing the dumped line would eat its closing
            # quote and the comma after it.
            "verdict": "superseded: withdrawn, see https://example.com/a?b=1",
            "warnings": ["its text names docs.example.com/x/y"]}
    lines = T.json_lines({"matches": [item], "nearest": [], "not_found": False,
                          "query": "id 7"})
    entry = json.loads(lines[0])
    assert entry["verdict"] == "superseded: withdrawn, see [link removed]"
    assert entry["warnings"] == ["its text names [link removed]"]
    assert entry["id"] == 7 and entry["gate"] == "superseded"


def test_a_file_name_that_is_its_links_key_is_not_printed():
    keyed = {"source_channel": "E-Agent", "source_doc": "d0c",
             "db": {"source_url": "https://static.wixstatic.example/ugd/"
                                  "abc123_0123456789abcdef0123456789abcdef.pdf"}}
    assert T.document_label(keyed) == "E-Agent file, [doc d0c]"
    titled = {"db": {"stocklist_file": "D:/lists/Taylors Run Price List- 24.06.26.pdf"}}
    assert T.document_label(titled) == 'file "Taylors Run Price List- 24.06.26.pdf"'
    assert T._file_name("x/Q3Zk9mWq2Lp7Rt5Yv8Bn4Hs6Jd1Fg0Kx.xlsx") == ""
    # A bare domain with a path is a link; a bare domain is a name.
    assert T._no_links("from docs.google.com/spreadsheets/d/abc/edit today") == (
        "from [link removed] today")
    assert T._no_links("builder hattan.com.au, 4.5/14.07, stock.json") == (
        "builder hattan.com.au, 4.5/14.07, stock.json")


def test_listings_end_with_one_verdict_line_each(files, agent_factory, tmp_path,
                                                 monkeypatch):
    """Colin's listings, traced under one search: each gets its rows and then ONE line
    saying where it was lost -- and the catalogue is explained once, not per listing."""
    stock, db = files
    listings = tmp_path / "colin.csv"
    listings.write_text(
        "builder,estate,lot,price,label\n"
        "Shape Homes,Dawn,1228,\"$721,900\",emailed list\n"
        "Shape Homes,Dawn,1230,723900,a builder's rep\n"
        "Silkwood Homes,Maplewood,,595k,Google Sheet\n"
        "Hattan Homes,Riverhills,618,553284,\n", encoding="utf-8")
    calls = []
    real = C.explain_rows

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(T.C, "explain_rows", counted)
    common = ["--listings", str(listings), "--state", "QLD", "--budget", "750000",
              "--today", TODAY_TEXT, "--snapshot", str(stock), "--db", str(db)]
    code, text = _cli(common, agent_factory)
    assert code == 0 and len(calls) == 1
    summary = text.split("One line per listing:\n", 1)[1].splitlines()
    assert len(summary) == 4, summary
    assert summary[0].startswith('  1. builder "Shape Homes", estate "Dawn", lot 1228, '
                                 'price $721,900 (emailed list) -> [needs confirming] id 11878')
    assert summary[0].endswith(DAWN_STALE + " (2 rows hold it)")
    assert "(a builder's rep) -> [intake gap] not in the catalogue; nearest is id 11" in (
        summary[1])
    assert "-> [hidden by a rule] id 15899 (v2:3b141dd8): hidden: document re-read " in (
        summary[2])
    # Superseded captures of lot 618 hold it too; the live, shown one speaks for it.
    assert re.search(r"-> \[shortlisted\] id 14905 \(v2:1c5ca48e\): shortlisted "
                     r"\(\d+ rows hold it\)$", summary[3]), summary[3]
    assert text.count("LISTING ") == 4

    _code, text = _cli(common + ["--json"], agent_factory)
    entries = [json.loads(line) for line in text.splitlines()]
    verdicts = {e["listing"]: e for e in entries if e.get("summary")}
    assert [verdicts[n]["kind"] for n in (1, 2, 3, 4)] == ["confirm", "intake", "gate",
                                                           "shown"]
    assert verdicts[1]["label"] == "emailed list"

    bad = tmp_path / "bad.csv"
    bad.write_text("builder,price\nShape Homes,lots\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _cli(["--listings", str(bad), "--snapshot", str(stock)], agent_factory)


def test_superseded_rows_are_traced_to_the_capture_that_replaced_them(catalogue,
                                                                     agent_factory):
    live = {r["content_hash"] for r in catalogue.rows if not r.get("superseded_by")}
    loser = next(r for r in catalogue.rows
                 if str(r.get("superseded_by") or "").startswith("v2:")
                 and r["superseded_by"] in live)
    report = _trace(catalogue, agent_factory, hashes=[loser["content_hash"]])
    item = report["matches"][0]
    assert item["verdict"].startswith("superseded by %s" % loser["superseded_by"][:11])
    assert " -- which is: " in item["verdict"]


def test_a_database_row_the_snapshot_lacks_is_named_not_lost(catalogue, agent_factory):
    item = _by_id(_trace(catalogue, agent_factory, ids=[99001]))[99001]
    assert item["kind"] == "snapshot"
    assert item["verdict"].startswith("in the database (checked 16/09/2026) but not in "
                                      "the snapshot searched")
    assert any("disagree" in note for note in catalogue.notes)


def test_a_snapshot_older_than_price_evidence_is_filled_from_the_database(files,
                                                                         agent_factory,
                                                                         tmp_path):
    """The deployed stock.json of 17 Sep predates the price-evidence cells. Without them
    the admission refuses every unstated row as 'no price evidence'; with the cells
    computed from the database the way build_web computes them, Wacol is hidden for the
    real reason."""
    stock, db = files
    raw = json.loads(stock.read_text(encoding="utf-8"))
    keys = [k for k in raw["keys"] if k not in C._COMPUTED_SNAPSHOT_FIELDS]
    rows = [dict(zip(raw["keys"], r)) for r in raw["rows"]]
    old = tmp_path / "old_stock.json"
    _write_snapshot(old, rows, keys)

    filled = T.load_catalogue(old, db)
    assert any("predates the price-evidence" in note for note in filled.notes)
    item = _by_id(_trace(filled, agent_factory, ids=[15899]))[15899]
    assert item["verdict"].startswith("hidden: document re-read without this row"), item

    bare = T.load_catalogue(old, None)
    wacol = next(r for r in bare.rows if r["content_hash"].startswith("v2:3b141dd8"))
    item = T.Tracer(bare, dict(QLD_750K), TODAY, agent_factory=agent_factory).trace(wacol)
    assert "no price evidence" in item["verdict"], item["verdict"]


# ------------------------------------------------------------------ read-only, no links

def test_the_database_is_only_ever_opened_read_only(files, agent_factory, monkeypatch):
    stock, db = files
    before = _fingerprint_file(db)
    calls = []
    real = sqlite3.connect

    def spy(target, *args, **kwargs):
        calls.append((str(target), kwargs.get("uri", False)))
        return real(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    code, _text = _cli(["--id", "15899", "--builder", "Shape Homes", "--lot", "1228",
                        "--price", "721900", "--state", "QLD", "--budget", "750000",
                        "--today", TODAY_TEXT, "--snapshot", str(stock), "--db", str(db)],
                       agent_factory)
    assert code == 0
    ours = [c for c in calls if Path(db).name in c[0]]
    assert ours, "the database was never read, so this check proves nothing"
    for target, uri in ours:
        assert uri and "mode=ro" in target, (target, uri)
    assert _fingerprint_file(db) == before


def test_nothing_printed_is_a_link(files, agent_factory):
    stock, db = files
    common = ["--state", "QLD", "--budget", "750000", "--today", TODAY_TEXT,
              "--snapshot", str(stock), "--db", str(db)]
    texts = []
    for extra in (["--id", "15899", "--id", "18491", "--id", "14905", "--id", "12739",
                   "--id", "99001"],
                  ["--builder", "Shape Homes", "--estate", "Dawn", "--lot", "1230"],
                  ["--id", "15899", "--id", "99001", "--json"]):
        code, text = _cli(extra + common, agent_factory)
        assert code == 0
        texts.append(text)
    everything = "\n".join(texts)
    assert "Google Sheet" in everything and "Smartsheet" in everything   # named, not linked
    for needle in ("http", "://", "www.", "docs.google", "smartsheet.com", "NOT-A-REAL"):
        assert needle not in everything, needle
    # The last line of defence, on its own.
    assert T._no_links("see https:/ and www.x.io/a?rlkey=1 or a@b.com") == (
        "see [link removed] and [link removed] or [link removed]")
    assert T.document_label({"db": {"source_url": FAKE_SHEET, "stocklist_file":
                                    "https://www.dropbox.com/scl/fi/x/Price%20List.pdf"
                                    "?rlkey=abc"}, "source_doc": "abc123"}) == (
        'Google Sheet, file "Price List.pdf", [doc abc123]')


def test_build_reads_a_copy_of_the_database(files, monkeypatch, tmp_path):
    """--build hands build_web a copy taken through a read-only connection; the database
    given is never the one build_web opens."""
    stock, db = files
    before = _fingerprint_file(db)
    seen = {}

    def fake_run(cmd, cwd=None, env=None, **kwargs):
        seen["env"] = env
        seen["out"] = Path(cmd[-1])
        copy = Path(env["SPB_DATABASE_PATH"])
        conn = sqlite3.connect(str(copy))
        try:
            seen["copied_rows"] = conn.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
        finally:
            conn.close()
        seen["out"].mkdir(parents=True, exist_ok=True)
        (seen["out"] / "stock.json").write_bytes(stock.read_bytes())

        class Done:
            returncode, stdout, stderr = 0, "", ""
        return Done()

    monkeypatch.setattr(T.subprocess, "run", fake_run)
    built, copy = T.build_snapshot(db, tmp_path / "work")
    assert Path(seen["env"]["SPB_DATABASE_PATH"]) == copy != Path(db)
    assert seen["copied_rows"] == len(DB_ROWS)
    assert built == seen["out"] / "stock.json" and built.is_file()
    assert _fingerprint_file(db) == before


def test_the_budget_is_read_as_the_research_tab_reads_it(files, agent_factory):
    stock, db = files
    base = ["--id", "15899", "--state", "QLD", "--today", TODAY_TEXT,
            "--snapshot", str(stock), "--db", str(db)]
    _code, text = _cli(base + ["--budget", "750k"], agent_factory)
    assert "budget up to $750,000" in text and "budget_from_text" in text
    _code, text = _cli(base + ["--budget", "750"], agent_factory)
    assert "budget up to $750,000" in text and "budget_in_thousands" in text
    _code, text = _cli(base + ["--budget", "750000"], agent_factory)
    assert "budget up to $750,000" in text and "adjusted" not in text
    _code, text = _cli(base, agent_factory)
    assert "no price ceiling" in text and "no_budget_max" in text


def test_a_scorer_that_cannot_run_is_said_not_hidden(catalogue):
    def broken(_rows):
        raise RuntimeError("no registry")

    report = _trace(catalogue, broken, ids=[14905])
    item = _by_id(report)[14905]
    assert item["verdict"].startswith("passes every gate and reaches scoring (scorer not "
                                      "run: RuntimeError: no registry)")
    assert any("scorer could not run" in note for note in report["notes"])


# ------------------------------------------------------------------ the 17 Sep catalogue

# Read at import, before any test runs: test_harvest_buildings pops SPB_DATABASE_PATH from
# the environment while the suite runs, and this check would then skip in the full suite.
_FULL_SNAPSHOT_ENV = os.environ.get("SPB_SNAPSHOT_JSON")
_FULL_DATABASE_ENV = os.environ.get("SPB_DATABASE_PATH")


def _full_catalogue_paths():
    snap, db = _FULL_SNAPSHOT_ENV, _FULL_DATABASE_ENV
    if not snap or not Path(snap).is_file():
        pytest.skip("local-only: SPB_SNAPSHOT_JSON does not point at a snapshot")
    if not db or not Path(db).is_file():
        pytest.skip("local-only: SPB_DATABASE_PATH does not point at a database")
    golden = json.loads(gen.GOLDEN.read_text(encoding="utf-8"))
    if gen.fingerprint(gen.load_rows(snap)) != golden["full_snapshot_fingerprint"]:
        pytest.skip("local-only: SPB_SNAPSHOT_JSON is not the 17 Sep snapshot")
    conn = sqlite3.connect("file:%s?mode=ro" % Path(db).as_posix(), uri=True)
    try:
        found = conn.execute("SELECT content_hash FROM buildings WHERE id = 12739").fetchone()
    finally:
        conn.close()
    if not found or found[0] != TORSION_12739["content_hash"]:
        pytest.skip("local-only: SPB_DATABASE_PATH is not the 17 Sep database")
    return snap, db


def test_the_named_cases_on_the_17_sep_catalogue(agent_factory):
    snap, db = _full_catalogue_paths()
    before = (Path(db).stat().st_mtime_ns, Path(db).stat().st_size)
    common = ["--state", "QLD", "--budget", "750000", "--today", TODAY_TEXT,
              "--snapshot", snap, "--db", db, "--json"]
    _code, text = _cli(["--id", "12739", "--id", "15899", "--id", "14905"] + common,
                       agent_factory)
    got = {entry["id"]: entry for entry in map(json.loads, text.splitlines())}
    assert got[12739]["verdict"] == "superseded: spreadsheet filter row, not a listing"
    assert got[15899]["verdict"].startswith("hidden: document re-read without this row")
    assert got[14905]["verdict"] == "shortlisted"
    assert any("rests on the suburb column" in w and "Wollert, a VIC locality" in w
               for w in got[14905]["warnings"]), got[14905]["warnings"]
    _code, text = _cli(["--builder", "Shape Homes", "--estate", "Dawn", "--lot", "1228",
                        "--price", "721900"] + common, agent_factory)
    dawn = [json.loads(line) for line in text.splitlines()]
    assert {entry["content_hash"] for entry in dawn} == set(DAWN_1228)
    assert [entry["verdict"] for entry in dawn].count(DAWN_STALE) == 1
    assert not re.search(r"https?:|www\.", text)
    _code, text = _cli(["--builder", "Shape Homes", "--lot", "1229", "--price", "721900"]
                       + common, agent_factory)
    assert {json.loads(line)["content_hash"] for line in text.splitlines()} == set(DAWN_1229)
    assert (Path(db).stat().st_mtime_ns, Path(db).stat().st_size) == before
