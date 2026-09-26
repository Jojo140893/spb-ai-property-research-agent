"""A lot whose own page is gone is not recommended (link_check.py -> build_web -> _candidates).

Coleen, 26 Sep 2026: the #1, #2 and #6 results of an "SA, any price" search were E-Agent
lots whose "Lot PDF -- opens this lot" link opened Kenekt's "Sorry but we were unable to
find the page you were looking for". Measured on the live search that day: 4 of its 102 lot
links answered 404, every one read from E-Agent on 24 Sep. Dead links had been fixed three
times before (brochures 29 Jul, folder links 7 Aug, Proxima project URLs 14 Aug), each one
kind at a time, and nothing ever asked whether a link still opens. These tests hold the
check that does.

NOTHING HERE LEAVES THIS MACHINE. The HTTP tests talk to a server on 127.0.0.1 started by
the test; everything else uses a fake prober.
"""
import http.server
import json
import socket
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import link_check as L  # noqa: E402


# ------------------------------------------------------------------ what counts as gone

@pytest.mark.parametrize("status, verdict", [
    (404, "gone"), (410, "gone"),
    (200, "ok"), (301, "ok"), (302, "ok"),
    # None of these says the page does not exist: a sign-in wall, a server having a bad
    # night, a request that never got an answer.
    (401, "unknown"), (403, "unknown"), (429, "unknown"), (500, "unknown"),
    (503, "unknown"), ("URLError: timed out", "unknown"), ("not reached", "unknown"),
])
def test_only_the_site_saying_the_page_does_not_exist_is_gone(status, verdict):
    assert L.verdict(status) == verdict


def test_a_whole_host_going_dark_is_held_back_not_read_as_sales():
    dark = {f"https://dark.example/p/{i}": 404 for i in range(11)}
    dark["https://dark.example/p/ok"] = 200
    fine = {f"https://fine.example/p/{i}": (404 if i < 3 else 200) for i in range(12)}
    small = {"https://small.example/a": 404, "https://small.example/b": 404}
    out, held = L.classify({**dark, **fine, **small})
    assert set(held) == {"dark.example"} and held["dark.example"] == {"pages": 12, "gone": 11}
    assert all(out[u]["verdict"] != "gone" for u in dark)
    assert out["https://dark.example/p/0"]["held_back"]
    assert sum(out[u]["verdict"] == "gone" for u in fine) == 3, "3 of 12 is lots selling"
    # Below HOST_FLOOR pages there is nothing to call a pattern: two dead pages are two.
    assert all(out[u]["verdict"] == "gone" for u in small)


# ------------------------------------------------------------------ asking every page

def test_every_page_that_gave_no_answer_is_asked_again_and_only_those():
    asked = []
    first = {"https://a/1": 200, "https://a/2": 404, "https://a/3": "URLError: reset",
             "https://a/4": "TimeoutError: timed out"}
    second = {"https://a/3": 404, "https://a/4": 200}

    def prober(url):
        asked.append(url)
        return (second if asked.count(url) > 1 else first)[url]

    paused = []
    out = L.run(list(first), prober=prober, workers=2, pause=paused.append)
    assert out == {"https://a/1": 200, "https://a/2": 404, "https://a/3": 404,
                   "https://a/4": 200}
    assert sorted(u for u in set(asked) if asked.count(u) == 2) == ["https://a/3",
                                                                    "https://a/4"]
    assert paused == [L.SECOND_PASS_PAUSE], "the second pass waits before asking again"


def test_pages_past_the_budget_are_not_reached_rather_than_guessed():
    ticks = iter(range(0, 10_000, 400))
    out = L.run(["https://a/1", "https://a/2", "https://a/3"], prober=lambda u: 404,
                workers=1, budget=500, clock=lambda: next(ticks), pause=lambda s: None)
    assert "not reached" in out.values()
    assert all(L.verdict(s) != "gone" for u, s in out.items() if s == "not reached")


