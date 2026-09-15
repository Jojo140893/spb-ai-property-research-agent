"""A suburb said out loud is the same suburb — but only when it can be nothing else.

Clare, live call 15 Sep 2026. The caller said "Red Bank Plains"; the Queensland locality
is "Redbank Plains". The locality index was keyed on the exact lower-case name, so
locate() answered None and api/research.py searched an area of ONE suburb instead of the
154 that "Redbank Plains" reaches at 20 km. Every nearby listing was then refused with
"Location could not be verified ... so the 20 km radius from Red Bank Plains cannot be
confirmed". Callers speak suburb names and Vapi transcribes them, and the Research tab
takes them typed, so spacing, hyphen, apostrophe and Mt/Mount, St/Saint variants are the
normal case.

The fallback folds exactly those differences, is used only where a person gave the name,
and refuses any key that names more than one locality in the state.
"""
import json
import os
import sys

import pytest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, "api"))

from geo import SuburbGeoIndex  # noqa: E402

_GEO = SuburbGeoIndex()


def _spoken(name, state):
    return (_GEO.locate(name, state, spoken=True),
            _GEO.canonical_suburb(name, state, spoken=True))


# ------------------------------------------------------------------ the incident itself

def test_red_bank_plains_is_redbank_plains():
    assert _GEO.locate("Redbank Plains", "QLD"), "premise: Redbank Plains is a QLD locality"
    assert _spoken("Red Bank Plains", "QLD") == (_GEO.locate("Redbank Plains", "QLD"),
                                                 "Redbank Plains")
    assert _GEO.states_for_suburb("Red Bank Plains", spoken=True) == ["QLD"]
    assert _GEO.canonical_suburb("Red Bank Plains", spoken=True) == "Redbank Plains"


def test_the_search_area_is_the_same_area():
    said = _GEO.expand_search_suburbs(["Red Bank Plains"], "QLD", 20)
    spelt = _GEO.expand_search_suburbs(["Redbank Plains"], "QLD", 20)
    assert len(spelt) > 100, "premise: 20 km around Redbank Plains is a real area"
    assert said == spelt, "the spoken name must search the same suburbs"
    origin = [a for a in said if a["distance_km"] == 0.0]
    assert [a["suburb"] for a in origin] == ["Redbank Plains"], \
        "the origin must carry the spelling listings are filed under, and appear once"


def test_distance_is_measured_from_a_spoken_origin():
    assert _GEO.distance_between("Springfield Lakes", "Red Bank Plains", "QLD") == \
        _GEO.distance_between("Springfield Lakes", "Redbank Plains", "QLD") is not None


# ------------------------------------------------------------- the shapes people say

def test_mt_and_mount():
    assert _spoken("Mt Cotton", "QLD")[1] == "Mount Cotton"
    assert _spoken("Mt. Cotton", "QLD")[1] == "Mount Cotton"
    assert _spoken("Mount Baw Baw", "VIC")[1] == "Mt Baw Baw", "the fold runs both ways"


def test_st_and_saint():
    assert _spoken("Saint Albans", "VIC") == (_GEO.locate("St Albans", "VIC"), "St Albans")
    assert _spoken("Saint Albans", "NSW") == (_GEO.locate("St Albans", "NSW"), "St Albans")
    assert _GEO.states_for_suburb("Saint Albans", spoken=True) == ["NSW", "VIC"]


def test_spacing_and_hyphens():
    assert _spoken("Pointcook", "VIC")[1] == "Point Cook"
    assert _spoken("Wyndham-Vale", "VIC")[1] == "Wyndham Vale"
    assert _spoken("Kippa Ring", "QLD")[1] == "Kippa-Ring"
    assert _spoken("Mt Kuring Gai", "NSW")[1] == "Mount Kuring-Gai"


def test_apostrophes():
    assert _spoken("Tam OShanter", "QLD")[1] == "Tam O'Shanter"
    assert _spoken("Tam O’Shanter", "QLD")[1] == "Tam O'Shanter", "a curly apostrophe"


def test_the_state_is_asked_before_the_same_spelling_elsewhere():
    """"South Bank" is spelt exactly like Brisbane's South Bank, but said in VIC it is
    Melbourne's Southbank. canonical_suburb answered with the QLD spelling before trying
    the fold in VIC, so the radius-0 respell in api/research.py left the brief as "South
    Bank" and matched no VIC stock, while locate() and the search area already said
    Southbank: one response naming two different places."""
    for said, state, spelt in (("South Bank", "VIC", "Southbank"),
                               ("Campbell Town", "NSW", "Campbelltown")):
        elsewhere = _GEO.states_for_suburb(said)
        assert elsewhere and state not in elsewhere, \
            "premise: %s is spelt exactly as a locality in another state" % said
        assert _GEO.canonical_suburb(said, state, spoken=True) == spelt, said
        assert _GEO.locate(said, state, spoken=True) == _GEO.locate(spelt, state), said
    assert _GEO.canonical_suburb("South Bank", "QLD", spoken=True) == "South Bank"
    assert _GEO.canonical_suburb("South Bank", "VIC") == "South Bank", \
        "spoken=False is unchanged: the exact spelling, whatever the state"


