"""The AI-drafted "Investment strengths" note, and the checks that make it safe to send.

Coleen and Colin asked for auto-generated investment strengths on 18 Aug 2026. Ahsan chose
FIGURES ONLY over the richer alternative the same day, and this file is where that choice
is enforced rather than requested: llama-3.1-8b-instant, asked about a suburb it does not
know, will produce a confident and specific train line or primary school, and these notes
go into a document someone reads before spending eight hundred thousand dollars.

A prompt asking a model not to invent is not a control. So every draft is checked before
it is returned, and a draft that fails is DROPPED rather than repaired — the box falls
back to empty, which is the state the report has always had and is honest about what we
know. These tests are that check.
"""
import json

import pytest

from api import narrative as N


FULL = {
    "suburb": "Armstrong Creek", "state": "VIC", "bedrooms": 4, "bathrooms": 2,
    "car_spaces": 2, "land_size_sqm": 448, "house_size_sqm": 259.4,
    "advertised_package_price": 812450, "incentive_amount": 15000,
    "title_status": "Titled",
}


# ------------------------------------------------------------------ the whitelist

def test_only_figures_reach_the_prompt():
    """The one assertion the whole design rests on.

    source_text, estate marketing copy, amenities_summary and recommendation_reason are
    free-form. Handing any of them to the model gives it prose to elaborate on, which is
    exactly what the endpoint exists to prevent.
    """
    facts, _ = N._facts_block(dict(
        FULL,
        source_text="Armstrong Creek 324 Dapple St Palomino Titled 448 $812,450",
        amenities_summary="Primary school 900 m; Geelong rail 6 km.",
        recommendation_reason="Best block in the release, walk to the town centre.",
        estate_context="Palomino Estate", builder_name="Hattan Homes",
        house_design="Sorrento 28", lot_address="Lot 324 Dapple Street",
    ))
    for leaked in ("Primary school", "rail", "Palomino", "Hattan", "Sorrento",
                   "town centre", "Dapple"):
        assert leaked not in facts, f"{leaked!r} reached the prompt"
    assert "Armstrong Creek" in facts, "the suburb NAME is allowed — it locates, not claims"
    assert "448" in facts and "812450" in facts


def test_empty_and_zero_fields_are_left_out():
    facts, _ = N._facts_block({"suburb": "Denman", "bedrooms": 0, "car_spaces": None,
                               "title_status": "", "land_size_sqm": 602})
    assert "Bedrooms" not in facts and "Car spaces" not in facts
    assert "Title status" not in facts
    assert "Land size (m2): 602" in facts


def test_the_allowed_numbers_come_from_the_facts():
    _, allowed = N._facts_block(FULL)
    assert {"448", "812450", "15000", "4", "2"} <= allowed


@pytest.mark.parametrize("written,expected", [
    ("$812,450", {"812450"}),
    ("812450", {"812450"}),
    ("259.4", {"259", "4"}),
    ("1 200 000", {"1200000"}),
    ("", set()),
])
def test_a_reformatted_number_is_still_recognised(written, expected):
    assert N._digits(written) == expected


# ------------------------------------------------------------------ the check

def _allowed():
    return N._facts_block(FULL)[1]


def test_a_clean_draft_is_accepted():
    ok, why = N._acceptable(
        "This package pairs a 259 m2 home with 448 m2 of land at 812450, and the title "
        "is already issued. A 15000 rebate reduces what the buyer actually pays.",
        _allowed(), "Armstrong Creek")
    assert ok, why


@pytest.mark.parametrize("draft", [
    "A primary school sits within walking distance of the block.",
    "The estate is close to the rail station and a shopping centre.",
    "Strong capital growth is expected in this corridor.",
    "Council has approved a new precinct nearby.",
    "The property is 5 minutes from the beach.",
    "Rental yield in the area supports an investor.",
])
def test_anything_about_the_AREA_is_refused(draft):
    """None of this was supplied, so the model wrote it from its own knowledge."""
    ok, why = N._acceptable(draft, _allowed(), "Armstrong Creek")
    assert not ok and why