class _Pages(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                                  # noqa: N802
        code = {"/ok": 200, "/gone": 404, "/removed": 410, "/boom": 500,
                "/login": 403}.get(self.path)
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/gone")
            self.end_headers()
            return
        self.send_response(code or 404)
        self.end_headers()
        self.wfile.write(b"x" * 10)

    def log_message(self, *_a):
        pass


@pytest.fixture
def local_site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_probe_reads_the_real_status_and_follows_a_redirect_to_it(local_site):
    assert L.probe(local_site + "/ok") == 200
    assert L.probe(local_site + "/gone") == 404
    assert L.probe(local_site + "/removed") == 410
    assert L.probe(local_site + "/boom") == 500
    assert L.probe(local_site + "/login") == 403
    assert L.probe(local_site + "/moved") == 404, "a redirect to a dead page is dead"


def test_a_page_that_cannot_be_reached_says_why_and_is_not_gone():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]            # closed again before the probe: refused
    status = L.probe(f"http://127.0.0.1:{port}/x", timeout=3)
    assert isinstance(status, str) and ":" in status, "the reason, not just 'URLError'"
    assert L.verdict(status) == "unknown"


# ------------------------------------------------------------------ which pages

def test_only_current_rows_http_lot_pages_are_checked_once_each(tmp_path):
    db = tmp_path / "spb.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE buildings (listing_url TEXT, superseded_by TEXT)")
    conn.executemany("INSERT INTO buildings VALUES (?, ?)", [
        ("https://ibportal.kenekt.app/property/BP-1/protected/x", None),
        ("https://ibportal.kenekt.app/property/BP-1/protected/x", ""),     # same page
        ("https://ibportal.kenekt.app/property/BP-2/protected/y", "123"),  # superseded
        ("email: Fw: Stock list", None),                                  # not a page
        ("https://mail.google.com/mail/u/0/#search/x", None),              # never a lot
        ("", None), (None, None),
        ("http://example.com/lot.pdf", None),
    ])
    conn.commit()
    conn.close()
    assert L.lot_urls(db) == ["http://example.com/lot.pdf",
                              "https://ibportal.kenekt.app/property/BP-1/protected/x"]


