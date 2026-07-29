"""
Tests for fetching stocklists that builders publish off E-Agent.

The bug these guard: the crawl only handled links on e-agent.com.au itself, so of 142
stock links across the category pages it read 24 — missing 21 builders and estates,
including Tomorrow Homes, which the client reaches by hand and named as missing.

All offline. The network methods themselves were established and independently
reproduced against the live links; what is pinned here is the routing and, more
importantly, the refusals — the cases where returning *something* would be worse than
returning nothing.
"""

import io
import re

from sources.e_agent import _project_from_label
from sources.remote_stocklist import (KIND_FILE, KIND_HTML, _fetch_direct,
                                      _fetch_dropbox, _fetch_google,
                                      _fetch_msoffice, _fetch_smartsheet,
                                      _fetch_torsion, _looks_like_a_file,
                                      _ss_to_csv, fetch_stocklist, handler_for,
                                      is_offsite)


class _Resp:
    def __init__(self, body=b"", status=200, ctype="text/html"):
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        self.ok = 200 <= status < 300

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")


class _Stub:
    """Records every URL asked for, so a test can assert what was NOT requested."""

    def __init__(self, routes):
        self.routes = routes
        self.asked = []

    def get(self, url, **kw):
        self.asked.append(url)
        for pattern, resp in self.routes.items():
            if pattern in url:
                return resp
        return _Resp(b"<html>not found</html>", 404)

    def post(self, url, **kw):
        self.asked.append(url)
        return _Resp(b"", 404)


def test_every_host_routes_to_its_own_handler():
    cases = {
        "https://www.e-agent.com.au/_files/ugd/069fe0_abc.xlsx": _fetch_direct,
        "https://docs.google.com/spreadsheets/d/1upUgU_j9GA9MN4jYimRBCQPIw7tbzkuad9/edit#gid=0": _fetch_google,
        "https://dbnhomes-my.sharepoint.com/:x:/g/personal/x/IQBLYR1m": _fetch_msoffice,
        "https://onedrive.live.com/:x:/g/personal/e79d716bcc3bcce5/IQBqRhl8": _fetch_msoffice,
        "https://www.dropbox.com/scl/fo/abc/def?rlkey=x&dl=0": _fetch_dropbox,
        "https://app.smartsheet.com/b/publish?EQBCT=cb7214cb": _fetch_smartsheet,
        "https://referrer.torsionhomes.au/Stocklist.aspx?DriveID=b!x&FolderID=y": _fetch_torsion,
    }
    for url, want in cases.items():
        assert handler_for(url) is want, f"{url} routed to {handler_for(url).__name__}"
    assert not is_offsite("https://www.e-agent.com.au/_files/ugd/x.xlsx")
    for url in cases:
        if "e-agent.com.au" not in url:
            assert is_offsite(url), url


def test_html_is_never_mistaken_for_a_file():
    """Every one of these hosts answers 200 with HTML when a share is restricted. Parsing
    a sign-in page would put its words in the client's stock sheet."""
    assert not _looks_like_a_file(b"<!DOCTYPE html><html><body>Sign in")
    assert not _looks_like_a_file(b"   <html><head><title>Request access")
    assert not _looks_like_a_file(b"")
    assert not _looks_like_a_file(b"PK")                       # too short to be real
    assert _looks_like_a_file(b"PK\x03\x04" + b"\x00" * 60)
    assert _looks_like_a_file(b"Status,Lot,Price\nAvailable,82,540220\n" + b"x" * 40)