@pytest.mark.parametrize("draft", [
    "The 448 m2 block is guaranteed to appreciate.",
    "At 812450 this is well below market.",
    "A risk-free entry for a first investor.",
])
def test_claims_a_stocklist_row_cannot_support_are_refused(draft):
    ok, why = N._acceptable(draft, _allowed(), "Armstrong Creek")
    assert not ok and why


def test_a_number_that_was_never_supplied_is_refused():
    ok, why = N._acceptable(
        "The 448 m2 block suits a family, and settlement is expected in 2027.",
        _allowed(), "Armstrong Creek")
    assert not ok
    assert "2027" in why


def test_the_model_declining_is_not_an_error():
    ok, why = N._acceptable("INSUFFICIENT", _allowed(), "x")
    assert not ok and why == "model declined"


def test_an_empty_answer_is_refused():
    assert N._acceptable("", _allowed(), "x")[0] is False
    assert N._acceptable("   ", _allowed(), "x")[0] is False


def test_an_essay_is_refused():
    ok, why = N._acceptable("448 " * 200, _allowed(), "x")
    assert not ok and why == "too long"


# ------------------------------------------------------------------ the endpoint

def test_a_sparse_option_is_skipped_without_calling_groq(monkeypatch):
    called = []
    monkeypatch.setattr(N, "_groq", lambda p: called.append(p) or ("x", ""))
    out = N.drafts_for([{"suburb": "Denman"}])
    assert called == [], "no point spending a call on two figures"
    assert out[0]["text"] == "" and out[0]["skipped"]


def test_a_refused_draft_returns_an_empty_box_and_a_reason(monkeypatch):
    monkeypatch.setattr(N, "_groq",
                        lambda p: ("Walk to the primary school from this block.", ""))
    out = N.drafts_for([FULL])
    assert out[0]["text"] == "", "a refused draft must never reach the report"
    assert "school" in out[0]["skipped"]


def test_a_groq_error_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(N, "_groq", lambda p: ("", "Groq rate limit reached — try again shortly."))
    out = N.drafts_for([FULL])
    assert out[0]["text"] == "" and "rate limit" in out[0]["skipped"]


def test_drafts_keep_the_order_they_were_given(monkeypatch):
    monkeypatch.setattr(N, "_groq", lambda p: ("The 448 m2 block is titled.", ""))
    out = N.drafts_for([FULL, dict(FULL, land_size_sqm=448), FULL])
    assert [d["index"] for d in out] == [0, 1, 2]


def test_no_more_than_the_reports_own_cap(monkeypatch):
    monkeypatch.setattr(N, "_groq", lambda p: ("The 448 m2 block is titled.", ""))
    assert len(N.drafts_for([FULL] * 20)) == N.MAX_OPTIONS


def test_the_key_is_never_returned(monkeypatch):
    """It is read from the environment and must not appear in any response."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-secret-value-do-not-leak")
    monkeypatch.setattr(N, "_groq", lambda p: ("The 448 m2 block is titled.", ""))
    blob = json.dumps({"configured": True, "drafts": N.drafts_for([FULL])})
    assert "gsk-secret" not in blob
    assert N._key() == "gsk-secret-value-do-not-leak"


def test_an_http_error_body_is_not_echoed(monkeypatch):
    """Groq's error bodies can quote the request; only the status is acted on."""
    import urllib.error

    def boom(req, timeout=0):
        raise urllib.error.HTTPError("u", 401, "Bad key gsk-secret", {}, None)

    monkeypatch.setenv("GROQ_API_KEY", "gsk-secret")
    monkeypatch.setattr(N.urllib.request, "urlopen", boom)
    text, err = N._groq("anything")
    assert text == ""
    assert "gsk-secret" not in err and "rejected" in err.lower()
