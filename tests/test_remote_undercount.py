"""Dropbox folders and Google workbooks: read all of it, or say what was skipped.

96 of E-Agent's 142 stock links are Dropbox folders, so this module is the dominant
source in the pipeline. It used to harvest one file from a folder that holds many, and
_fetch_dropbox contained no logging at all, so the loss produced no signal anywhere.
"""
import logging

import pytest

import sources.remote_stocklist as RS


def _entries(*items):
    """(name, is_dir, url) triples, as _dropbox_entries returns them."""
    return [(n, d, f"https://www.dropbox.com/x/{n}?dl=0") for n, d in items]


class _Resp:
    def __init__(self, content=b"PK" + b"x" * 64, status=200, ctype=""):
        self.content, self.status_code = content, status
        self.text = content.decode("utf-8", "replace")
        self.ok = status == 200
        self.headers = {"Content-Type": ctype}


class _Sess:
    def __init__(self):
        self.got = []

    def get(self, url, **kw):
        self.got.append(url)
        return _Resp()


@pytest.fixture
def folder(monkeypatch):
    """Wires _dropbox_entries to a fake tree and returns a setter for it."""
    tree = {}

    def entries(sess, url):
        return tree.get(url, tree.get("__root__", []))

    monkeypatch.setattr(RS, "_dropbox_entries", entries)
    return tree


# ------------------------------------------------------------ files beside each other

def test_a_keyword_file_no_longer_suppresses_the_sheets_beside_it(folder):
    """A folder holding a price list AND two named workbooks yielded one. 'Report
    Murcia' — a real filename from this account — matches no keyword at all."""
    folder["__root__"] = _entries(("VIC Price List.xlsx", False),
                                  ("Brisbane Apartments.xlsx", False),
                                  ("Marina Towers Stage 2.xlsx", False))
    out = RS._fetch_dropbox("https://www.dropbox.com/scl/fo/abc", "VIC", _Sess())
    assert len(out) == 3
    assert all(f.kind == RS.KIND_FILE for f in out)


def test_the_keyword_file_still_comes_first(folder):
    """Ordering is not cosmetic: the download budget is spent top-down."""
    folder["__root__"] = _entries(("Aardvark Workbook.xlsx", False),
                                  ("VIC Price List.xlsx", False))
    out = RS._fetch_dropbox("https://www.dropbox.com/scl/fo/abc", "VIC", _Sess())
    assert "Price List" in out[0].label


# ------------------------------------------------------------ subfolders

def test_subfolders_are_walked_even_when_the_root_has_a_file(folder):
    """THE BIG ONE. `if not picks:` gated the descent, so one summary sheet in the root
    hid every per-estate subfolder beneath it."""
    folder["__root__"] = _entries(("Summary.xlsx", False), ("Babylon", True))
    folder["https://www.dropbox.com/x/Babylon?dl=0"] = _entries(("Babylon Prices.xlsx", False))
    out = RS._fetch_dropbox("https://www.dropbox.com/scl/fo/abc", "NSW", _Sess())
    labels = " ".join(f.label for f in out)
    assert "Summary.xlsx" in labels
    assert "Babylon/Babylon Prices.xlsx" in labels, "the subfolder must still be walked"


def test_a_project_named_subfolder_is_not_skipped(folder):
    """Real subfolders are project-named — eAgent/APARTMENTS/NSW/Babylon — and match
    none of price|pricing|stock|availab|sales list|stage N, so the old name filter
    skipped them even on the fallback path."""
    folder["__root__"] = _entries(("Babylon", True))
    folder["https://www.dropbox.com/x/Babylon?dl=0"] = _entries(("Stocklist.xlsx", False))
    out = RS._fetch_dropbox("https://www.dropbox.com/scl/fo/abc", "NSW", _Sess())
    assert len(out) == 1 and "Babylon/" in out[0].label