def test_google_uses_gviz_only_as_a_fallback():
    """Tomorrow Homes and Verv are shared link-viewable with downloading disabled, so
    every /export URL answers 401 and only gviz serves them. It must be a fallback, not
    the default: gviz is lossier."""
    csv_body = "Status,Lot,Package\nAvailable,Lot 82,$540220\n"
    sess = _Stub({
        "/htmlview": _Resp('items.push({name: "Stock List", pageUrl: "x", gid: "0"});'),
        "/export?format=csv": _Resp(b"", 401),
        "/gviz/tq": _Resp(csv_body, 200, "text/csv"),
    })
    got = _fetch_google("https://docs.google.com/spreadsheets/d/" + "A" * 30 + "/edit#gid=0",
                        "Tomorrow Homes", sess)
    assert len(got) == 1 and not got[0].problem, got[0].problem
    assert got[0].kind == KIND_FILE and b"540220" in got[0].data
    assert "Tomorrow Homes" in got[0].label and "Stock List" in got[0].label
    # export was tried first, gviz only after it refused
    order = [u for u in sess.asked if "export" in u or "gviz" in u]
    assert order[0].endswith("gid=0") and "export" in order[0], order
    assert "gviz" in order[1], order


def test_google_refuses_to_substitute_another_tab_for_a_stale_gid():
    """The dangerous case. Eight NSW estates point at one workbook and differ only by
    #gid=. `export` answers 400 for a deleted tab, but `gviz` answers 200 with the FIRST
    tab's data — so every estate would silently receive the same stock and nothing would
    look broken."""
    sess = _Stub({
        "/htmlview": _Resp('items.push({name: "Stock List", pageUrl: "x", gid: "0"});'),
        "/gviz/tq": _Resp("Status,Lot,Package\nAvailable,Lot 1,$1\n", 200, "text/csv"),
        "/export?format=csv": _Resp("Status,Lot,Package\nAvailable,Lot 1,$1\n", 200, "text/csv"),
    })
    got = _fetch_google("https://docs.google.com/spreadsheets/d/" + "B" * 30 +
                        "/edit#gid=987654", "Sapphire - Rouse Hill", sess)
    assert len(got) == 1 and got[0].problem, "a stale gid was silently served another tab"
    assert "987654" in got[0].problem and "Stock List" in got[0].problem
    assert not got[0].data
    assert not any("gviz" in u or "export" in u for u in sess.asked), \
        "must not fetch anything once the gid is known to be gone"


def test_dropbox_private_home_url_is_reported_not_retried():
    """One of the 96 links points into the owner's private Dropbox file browser. It can
    never be read without their account, and saying so is the useful outcome."""
    sess = _Stub({})
    got = _fetch_dropbox("https://www.dropbox.com/home/eAgent/APARTMENTS/NSW/Babylon",
                         "New South Wales", sess)
    assert len(got) == 1 and got[0].problem
    assert "private" in got[0].problem.lower() and "re-share" in got[0].problem.lower()
    assert sess.asked == [], "no point requesting a URL that cannot work"


def test_torsion_is_reported_as_a_page_not_a_file():
    got = _fetch_torsion("https://referrer.torsionhomes.au/Stocklist.aspx?DriveID=b!x",
                         "Torsion Homes", None)
    assert len(got) == 1 and got[0].kind == KIND_HTML and not got[0].data


def test_smartsheet_wire_format_is_rebuilt_as_csv():
    """Smartsheet has no export for a published token, so the grid is rebuilt from the
    app's own payload. Currency cells must be re-symbolised: a bare 750000 is discarded
    by the price parser, which requires a literal '$'."""
    gid = "326387809"
    body = (
        "jsdSchema.ajaxMegaBulkRecordInsert([jsdSchema.TABLE_INDEX_GRIDCOLUMNDEF,56],false,"
        f"10,111,{gid},0,'SUBURB',0,null,9,0,true,0,201,false,"
        f"10,222,{gid},0,'LOT #',0,null,6,3,false,1,81,false,"
        f"10,333,{gid},0,'PACKAGE PRICE',0,null,6,3,false,2,81,false);\r\n"
        "jsdSchema.ajaxMegaBulkRecordInsert([jsdSchema.TABLE_INDEX_GRIDDATA,127],false,"
        f"10,9001,7001,111,9,{gid},0,'SUBURB',0,'Joyner',null,0,null,1134867798560257,0,2,1,false,"
        f"10,9002,7001,222,9,{gid},0,'LOT #',0,'3',null,0,null,1134867798560257,0,2,1,false,"
        f"10,9003,7001,333,9,{gid},0,'PACKAGE PRICE',0,null,750000,0,null,19024319086592,0,2,1,false);\r\n"
        "setAjaxResponseStatus(true);")
    out = _ss_to_csv(body).decode("utf-8")
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines[0] == "SUBURB,LOT #,PACKAGE PRICE", lines[0]
    assert lines[1].startswith("Joyner,3,"), lines[1]
    assert "$750,000" in lines[1], f"currency not re-symbolised: {lines[1]}"

    # A payload whose shape has shifted must produce nothing, so the caller reports a
    # failure instead of recording an empty stocklist.
    assert _ss_to_csv("setAjaxResponseStatus(true);") == b""


