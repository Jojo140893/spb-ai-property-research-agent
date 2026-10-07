"""Suite-wide guards.

NO GROQ KEY UNLESS A TEST ASKS FOR ONE. Since 15 Sep 2026 api/research.py's run_research
calls Groq (api/_location_resolver.py) whenever GROQ_API_KEY is set and a brief names a
suburb the locality index cannot place. Several offline tests run run_research with exactly
such names on purpose -- "Newpark" in test_spoken_suburb_names.py, junk values in
test_brief_robustness.py -- so on a laptop or runner that holds the key they would reach the
network, and a test that reaches the network is a bug. A test that wants a key sets one with
monkeypatch.setenv, which runs after this fixture.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_groq_key_unless_a_test_sets_one(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)


# THE HARVEST'S NIGHTLY FILES ARE PUT BACK AFTER EVERY TEST (7 Oct 2026). harvest() now
# writes data/unreadable_stocklists.json and data/harvest_manifest.json on every run, not
# only a failed one, so any test that drives harvest() would otherwise leave the committed
# copy rewritten with test data.
_HARVEST_FILES = ("unreadable_stocklists.json", "harvest_manifest.json")


@pytest.fixture(autouse=True)
def _harvest_files_are_put_back():
    from pathlib import Path
    data = Path(__file__).resolve().parent.parent / "data"
    saved = {n: ((data / n).read_bytes() if (data / n).exists() else None)
             for n in _HARVEST_FILES}
    yield
    for n, body in saved.items():
        if body is None:
            (data / n).unlink(missing_ok=True)
        elif not (data / n).exists() or (data / n).read_bytes() != body:
            (data / n).write_bytes(body)
