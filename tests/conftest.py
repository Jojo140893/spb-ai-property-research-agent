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