def test_project_name_is_read_off_the_file_when_no_heading_exists():
    """Apartments, townhouses and commercial are grouped by DEVELOPMENT, and on some
    pages the development is in no heading element at all — but it is in the file name."""
    cases = {
        "Victoria / eastbury-wheelers-hill-pricelist.pdf": "Eastbury Wheelers Hill",
        "Victoria / Knew Street_Pricelist_.xlsx": "Knew Street",
        "Balmain Donnybrook / Balmain Price List 20.06.26.pdf": "Balmain",
        "Creekbank Deanside / CREEKBANK-Pricing-MASTER 15-6-26 (1).pdf": "Creekbank",
        "Taylors Run / Taylors Run Price List- 24.06.26.pdf": "Taylors Run",
        "Victoria / Tunstall Village - Stage 2 Pricelist -.pdf": "Tunstall Village Stage 2",
    }
    for label, want in cases.items():
        assert _project_from_label(label) == want, \
            f"{label!r} -> {_project_from_label(label)!r}, wanted {want!r}"
    # nothing readable -> blank, never a guess
    assert _project_from_label("Victoria / 1101438-16S-PS-V1.pdf") == ""
    assert _project_from_label("") == ""

    # A first pass at this put these twelve values in the builder column across 374 rows,
    # which is worse than a blank because it looks like data. The bar is now high.
    for label, want in {
        "Access Portal": "",                       # the LINK's text, not a file name
        "Access Portal (tab Sheet1)": "",
        "Victoria / Pricelist.pdf": "",            # nothing left once noise is removed
        "Victoria / Stage 1.pdf": "",              # a slice of a development, not one
        "Victoria / 1101438-16S-PS-V1.pdf": "",    # an engineering plan
        "Victoria / Masterpricelist Millwell New.pdf": "Millwell",
        "Victoria / Masterpricelist St Clair.xlsx": "St Clair",
        "Victoria / Report Murcia.pdf": "Murcia",
        "Victoria / Solara April.pdf": "Solara",
        "Victoria / Waler Heights [ ].pdf": "Waler Heights",
        "Victoria / Highgate New.pdf": "Highgate",
        "Victoria / Rockdale S.pdf": "Rockdale",
        "Victoria / Yarra Park Stage 1 Bonus.pdf": "Yarra Park Stage 1",
        "Victoria / Kings Forest Flyer Stage One Release As At.pdf": "Kings Forest Stage One",
    }.items():
        assert _project_from_label(label) == want,             f"{label!r} -> {_project_from_label(label)!r}, wanted {want!r}"


def run_all():
    tests = [
        ("hosts route to their handler", test_every_host_routes_to_its_own_handler),
        ("html is never taken for a file", test_html_is_never_mistaken_for_a_file),
        ("google gviz is a fallback", test_google_uses_gviz_only_as_a_fallback),
        ("google refuses a stale gid", test_google_refuses_to_substitute_another_tab_for_a_stale_gid),
        ("dropbox private url reported", test_dropbox_private_home_url_is_reported_not_retried),
        ("torsion is a page not a file", test_torsion_is_reported_as_a_page_not_a_file),
        ("smartsheet rebuilt as csv", test_smartsheet_wire_format_is_rebuilt_as_csv),
        ("project name from file name", test_project_name_is_read_off_the_file_when_no_heading_exists),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] remote: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] remote: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
