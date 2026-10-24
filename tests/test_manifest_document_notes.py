"""What the readers tell the harvest manifest about a document's own size (24 Oct 2026).

The retirement ledger (list_ledger) accepts a read as whole only on evidence, and some of
the best evidence is said once, by the reader, the moment it fetches a document: a Google
tab's whole-export CSV row count, and a Dropbox folder whose file or folder budget bound.
Until now it went to the log and nowhere else. harvest_manifest now keeps it -- keyed by
the hash of the url, never the url, because the repo is public and a share link is a
capability -- and the schema stays 1 (additive fields only)."""
import json
import sys

import pytest

import harvest_manifest as HM
import sources.remote_stocklist as RS

SHEET = "https://docs.google.com/spreadsheets/d/" + "V" * 30 + "/edit#gid=0"
FOLDER = "https://www.dropbox.com/scl/fo/abc123/folder?rlkey=secretkey&dl=0"


class _Resp:
    def __init__(self, content=b"", status=200, ctype=""):
        self.content, self.status_code = content, status
        self.text = content.decode("utf-8", "replace")
        self.headers = {"Content-Type": ctype}


class _Sess:
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, **kw):
        for part, resp in self.routes.items():
            if part in url:
                return resp
        return _Resp(b"PK" + b"x" * 64)


@pytest.fixture(autouse=True)
def _a_clean_manifest():
    HM.reset()
    yield
    HM.reset()


@pytest.fixture
def folder(monkeypatch):
    tree = {}
    monkeypatch.setattr(RS, "_dropbox_entries",
                        lambda sess, url: tree.get(url, tree.get("__root__", [])))
    return tree


def _entries(*names):
    return [(n, False, f"https://www.dropbox.com/x/{n}?dl=0") for n in names]


@pytest.mark.parametrize("export_status,endpoint", [(200, "export"), (401, "gviz")])
def test_a_google_tab_tells_the_manifest_its_rows_and_its_endpoint(monkeypatch,
                                                                   export_status, endpoint):
    monkeypatch.setattr(RS, "_google_tabs", lambda sess, sid: {"0": "Stock List"})
    body = b"Lot,Price\nLot 1,$500000\nLot 2,$510000\n"
    sess = _Sess({"/export": _Resp(body if export_status == 200 else b"", export_status,
                                   "text/csv"),
                  "/gviz/": _Resp(body, 200, "text/csv")})
    got = RS._fetch_google(SHEET, "Verv", sess)
    assert len(got) == 1 and not got[0].problem
    assert HM.DOCUMENTS[HM.stocklist_key(got[0].url)] == {"csv_rows": 3,
                                                          "endpoint": endpoint}, \
        "keyed by the url the rows store as source_url"


def test_a_folder_whose_file_budget_binds_is_noted(folder):
    folder["__root__"] = _entries(*[f"Price List {i}.xlsx"
                                    for i in range(RS._DBX_MAX_FILES + 2)])
    RS._fetch_dropbox(FOLDER, "VIC", _Sess({}))
    note = HM.LINK_NOTES[HM.stocklist_key(FOLDER)]
    assert note["truncated"] and "budget" in note["why"]


def test_an_empty_folder_listing_is_noted(folder):
    folder["__root__"] = []
    RS._fetch_dropbox(FOLDER, "VIC", _Sess({}))
    assert HM.LINK_NOTES[HM.stocklist_key(FOLDER)]["truncated"]


def test_a_folder_read_whole_is_not_noted(folder):
    folder["__root__"] = _entries("VIC Price List.xlsx", "Second Sheet.xlsx")
    RS._fetch_dropbox(FOLDER, "VIC", _Sess({}))
    assert not HM.LINK_NOTES


def test_every_entry_names_its_unit_and_the_file_holds_no_link(tmp_path):
    e = HM.entry("E-Agent", "stock link", FOLDER, name="Example Homes")
    assert e["unit"] == HM.stocklist_key(FOLDER)
    HM.note_document("https://docs.google.com/spreadsheets/d/X/export?format=csv&gid=0",
                     csv_rows=5, endpoint="export")
    HM.note_link(FOLDER, why=f"a subfolder of {FOLDER} could not be listed")
    text = HM.write(tmp_path / "m.json").read_text(encoding="utf-8")
    assert "dropbox.com" not in text and "docs.google" not in text and "rlkey" not in text
    m = json.loads(text)
    assert m["schema"] == 1
    assert list(m["documents"].values()) == [{"csv_rows": 5, "endpoint": "export"}]
    assert list(m["link_notes"].values())[0]["truncated"] is True
    HM.reset()
    assert not HM.DOCUMENTS and not HM.LINK_NOTES and not HM.ENTRIES


def test_the_readers_never_need_harvest_manifest(monkeypatch, folder):
    """The webhook bundle ships remote_stocklist without harvest_manifest, and the sheet
    check runs in its own process: the notes are skipped, never fatal."""
    monkeypatch.setitem(sys.modules, "harvest_manifest", None)
    RS._note_document("https://x.example/a", csv_rows=1, endpoint="export")
    RS._note_truncated("https://x.example/a", "the file budget bound")
    folder["__root__"] = []
    assert RS._fetch_dropbox(FOLDER, "VIC", _Sess({}))[0].problem


def test_the_parity_check_still_reads_the_entries():
    import parity_check
    m = {"schema": 1, "entries": [{"kind": "portal", "name": "X"}],
         "documents": {"k": {"csv_rows": 1}}, "link_notes": {"k": {"truncated": True}}}
    assert parity_check.manifest_entries(m) == [{"kind": "portal", "name": "X"}]


def test_the_manifest_the_harvest_writes_is_the_one_the_ledger_reads(tmp_path):
    """End to end: a tab's export count written by harvest_manifest proves the list whole
    for list_ledger, through read_manifest and Tonight."""
    from datetime import datetime
    import list_ledger as LL
    url = "https://docs.google.com/spreadsheets/d/X/export?format=csv&gid=0"
    HM.note_document(url, csv_rows=13, endpoint="export")
    path = HM.write(tmp_path / "harvest_manifest.json")
    m = LL.read_manifest(path, now=datetime.now())
    today = datetime.now().strftime("%d/%m/%Y")
    rows = [{"id": i, "source_channel": "E-Agent", "source_url": url, "stocklist_file": url,
             "superseded_by": None, "date_checked": today, "builder_name": "Verv"}
            for i in range(12)]
    veto, proof = LL.Tonight(m, rows).veto_and_proof((url, ""), rows, 12)
    assert veto is None and proof.startswith("export csv: 12 listing(s) for 13 row(s)")