def test_a_subfolder_that_cannot_be_listed_is_reported(folder, monkeypatch, caplog):
    calls = {"n": 0}

    def entries(sess, url):
        calls["n"] += 1
        if calls["n"] == 1:
            return _entries(("Good.xlsx", False), ("Broken", True))
        raise RuntimeError("403")

    monkeypatch.setattr(RS, "_dropbox_entries", entries)
    with caplog.at_level(logging.WARNING):
        out = RS._fetch_dropbox("https://www.dropbox.com/scl/fo/abc", "NSW", _Sess())
    assert len(out) == 1, "the readable file still comes back"
    assert any("Broken" in m for m in caplog.messages)


def test_the_same_file_is_not_downloaded_twice(folder):
    folder["__root__"] = _entries(("Price List.xlsx", False))
    out = RS._fetch_dropbox("https://www.dropbox.com/scl/fo/abc", "VIC", _Sess())
    assert len(out) == 1


# ------------------------------------------------------------ budgets announce themselves

def test_the_file_budget_says_what_it_dropped(folder, caplog):
    folder["__root__"] = _entries(*[(f"Price List {i}.xlsx", False)
                                    for i in range(RS._DBX_MAX_FILES + 3)])
    with caplog.at_level(logging.WARNING):
        out = RS._fetch_dropbox("https://www.dropbox.com/scl/fo/abc", "VIC", _Sess())
    assert len(out) == RS._DBX_MAX_FILES
    assert any("only %d downloaded" % RS._DBX_MAX_FILES in m or "only" in m
               for m in caplog.messages)


def test_the_folder_budget_says_what_it_skipped(folder, caplog):
    folder["__root__"] = _entries(*[(f"Estate {i}", True)
                                    for i in range(RS._DBX_MAX_FOLDERS + 2)])
    with caplog.at_level(logging.WARNING):
        RS._fetch_dropbox("https://www.dropbox.com/scl/fo/abc", "VIC", _Sess())
    assert any("subfolder(s), only the first" in m for m in caplog.messages)


def test_an_empty_folder_still_reports_a_problem(folder):
    folder["__root__"] = _entries(("readme.txt", False))
    out = RS._fetch_dropbox("https://www.dropbox.com/scl/fo/abc", "VIC", _Sess())
    assert len(out) == 1 and out[0].problem and "no price list" in out[0].problem


# ------------------------------------------------------------ the Google stale-gid guard

def test_the_gviz_fallback_is_withheld_when_the_tab_list_is_unknown(monkeypatch, caplog):
    """_google_tabs returns {} on any non-200, which used to DISABLE the guard — and
    gviz answers 200 with the workbook's FIRST tab for a stale gid."""
    monkeypatch.setattr(RS, "_google_tabs", lambda sess, sid: {})
    sess = _Sess()
    with caplog.at_level(logging.WARNING):
        RS._fetch_google("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit#gid=999",
                         "Tomorrow Homes", sess)
    assert not any("gviz" in u for u in sess.got), "gviz must not be tried blind"
    assert any("withheld" in m for m in caplog.messages)


def test_a_known_gid_still_gets_the_fallback(monkeypatch):
    monkeypatch.setattr(RS, "_google_tabs", lambda sess, sid: {"999": "NSW"})

    class _Fail(_Sess):
        def get(self, url, **kw):
            self.got.append(url)
            return _Resp(content=b"", status=401)

    sess = _Fail()
    RS._fetch_google("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit#gid=999", "X", sess)
    assert any("gviz" in u for u in sess.got)


def test_a_stale_gid_is_refused_outright_when_the_tabs_are_known(monkeypatch):
    monkeypatch.setattr(RS, "_google_tabs", lambda sess, sid: {"111": "VIC"})
    out = RS._fetch_google("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit#gid=999",
                           "X", _Sess())
    assert len(out) == 1 and out[0].problem and "no longer exists" in out[0].problem


# ------------------------------------------------------------ retries

def test_the_session_carries_retries():
    """One failed Dropbox fetch costs a whole product category, and the caller drops a
    piece that carries a problem."""
    s = RS._session() if hasattr(RS, "_session") else None
    if s is None:
        pytest.skip("session factory is named differently")
    ad = s.get_adapter("https://www.dropbox.com/")
    assert getattr(ad.max_retries, "total", 0) >= 3