# ------------------------------------------------------------------- never inventing

def test_a_key_two_localities_share_names_neither():
    """Red Head and Redhead are two NSW localities 100 km apart. A caller saying
    "Red-Head" could mean either, and a coin toss is not a location."""
    assert _GEO.locate("Red Head", "NSW") != _GEO.locate("Redhead", "NSW"), "premise"
    for name, state in (("Red-Head", "NSW"), ("Willow-Vale", "QLD"),
                        ("Crystal-Brook", "QLD"), ("Mt. Britton", "QLD")):
        assert _GEO.locate(name, state, spoken=True) is None, name
        assert _GEO.canonical_suburb(name, state, spoken=True) == "", name
        assert _GEO.canonical_suburb(name, spoken=True) == "", name
        assert _GEO.states_for_suburb(name, spoken=True) == [], name


def test_the_state_still_decides():
    assert _GEO.locate("Red Bank Plains", "NSW", spoken=True) is None


def test_digits_and_lone_letters_are_not_folded():
    """'Lot A' would fold to Lota and 'Tarneit 3029' to Tarneit. A hyphen, full stop or
    slash is a word break too: splitting on spaces alone let 'Lot-A' through to Lota."""
    for name, state in (("Lot A", "QLD"), ("Tarneit 3029", "VIC"), ("NE A", "NSW"),
                        ("Lot-A", "QLD"), ("Ne-A", "NSW"), ("Lot.A", "QLD"),
                        ("Lot/A", "QLD")):
        assert _GEO.locate(name, state, spoken=True) is None, name
        assert _GEO.canonical_suburb(name, state, spoken=True) == "", name
        assert _GEO.canonical_suburb(name, spoken=True) == "", name
        assert _GEO.states_for_suburb(name, spoken=True) == [], name


def test_exact_names_are_unchanged():
    """Both halves of a colliding pair keep resolving to themselves."""
    for name, state in (("Red Head", "NSW"), ("Redhead", "NSW"), ("Mt Britton", "QLD"),
                        ("Mount Britton", "QLD"), ("St Helena", "VIC"),
                        ("Saint Helena", "VIC"), ("Redbank Plains", "QLD")):
        exact = _GEO.locate(name, state)
        assert exact is not None, name
        assert _GEO.locate(name, state, spoken=True) == exact, name
        assert _GEO.canonical_suburb(name, state, spoken=True) == \
            _GEO.canonical_suburb(name, state), name
    assert len(_GEO.states_for_suburb("Springfield", spoken=True)) > 1


def test_free_text_lookups_stay_exact():
    """The fallback is opt-in. Every stocklist scanner asks these methods about fragments
    of free text, and folding there turned a builder called "Lakeview" into Lake View,
    South Australia, on 13 stored rows."""
    assert _GEO.locate("Red Bank Plains", "QLD") is None
    assert _GEO.states_for_suburb("Lakeview") == []
    assert _GEO.canonical_suburb("Pointcook", "VIC") == ""

    import suburb_quality
    suburb, _why = suburb_quality.resolve(
        {"builder_name": "Lakeview", "suburb": "", "state": "",
         "lot_address": "Total 250 250 External 22 22 3 0 0 0 0"}, _GEO)
    assert suburb == "", "a builder's name was read as a South Australian locality"


# ------------------------------------------------------------ an estate is not a suburb

def test_an_estate_name_is_not_folded_onto_a_namesake_far_away():
    """Newpark is an estate at Melonba (NSW). Its name folds to New Park, NSW, 604 km
    away, so typed into the Research tab it searched 43 suburbs around the wrong town and
    measured every lot from there -- where before it was plainly a place we could not
    find. Whiterock (QLD) folds to White Rock, which is where that estate's stock is
    filed, so that fold is right and must keep working."""
    index = SuburbGeoIndex()
    assert index.canonical_suburb("Newpark", "NSW", spoken=True) == "New Park", \
        "premise: with no estates known the name folds to New Park"
    index.know_estates([("Newpark", "Melonba", "NSW"),
                        ("Whiterock", "White Rock", "QLD"),
                        ("Springfield", "Springfield Lakes", "QLD")])
    # "New-Park" too: said in NSW it is the estate or the town, and it can't be both.
    for said in ("Newpark", "NEWPARK", "New-Park"):
        assert index.locate(said, "NSW", spoken=True) is None, said
        assert index.canonical_suburb(said, "NSW", spoken=True) == "", said
        assert index.canonical_suburb(said, spoken=True) == "", said
        assert index.states_for_suburb(said, spoken=True) == [], said
        assert index.distance_between("Melonba", said, "NSW") is None, said
    assert index.expand_search_suburbs(["Newpark"], "NSW", 20) == [
        {"suburb": "Newpark", "state": "NSW", "distance_km": 0.0, "origin": "Newpark"}], \
        "an area of one, labelled as typed: visibly a place that could not be found"
    assert index.locate("New Park", "NSW", spoken=True), "the town itself, said exactly"
    assert index.canonical_suburb("Whiterock", "QLD", spoken=True) == "White Rock"
    assert index.canonical_suburb("Spring Field", "QLD", spoken=True) == "Springfield", \
        "an estate named after a real locality does not stop that locality folding"

    index.know_estates([])
    assert index.canonical_suburb("Newpark", "NSW", spoken=True) == "New Park", \
        "know_estates replaces what was known; it does not accumulate"