def test_the_run_writes_what_the_build_reads(tmp_path, monkeypatch):
    db = tmp_path / "spb.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE buildings (listing_url TEXT, superseded_by TEXT)")
    conn.executemany("INSERT INTO buildings VALUES (?, NULL)",
                     [("https://x.example/1",), ("https://x.example/2",)])
    conn.commit()
    conn.close()
    out = tmp_path / ".sessions" / "link_status.json"
    monkeypatch.setattr(L.config, "DATABASE_PATH", db)
    monkeypatch.setattr(L, "LINK_STATUS_PATH", out)
    monkeypatch.setattr(L, "run", lambda urls: {"https://x.example/1": 404,
                                                "https://x.example/2": 200})
    assert L.main([]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["urls"]["https://x.example/1"]["verdict"] == "gone"
    assert data["summary"]["pages"] == 2 and data["summary"]["gone"] == 1
    assert len(data["date"]) == 10 and data["date"][2] == "/"

    import build_web
    monkeypatch.setattr(build_web.config, "PROJECT_ROOT", tmp_path)
    assert build_web._lot_pages_gone() == ({"https://x.example/1"}, data["date"])


def test_with_no_check_file_the_build_marks_nothing(tmp_path, monkeypatch):
    import build_web
    monkeypatch.setattr(build_web.config, "PROJECT_ROOT", tmp_path)
    assert build_web._lot_pages_gone() == (set(), "")
    (tmp_path / ".sessions").mkdir()
    (tmp_path / ".sessions" / "link_status.json").write_text("{not json", encoding="utf-8")
    assert build_web._lot_pages_gone() == (set(), ""), "unreadable is not 'all gone'"


def test_the_nightly_checks_the_pages_before_it_builds():
    import inspect
    import run_daily
    src = inspect.getsource(run_daily)
    check = src.index('["link_check.py"]')
    build = src.index('["build_web.py"]')
    assert check < build, "build_web reads link_status.json, so the check must run first"
    line = src[src.rindex("(", 0, check):src.index(")", check) + 1]
    assert "True)" in line or line.rstrip(")").rstrip().endswith("True"), \
        "optional: a check that cannot run must not withhold tonight's stock"


# ------------------------------------------------------------------ the search

def _row(**over):
    row = dict(builder_name="Alete Homes", lot_address="Lot 1, House and Land Packages",
               suburb="Adelaide", state="SA", availability_status="Available",
               price=674200.0, bedrooms=3, bathrooms=2, car_spaces=1, land_sqm=248.0,
               house_sqm=123.0, source_channel="E-Agent", date_checked="24/09/2026",
               listing_url="https://ibportal.kenekt.app/property/BP-5571812/protected/x",
               content_hash="h1", lot_page_gone="")
    row.update(over)
    return row


def test_a_lot_whose_page_is_gone_is_not_recommended_and_says_why():
    import _candidates as C
    from datetime import datetime
    brief = {"state": "SA", "primary_suburbs": [], "budget_max": None}
    today = datetime(2026, 9, 26)
    live, gone = _row(), _row(content_hash="h2", lot_page_gone="26/09/2026")
    packages, counts = C.build_packages(brief, [live, gone], today=today)
    assert [p.get("content_hash") for p in packages] == ["h1"]
    assert counts["lot_page_gone"] == 1
    verdicts = {r["content_hash"]: v for r, v in
                C.explain_rows(brief, [live, gone], today=today)}
    assert verdicts["h2"]["gate"] == "lot_page_gone"
    assert verdicts["h2"]["detail"]["lot_page_gone"] == "26/09/2026"
    assert verdicts["h1"]["gate"] != "lot_page_gone"


def test_a_sold_lot_is_still_reported_as_sold_first():
    """The status the source gave outranks a page check: a lot marked Sold whose page is
    also gone is counted once, as not available."""
    import _candidates as C
    from datetime import datetime
    row = _row(availability_status="Sold", lot_page_gone="26/09/2026")
    verdict = C.explain_rows({"state": "SA", "primary_suburbs": []}, [row],
                             today=datetime(2026, 9, 26))[0][1]
    assert verdict["gate"] == "not_available"


def test_a_snapshot_database_from_before_the_check_reads_as_nothing_gone(tmp_path):
    import _candidates as C
    db = tmp_path / "snap.db"
    cols = [f for f in C.SNAPSHOT_FIELDS if f != "lot_page_gone"]
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE buildings (%s)" % ", ".join(f'"{c}"' for c in cols))
    conn.execute("INSERT INTO buildings (builder_name, state) VALUES ('B', 'SA')")
    conn.commit()
    conn.close()
    rows = C._read_sqlite(db)
    assert rows[0]["lot_page_gone"] == ""


def test_the_research_tab_names_the_reason():
    import re
    html = (APP / "index.html").read_text(encoding="utf-8")
    assert re.search(r"^\s+lot_page_gone: \[", html, re.M), \
        "GATE_LABEL must name every filtered count"
    import research
    assert "lot_page_gone" in research.FILTERED_COUNTS


def test_a_gone_lot_links_to_the_next_thing_that_exists_not_the_dead_page():
    import build_web
    from provenance import primary_source_link
    row = _row(stocklist_file="https://e-agent.example/price-list.xlsx")
    live = build_web._published_link(row, False, primary_source_link)
    gone = build_web._published_link(row, True, primary_source_link)
    assert live["url"] == row["listing_url"] and live["label"] == "Lot PDF"
    assert gone["url"] != row["listing_url"] and gone["opens"] != "lot"
    assert gone["url"] == "https://e-agent.example/price-list.xlsx"
    assert row["listing_url"], "the row itself is left as it was stored"


def test_the_field_is_published_and_never_selected():
    import build_web
    assert "lot_page_gone" in build_web.BUILDING_DERIVED_FIELDS
    assert "lot_page_gone" not in build_web.BUILDING_FIELDS, "it would reach the SELECT"
    build_web._check("buildings", build_web.BUILDING_FIELDS + build_web.BUILDING_DERIVED_FIELDS)


# ------------------------------------------------------------------ review, 26 Sep

def test_the_nightly_replay_accepts_a_gone_lot_as_rightly_hidden():
    """search_regressions picks 'obvious' rows from the database, which has no
    lot_page_gone column; without this a gone lot fails the replay and turns CI red."""
    import search_regressions as SR
    assert "lot_page_gone" in SR.ALLOWED_VERDICTS


def test_the_tracer_says_why_in_words():
    import trace_listing as T
    tracer = T.Tracer.__new__(T.Tracer)
    tracer.brief = {"state": "SA"}
    text, kind = tracer._verdict(_row(), {"gate": "lot_page_gone", "package": None,
                                          "detail": {"lot_page_gone": "26/09/2026"}}, 0)
    assert kind == "gate" and "26/09/2026" in text and "not found" in text
