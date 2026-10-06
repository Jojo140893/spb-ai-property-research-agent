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


@pytest.fixture(scope="session")
def _stocklist_scratch(tmp_path_factory):
    return tmp_path_factory.mktemp("eagent_stocklist")


@pytest.fixture(autouse=True)
def _eagent_stocklist_stays_offline(monkeypatch, _stocklist_scratch):
    """NEVER THE REAL EAGENT STOCKLIST, AND NEVER THE REPO'S logs/ (7 Oct 2026).

    harvest() now runs sources/eagent_stocklist.py whenever it runs E-Agent, and several
    tests call harvest() with E-Agent on. On a machine where the owner has stored the
    Stocklist login (python setup_credentials.py eagent_stocklist) those tests would sign
    in to eagent.mekooflow.com. So every test points the reader at a closed local port --
    a test that wants a site passes base_url= to its own fake -- and its status file and
    capture go to a scratch folder instead of logs/.
    """
    monkeypatch.setenv("EAGENT_STOCKLIST_URL", "http://127.0.0.1:9")
    try:
        from sources import eagent_stocklist as es
    except Exception:                                                   # noqa: BLE001
        return
    monkeypatch.setattr(es, "STATUS_PATH", _stocklist_scratch / "status.json")
    monkeypatch.setattr(es, "CAPTURE_DIR", _stocklist_scratch / "capture")