# ------------------------------------------------------------ the endpoint, offline

def _snapshot():
    # vercel_site/ is gitignored, so a fresh clone or a CI runner has no snapshot. Skip
    # there, as tests/test_shortlist_carries_lot_number.py does, instead of erroring on a
    # reason that has nothing to do with suburb names.
    path = os.environ.get("SPB_SNAPSHOT_JSON") or os.path.join(APP, "vercel_site",
                                                               "stock.json")
    if not os.path.exists(path):
        pytest.skip("no local stock.json snapshot")
    return path


def _research(name, radius, state="QLD", budget=5000000):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SPB_SNAPSHOT_JSON", _snapshot())
        import research
        return research.run_research({"client_brief": {
            "client_name": "T", "state": state, "primary_suburbs": [name],
            "budget_max": budget, "search_radius_km": radius}})


def _hashes(result):
    return sorted(p.get("content_hash") for p in result["shortlist"])


def test_research_searches_the_same_area_for_the_spoken_name():
    said = _research("Red Bank Plains", 20)
    spelt = _research("Redbank Plains", 20)
    assert len(spelt["search_area"]) > 100, "premise: the snapshot search area is real"
    assert said["search_area"] == spelt["search_area"]
    assert said["shortlist_count"] == spelt["shortlist_count"]
    refused = [r for r in said["rejected_log"]
               if "radius from Red Bank Plains cannot be confirmed" in str(r.get("reason"))]
    assert not refused, refused[:2]


def test_research_at_radius_zero_matches_the_spoken_name():
    """The Research tab's default. The exact-suburb filter compares brief names verbatim.

    The premise is ASSERTED, not skipped: a skip here turned a broken respell into a green
    run the first time a harvest left no Redbank Plains stock."""
    spelt = _research("Redbank Plains", 0)
    assert spelt["shortlist"], ("premise: the snapshot has shortlistable Redbank Plains "
                                "stock -- if a harvest removed it, pick another suburb")
    said = _research("Red Bank Plains", 0)
    assert _hashes(said) == _hashes(spelt)


def test_research_at_radius_zero_reads_the_state_before_the_spelling():
    """Melbourne's Southbank said as "South Bank", which is Brisbane's exact spelling."""
    spelt = _research("Southbank", 0, state="VIC")
    assert spelt["shortlist"], ("premise: the snapshot has shortlistable Southbank VIC "
                                "stock -- if a harvest removed it, pick another suburb")
    said = _research("South Bank", 0, state="VIC")
    assert _hashes(said) == _hashes(spelt)
    refused = [r for r in said["rejected_log"]
               if "requested suburbs (South Bank)" in str(r.get("reason"))]
    assert not refused, refused[:2]


def test_research_does_not_search_around_an_estates_namesake():
    """"Newpark" typed for the estate at Melonba must not become a search of New Park,
    604 km away. The respell and the agent's own geo index both have to know it."""
    with open(_snapshot(), encoding="utf-8") as f:
        data = json.load(f)
    at = {k: i for i, k in enumerate(data["keys"])}
    filed = {str(r[at["suburb"]] or "") for r in data["rows"]
             if str(r[at["estate_name"]] or "").strip().lower() == "newpark"
             and str(r[at["state"]] or "").upper() == "NSW" and not r[at["superseded_by"]]}
    assert filed and "New Park" not in filed, ("premise: the snapshot carries the Newpark "
                                              "estate, filed away from New Park")

    out = _research("Newpark", 20, state="NSW")
    assert out["search_area"] == [
        {"suburb": "Newpark", "state": "NSW", "distance_km": 0.0, "origin": "Newpark"}]
    assert "New Park" not in out["research_record_id"]
    moved = [r for r in out["rejected_log"] if "New Park" in str(r.get("reason"))]
    assert not moved, moved[:2]
