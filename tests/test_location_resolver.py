"""What a caller meant, read by Groq and decided by the suburb index.

Ahsan, 15 Sep 2026: "use the Groq API key to eliminate any address issue you might face in
the future". Callers speak a location and Vapi transcribes it. geo.py's spoken fold fixes
"Red Bank Plains"; it cannot hear "Tarn eat" as Tarneit, "Wyndum Vale" as Wyndham Vale, or
"western suburbs of Melbourne" as the suburbs there. api/_location_resolver.py asks Groq for
those -- and then believes only what data/au_suburbs.csv confirms in the brief's state,
because NEVER GUESS A FACT applies to a model's answer as much as to a scraped row.

NOTHING HERE TOUCHES THE NETWORK. urllib.request.urlopen is replaced for every test that
could reach Groq, the replacement refuses any other URL, and socket connections raise.
"""
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

import pytest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, "api"))

import _location_resolver as LR  # noqa: E402
from geo import SuburbGeoIndex  # noqa: E402

_GEO = SuburbGeoIndex()
KEY = "gsk-test-not-a-real-key"


class _Reply:
    def __init__(self, raw):
        self._raw = raw

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def answer(results=None, content=None):
    """A Groq chat-completions body whose message content is `content`, or the results."""
    text = content if content is not None else json.dumps({"results": results or []})
    return json.dumps({"choices": [{"message": {"content": text}}]}).encode()


class Groq:
    """urlopen, stubbed. `respond` is bytes, a callable(request_body) -> bytes, or an
    exception to raise. Every request is kept; a request anywhere else is refused."""

    def __init__(self):
        self.calls, self.blocked, self.respond = [], [], answer([])

    def urlopen(self, req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if url != LR.GROQ_URL:
            self.blocked.append(url)
            raise AssertionError("a test tried to reach " + url)
        body = json.loads(req.data.decode("utf-8"))
        self.calls.append({"body": body, "timeout": timeout,
                           "phrases": json.loads(body["messages"][1]["content"]
                                                 .split("Phrases:\n", 1)[1])["phrases"]})
        r = self.respond
        if isinstance(r, BaseException):
            raise r
        return _Reply(r(body) if callable(r) else r)


def _no_network(*_a, **_k):
    raise AssertionError("a test reached the network")


@pytest.fixture
def groq(monkeypatch):
    stub = Groq()
    monkeypatch.setattr(urllib.request, "urlopen", stub.urlopen)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    # The resolver remembers readable answers (CACHE_SECONDS). Every test asks afresh, so
    # one test's stubbed answer can never become another's.
    LR._CACHE.clear()
    yield stub
    LR._CACHE.clear()
    assert not stub.blocked, stub.blocked


def said(result):
    return {e["said"]: e for e in result["location_resolution"]}


def locality(name, state):
    assert _GEO.locate(name, state), "premise: %s is a locality of %s" % (name, state)
    return name


# ------------------------------------------------------------------ what Groq is for

def test_misheard_and_misspelt_suburbs_become_the_localities_the_index_confirms(groq):
    groq.respond = answer([
        {"id": 1, "localities": [locality("Tarneit", "VIC")], "confidence": "high"},
        {"id": 2, "localities": [locality("Wyndham Vale", "VIC")], "confidence": "high"},
        {"id": 3, "localities": [locality("Craigieburn", "VIC")], "confidence": "medium"},
    ])
    out = LR.resolve(["Tarn eat", "Wyndum Vale", "Cragieburn"], "VIC", _GEO)
    assert out["suburbs"] == ["Tarneit", "Wyndham Vale", "Craigieburn"]
    assert [e["method"] for e in out["location_resolution"]] == ["groq"] * 3
    assert said(out)["Tarn eat"]["used"] == ["Tarneit"]
    assert out["state"] == "", "the brief had a state; nothing is inferred"

    assert len(groq.calls) == 1, "ONE call for every unresolved phrase together"
    call = groq.calls[0]
    assert [p["said"] for p in call["phrases"]] == ["Tarn eat", "Wyndum Vale", "Cragieburn"]
    assert call["body"]["temperature"] == 0
    assert call["body"]["model"] == LR.MODEL
    assert call["body"]["max_completion_tokens"] >= 1000, "room for a reasoning model"
    assert call["timeout"] is not None and call["timeout"] <= 10
    assert "JSON only" in call["body"]["messages"][0]["content"]
    assert "unknown" in call["body"]["messages"][0]["content"]
    assert "VIC" in call["body"]["messages"][1]["content"]


def test_an_area_phrase_becomes_several_validated_localities(groq):
    west = [locality(n, "VIC") for n in ("Tarneit", "Truganina", "Werribee",
                                         "Hoppers Crossing", "Point Cook")]
    groq.respond = answer([{"id": 1, "localities": west, "confidence": "medium"}])
    out = LR.resolve(["western suburbs of Melbourne"], "VIC", _GEO)
    assert out["suburbs"] == west
    entry = said(out)["western suburbs of Melbourne"]
    assert entry["method"] == "groq" and entry["used"] == west
    assert "5 localities in VIC" in entry["note"]


def test_a_made_up_place_and_a_real_place_in_another_state_are_both_dropped(groq):
    assert not _GEO.states_for_suburb("Tarneit Heights", spoken=True), "premise: not a place"
    assert _GEO.states_for_suburb("Springfield Lakes") == ["QLD"], "premise: QLD only"
    groq.respond = answer([{"id": 1, "localities": ["Tarneit Heights", "Springfield Lakes"],
                            "confidence": "high"}])
    out = LR.resolve(["Tarn eat"], "VIC", _GEO)
    assert out["suburbs"] == ["Tarn eat"], "nothing validated: searched exactly as said"
    entry = said(out)["Tarn eat"]
    assert entry["method"] == "unresolved" and entry["used"] == ["Tarn eat"]
    assert "none of the 2 name(s)" in entry["note"]

    LR._CACHE.clear()                     # a second answer to the same phrase, not the first
    groq.respond = answer([{"id": 1, "localities": ["Tarneit Heights", "Springfield Lakes",
                                                    "Tarneit", "tarneit", "TARNEIT"],
                            "confidence": "high"}])
    out = LR.resolve(["Tarn eat"], "VIC", _GEO)
    assert out["suburbs"] == ["Tarneit"], "only the real VIC locality, once"
    assert "4 other name(s)" in said(out)["Tarn eat"]["note"]


def test_a_name_that_already_resolves_is_never_sent_or_replaced(groq):
    groq.respond = answer([{"id": 1, "localities": ["Truganina"], "confidence": "high"}])
    out = LR.resolve(["Tarneit", "Pointcook", "Tarn eat"], "VIC", _GEO)
    assert [p["said"] for p in groq.calls[0]["phrases"]] == ["Tarn eat"], \
        "Tarneit is exact and Pointcook folds to Point Cook; neither is sent"
    by = said(out)
    assert by["Tarneit"] == {"said": "Tarneit", "used": ["Tarneit"], "method": "exact",
                             "note": "a locality in VIC"}
    assert by["Pointcook"]["used"] == ["Point Cook"] and by["Pointcook"]["method"] == "spoken"
    assert by["Tarn eat"]["used"] == ["Truganina"]
    assert out["suburbs"][:2] == ["Tarneit", "Point Cook"]


def test_every_phrase_resolved_means_no_call(groq):
    out = LR.resolve(["Tarneit", "Pointcook", "Wyndham-Vale"], "VIC", _GEO)
    assert groq.calls == []
    assert out["suburbs"] == ["Tarneit", "Point Cook", "Wyndham Vale"]
    assert [e["method"] for e in out["location_resolution"]] == ["exact", "spoken", "spoken"]


def test_no_key_means_no_call(groq, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    out = LR.resolve(["Tarn eat"], "VIC", _GEO)
    assert groq.calls == []
    assert out["suburbs"] == ["Tarn eat"]
    entry = said(out)["Tarn eat"]
    assert entry["method"] == "unresolved" and "GROQ_API_KEY is not set" in entry["note"]


# ------------------------------------------------------------------ when Groq fails

@pytest.mark.parametrize("failure", [
    TimeoutError("timed out"),
    socket.timeout("timed out"),
    urllib.error.URLError(socket.timeout("timed out")),
    urllib.error.HTTPError(LR.GROQ_URL, 500, "Internal Server Error", {}, None),
    urllib.error.HTTPError(LR.GROQ_URL, 401, "Invalid API Key " + KEY, {}, None),
    answer(content="Sure! Tarn eat is probably Tarneit."),
    answer(content='{"results": "Tarneit"}'),
    answer(content='{"results": [{"id": 1, "localities": ["Tarn'),
    answer(content=""),
    b"<html>502 Bad Gateway</html>",
    b'{"choices": []}',
    # Review, 15 Sep 2026: json.loads raises RecursionError on this, and it escaped resolve().
    answer(content='{"results": ' + "[" * 100000 + "]" * 100000 + "}"),
], ids=["timeout", "socket-timeout", "urlerror-timeout", "http-500", "http-401",
        "prose", "results-not-a-list", "truncated-json", "empty-content", "html", "no-choices",
        "deeply-nested-json"])
def test_a_groq_failure_leaves_the_brief_exactly_as_the_fold_left_it(groq, monkeypatch,
                                                                     failure, capsys):
    phrases = ["Tarneit", "Red Bank Plains", "Tarn eat", "tarneit"]
    monkeypatch.delenv("GROQ_API_KEY")
    today = LR.resolve(phrases, "QLD", _GEO)
    monkeypatch.setenv("GROQ_API_KEY", KEY)

    groq.respond = failure
    out = LR.resolve(phrases, "QLD", _GEO)
    assert len(groq.calls) == 1
    assert out["suburbs"] == today["suburbs"]
    assert out["suburbs"] == ["Tarneit", "Redbank Plains", "Tarn eat"], \
        "what api/research.py's respell produced before the resolver existed"
    assert [e["method"] for e in out["location_resolution"]] == \
        ["unresolved", "spoken", "unresolved", "unresolved"]
    assert KEY not in json.dumps(out), "the key never reaches a response"
    if "timed out" in str(getattr(failure, "reason", failure)):
        assert "did not answer within" in said(out)["Tarn eat"]["note"]
    assert said(out)["Tarn eat"]["note"].startswith("not a locality in QLD; searched as said (")
    log = capsys.readouterr().err
    # Tarneit and tarneit are VIC localities, so on a QLD brief they are not sent: only
    # Tarn eat is (see test_a_real_locality_of_another_state_is_not_sent_to_groq).
    assert "[location_resolver] groq phrases=1 " in log
    assert "Tarn eat" not in log and KEY not in log, "counts only in the log"


@pytest.mark.parametrize("confidence", ["low", "unknown", "", None])
def test_a_reading_groq_is_not_sure_of_changes_nothing(groq, confidence):
    groq.respond = answer([{"id": 1, "localities": ["Tarneit"], "confidence": confidence}])
    out = LR.resolve(["Tarn eat"], "VIC", _GEO)
    assert out["suburbs"] == ["Tarn eat"]
    assert said(out)["Tarn eat"]["method"] == "unresolved"


def test_an_answer_in_a_code_fence_matched_by_the_words_is_still_read(groq):
    groq.respond = answer(content='```json\n{"results": [{"said": "tarn EAT", '
                                  '"localities": ["Tarneit"], "confidence": "high"}]}\n```')
    assert LR.resolve(["Tarn eat"], "VIC", _GEO)["suburbs"] == ["Tarneit"]


@pytest.mark.parametrize("given", ["1", " 1 ", 1.0])
def test_an_id_written_as_text_or_a_float_is_still_that_phrase(given):
    """Review, 15 Sep 2026: "1" and 1.0 were discarded, and the note blamed Groq's reading."""
    text = json.dumps({"results": [{"id": given, "localities": ["Tarneit"],
                                    "confidence": "high"}]})
    assert LR._parse(text, ["Tarn eat"]) == {1: (["Tarneit"], "high")}


@pytest.mark.parametrize("given", [True, 0, 2, "2", 1.5, "1.5", "one", None])
def test_an_id_naming_no_phrase_asked_is_reported_as_unmatched(given):
    text = json.dumps({"results": [{"id": given, "localities": ["Tarneit"],
                                    "confidence": "high"}]})
    with pytest.raises(LR._Unmatched):
        LR._parse(text, ["Tarn eat"])


def test_an_answer_that_matches_no_phrase_says_so_rather_than_blaming_the_reading(groq):
    groq.respond = answer([{"id": 7, "localities": ["Tarneit"], "confidence": "high"}])
    entry = said(LR.resolve(["Tarn eat"], "VIC", _GEO))["Tarn eat"]
    assert entry["method"] == "unresolved"
    assert "Groq's answer did not match the phrases asked" in entry["note"]
    assert "reading was" not in entry["note"]

    groq.respond = answer([{"id": 1, "localities": ["Tarneit"], "confidence": "high"}])
    out = LR.resolve(["Tarn eat", "Wyndum Vale"], "VIC", _GEO)
    assert said(out)["Tarn eat"]["used"] == ["Tarneit"]
    assert "Groq's answer did not include it" in said(out)["Wyndum Vale"]["note"]


def test_a_slow_groq_is_given_up_on_at_the_deadline_not_at_each_read(groq, monkeypatch):
    """Review, 15 Sep 2026: headers after 5 s, then the body 4 s a chunk -- every socket
    operation inside urlopen's timeout, and resolve() held for 21 s. Scaled down here: each
    step is under TIMEOUT, all of them together are well over it."""
    monkeypatch.setattr(LR, "TIMEOUT", 0.5)

    def trickle(_body):
        for _ in range(6):
            time.sleep(0.3)
        return answer([{"id": 1, "localities": ["Tarneit"], "confidence": "high"}])

    groq.respond = trickle
    started = time.monotonic()
    out = LR.resolve(["Tarn eat"], "VIC", _GEO)
    took = time.monotonic() - started
    assert took < LR.TIMEOUT + 0.6, "gave up after %.2fs, not at the deadline" % took
    assert out["suburbs"] == ["Tarn eat"], "unchanged: as if Groq had never been asked"
    entry = said(out)["Tarn eat"]
    assert entry["method"] == "unresolved" and "did not answer within 0.5s" in entry["note"]
    assert groq.calls[0]["timeout"] <= LR.TIMEOUT
    assert LR._CACHE == {}, "nothing that arrives after the deadline is remembered"


# ------------------------------------------------------------------ asked once

def test_the_same_phrases_twice_are_one_call(groq, capsys):
    """The hook sends one brief at 20 km, then at 50 km: one Groq call, one reading."""
    groq.respond = answer([{"id": 1, "localities": ["Tarneit"], "confidence": "high"}])
    first = LR.resolve(["Tarn eat"], "VIC", _GEO)
    second = LR.resolve(["Tarn eat"], "VIC", _GEO)
    assert len(groq.calls) == 1
    assert first == second and second["suburbs"] == ["Tarneit"]
    assert "cached=1" in capsys.readouterr().err

    LR.resolve(["Tarn eat"], "NSW", _GEO)
    assert len(groq.calls) == 2, "another state is another question"


def test_a_failed_call_is_not_remembered(groq):
    groq.respond = urllib.error.HTTPError(LR.GROQ_URL, 500, "boom", {}, None)
    assert LR.resolve(["Tarn eat"], "VIC", _GEO)["suburbs"] == ["Tarn eat"]
    groq.respond = answer([{"id": 1, "localities": ["Tarneit"], "confidence": "high"}])
    assert LR.resolve(["Tarn eat"], "VIC", _GEO)["suburbs"] == ["Tarneit"]
    assert len(groq.calls) == 2


def test_a_remembered_answer_expires(groq, monkeypatch):
    monkeypatch.setattr(LR, "CACHE_SECONDS", 0)
    groq.respond = answer([{"id": 1, "localities": ["Tarneit"], "confidence": "high"}])
    LR.resolve(["Tarn eat"], "VIC", _GEO)
    LR.resolve(["Tarn eat"], "VIC", _GEO)
    assert len(groq.calls) == 2


# ------------------------------------------------------------------ the limits

def test_the_search_is_capped_and_a_misheard_suburb_is_not_crowded_out(groq):
    exact = [locality(n, "VIC") for n in ("Tarneit", "Werribee", "Point Cook", "Melton",
                                          "Sunbury")]
    area = [locality(n, "VIC") for n in ("Truganina", "Hoppers Crossing", "Wyndham Vale",
                                         "Manor Lakes", "Williams Landing", "Laverton",
                                         "Altona Meadows", "Sunshine")]
    groq.respond = answer([
        {"id": 1, "localities": area, "confidence": "medium"},
        {"id": 2, "localities": [locality("Craigieburn", "VIC")], "confidence": "high"},
    ])
    out = LR.resolve(exact + ["western suburbs", "Cragieburn"], "VIC", _GEO)
    assert len(out["suburbs"]) == LR.MAX_PRIMARY_SUBURBS == 12
    assert out["suburbs"][:5] == exact, "a phrase that resolved is never displaced"
    assert "Craigieburn" in out["suburbs"], "the misheard suburb still gets its one place"
    by = said(out)
    assert by["western suburbs"]["used"] == area[:6]
    assert "2 more left out" in by["western suburbs"]["note"]


def test_a_locality_already_in_the_search_is_not_added_twice(groq):
    groq.respond = answer([{"id": 1, "localities": ["Tarneit"], "confidence": "high"}])
    out = LR.resolve(["Tarneit", "Tarn eat"], "VIC", _GEO)
    assert out["suburbs"] == ["Tarneit"]
    entry = said(out)["Tarn eat"]
    assert entry["method"] == "groq" and "already in the search: Tarneit" in entry["note"]


def test_groq_cannot_readmit_a_name_the_spoken_fold_refused(groq):
    """Newpark is the estate at Melonba (NSW). The fold refuses New Park, 604 km away, on
    purpose (geo.know_estates) -- so the model answering "New Park" must not undo that."""
    index = SuburbGeoIndex()
    index.know_estates([("Newpark", "Melonba", "NSW")])
    locality("Melonba", "NSW")
    groq.respond = answer([{"id": 1, "localities": ["New Park", "Melonba"],
                            "confidence": "high"}])
    out = LR.resolve(["Newpark"], "NSW", index)
    assert out["suburbs"] == ["Melonba"]
    assert said(out)["Newpark"]["used"] == ["Melonba"]


# ------------------------------------------------------------------ one reading, not any real place
# Review, 15 Sep 2026: validation only asked "is this a locality somewhere in the state?".

def test_names_far_from_the_place_the_phrase_names_are_dropped(groq):
    far = [locality(n, "QLD") for n in ("Cairns", "Mount Isa", "Weipa", "Birdsville",
                                        "Thursday Island")]
    groq.respond = answer([{"id": 1, "localities": [locality("Ipswich", "QLD")] + far,
                            "confidence": "medium"}])
    out = LR.resolve(["near Ipswich"], "QLD", _GEO)
    assert out["suburbs"] == ["Ipswich"]
    entry = said(out)["near Ipswich"]
    assert entry["method"] == "groq"
    assert "5 more than %d km from Ipswich dropped" % LR.MAX_KM_FROM_ANCHOR in entry["note"]


def test_a_place_named_in_the_phrase_outweighs_names_that_agree_with_each_other(groq):
    cairns = [locality(n, "QLD") for n in ("Cairns", "Earlville", "Edmonton")]
    groq.respond = answer([{"id": 1, "localities": cairns, "confidence": "high"}])
    out = LR.resolve(["near Ipswich"], "QLD", _GEO)
    assert out["suburbs"] == ["near Ipswich"], "searched as said: none is near Ipswich"
    entry = said(out)["near Ipswich"]
    assert entry["method"] == "unresolved"
    assert "3 more than %d km from Ipswich dropped" % LR.MAX_KM_FROM_ANCHOR in entry["note"]


def test_without_a_place_in_the_phrase_the_anchor_is_where_the_names_agree(groq):
    west = [locality(n, "VIC") for n in ("Tarneit", "Werribee", "Truganina")]
    groq.respond = answer([{"id": 1, "localities": [locality("Mildura", "VIC")] + west,
                            "confidence": "medium"}])
    out = LR.resolve(["western suburbs"], "VIC", _GEO)
    assert out["suburbs"] == west, "one far name listed first does not drag the rest"
    assert "1 more than %d km from Tarneit dropped" % LR.MAX_KM_FROM_ANCHOR \
        in said(out)["western suburbs"]["note"]


def test_one_misheard_suburb_is_one_search_origin(groq):
    towns = [locality(n, "VIC") for n in ("Mildura", "Irymple", "Red Cliffs", "Portland",
                                          "Swan Hill", "Wodonga", "Horsham")]
    groq.respond = answer([{"id": 1, "localities": towns + ["Tarneit"],
                            "confidence": "high"}])
    out = LR.resolve(["Tarn eat"], "VIC", _GEO)
    assert out["suburbs"] == ["Tarneit"], "the name that sounds like it, not the cluster"
    assert "kept to Tarneit alone" in said(out)["Tarn eat"]["note"]

    LR._CACHE.clear()
    near = [locality(n, "VIC") for n in ("Truganina", "Tarneit", "Werribee")]
    groq.respond = answer([{"id": 1, "localities": near, "confidence": "high"}])
    assert LR.resolve(["Tarn eat"], "VIC", _GEO)["suburbs"] == ["Tarneit"], \
        "neighbours inside the distance are still not what was said"


def test_a_council_name_that_sounds_like_no_one_suburb_keeps_its_area(groq):
    wyndham = [locality(n, "VIC") for n in ("Tarneit", "Werribee", "Point Cook",
                                            "Wyndham Vale")]
    groq.respond = answer([{"id": 1, "localities": wyndham, "confidence": "high"}])
    out = LR.resolve(["City of Wyndham"], "VIC", _GEO)
    assert out["suburbs"] == wyndham


def _newpark():
    index = SuburbGeoIndex()
    index.know_estates([("Newpark", "Melonba", "NSW")])
    return index


@pytest.mark.parametrize("phrase", ["Newpark estate", "Newpark Marsden Park",
                                    "the Newpark development"])
def test_an_estate_namesake_is_refused_with_more_words_around_it(groq, phrase):
    """Review, 15 Sep 2026: "Newpark estate" answered New Park was accepted, 604 km from
    the Melonba stock -- the incident know_estates exists for, one word further on."""
    groq.respond = answer([{"id": 1, "localities": [locality("New Park", "NSW")],
                            "confidence": "high"}])
    out = LR.resolve([phrase], "NSW", _newpark())
    assert out["suburbs"] == [phrase]
    entry = said(out)[phrase]
    assert entry["method"] == "unresolved"
    assert "estate the stock files elsewhere" in entry["note"]


def test_an_estate_the_stock_knows_keeps_only_where_its_stock_is_filed(groq):
    locality("Marsden Park", "NSW")
    groq.respond = answer([{"id": 1, "localities": ["Marsden Park"], "confidence": "high"}])
    out = LR.resolve(["Newpark estate"], "NSW", _newpark())
    assert out["suburbs"] == ["Newpark estate"], "Marsden Park is near, and still not it"
    assert "not where the stock files the Newpark estate (Melonba)" in \
        said(out)["Newpark estate"]["note"]

    LR._CACHE.clear()
    groq.respond = answer([{"id": 1, "localities": ["New Park", "Marsden Park", "Melonba"],
                            "confidence": "high"}])
    out = LR.resolve(["Newpark estate"], "NSW", _newpark())
    assert out["suburbs"] == ["Melonba"]
    assert said(out)["Newpark estate"]["method"] == "groq"


@pytest.mark.parametrize("phrase,state,home", [
    ("Springfield Lakes", "VIC", "QLD"),
    ("Redbank Plains", "NSW", "QLD"),
    ("Red Bank Plains", "VIC", "QLD"),
])
def test_a_real_locality_of_another_state_is_not_sent_to_groq(groq, phrase, state, home):
    """Review, 15 Sep 2026: "Springfield Lakes" on a VIC brief came back as Springvale, a
    suburb the caller never named, searched silently."""
    assert _GEO.states_for_suburb(phrase, spoken=True) == [home], "premise"
    groq.respond = answer([{"id": 1, "localities": [locality("Springvale", "VIC")],
                            "confidence": "medium"}])
    out = LR.resolve([phrase], state, _GEO)
    assert groq.calls == []
    assert out["suburbs"] == [phrase]
    entry = said(out)[phrase]
    assert entry["method"] == "unresolved"
    assert "a locality of %s, not %s; the state may be wrong" % (home, state) in entry["note"]


# ------------------------------------------------------------------ the index helps, and stands in
# Live, 15 Sep 2026, real Groq (gpt-oss-20b, reasoning_effort low) through POST /api/research:
# "Wyndum Vale" came back unknown, "Cragieburn" was left out of the answer, and "western
# suburbs of Melbourne" included Essendon, Coburg and Brunswick. The small model at low effort
# is weak at correcting a spelling on its own, so the index now offers it the closest
# spellings, stands in when it still does not say, and checks which way an area lies.

def _phrases_sent(body):
    return json.loads(body["messages"][1]["content"].split("Phrases:\n", 1)[1])["phrases"]


def _picks_the_first_candidate(body):
    return answer([{"id": p["id"], "localities": p["candidates"][:1], "confidence": "high"}
                   for p in _phrases_sent(body) if p.get("candidates")])


UNKNOWN = answer([{"id": 1, "localities": [], "confidence": "unknown"}])


def test_a_misspelt_suburb_is_sent_with_its_closest_spellings_and_groq_can_choose_one(groq):
    groq.respond = _picks_the_first_candidate
    out = LR.resolve(["Wyndum Vale", "Cragieburn", "western suburbs of Melbourne"], "VIC", _GEO)
    sent = {p["said"]: p for p in groq.calls[0]["phrases"]}
    assert sent["Wyndum Vale"]["candidates"][0] == "Wyndham Vale"
    assert sent["Cragieburn"]["candidates"][0] == "Craigieburn"
    for p in sent.values():
        assert len(p.get("candidates", [])) <= LR.CANDIDATE_COUNT
        assert all(_GEO.locate(n, "VIC") for n in p.get("candidates", [])), \
            "only real localities of the brief's state are offered"
    assert "candidates" not in sent["western suburbs of Melbourne"], "an area is not a spelling"
    system = groq.calls[0]["body"]["messages"][0]["content"]
    assert "candidates" in system and "misspelt" in system and "'unknown'" in system

    by = said(out)
    assert by["Wyndum Vale"]["method"] == "groq" and by["Wyndum Vale"]["used"] == ["Wyndham Vale"]
    assert by["Cragieburn"]["method"] == "groq" and by["Cragieburn"]["used"] == ["Craigieburn"]
    assert by["western suburbs of Melbourne"]["method"] == "unresolved"
    assert len(groq.calls) == 1, "still one call for every phrase"


def test_a_candidate_is_never_a_name_the_answer_could_not_keep(groq, monkeypatch):
    """The phrase's own spoken key was refused by the fold on purpose, and an estate's name
    filed elsewhere is not that place: offering either would only invite a dropped answer."""
    groq.respond = UNKNOWN
    index = _newpark()
    LR.resolve(["Newpark Estate"], "NSW", index)
    assert "New Park" not in groq.calls[0]["phrases"][0].get("candidates", [])

    # The list itself, with the one-clear-spelling rule out of the way -- otherwise nothing
    # is offered for this phrase at all and the line above proves nothing.
    ranked = LR._ranked(index, LR._phrase_key("Newpark Estate"), "NSW")
    assert ranked[0][1] == "New Park", "premise: the refused name is the closest spelling"
    monkeypatch.setattr(LR, "FUZZY_MARGIN", 0.0)
    monkeypatch.setattr(LR, "_closest_other", lambda *_a: 0.0)
    offered = LR._candidates(index, "Newpark Estate", "NSW", ranked)
    assert offered and "New Park" not in offered


def test_when_groq_does_not_settle_a_misspelling_its_one_close_locality_is_searched(groq):
    # Groq says unknown for the first and leaves the second out altogether -- both live.
    groq.respond = UNKNOWN
    out = LR.resolve(["Wyndum Vale", "Cragieburn"], "VIC", _GEO)
    assert out["suburbs"] == ["Wyndham Vale", "Craigieburn"]
    by = said(out)
    assert by["Wyndum Vale"]["method"] == "fuzzy" and by["Wyndum Vale"]["used"] == ["Wyndham Vale"]
    assert "similarity 0.86; the next closest, Wando Vale, 0.74" in by["Wyndum Vale"]["note"]
    assert "Groq did not settle it (Groq's reading was unknown)" in by["Wyndum Vale"]["note"]
    assert by["Cragieburn"]["method"] == "fuzzy" and by["Cragieburn"]["used"] == ["Craigieburn"]
    assert "similarity 0.95" in by["Cragieburn"]["note"]
    assert "Groq's answer did not include it" in by["Cragieburn"]["note"]
    assert "searched as said" not in by["Cragieburn"]["note"]


@pytest.mark.parametrize("failure", [
    urllib.error.HTTPError(LR.GROQ_URL, 500, "Internal Server Error", {}, None),
    TimeoutError("timed out"),
    answer(content="Sure! That is Craigieburn."),
    "no key",
], ids=["http-500", "timeout", "prose", "no-key"])
def test_the_backstop_does_not_need_groq(groq, monkeypatch, failure):
    if failure == "no key":
        monkeypatch.delenv("GROQ_API_KEY")
    else:
        groq.respond = failure
    out = LR.resolve(["Cragieburn", "Packing ham"], "VIC", _GEO)
    by = said(out)
    assert by["Cragieburn"]["method"] == "fuzzy" and out["suburbs"][0] == "Craigieburn"
    # A WEAK CANDIDATE IS NOT A READING. Pakenham is the closest VIC spelling of "Packing
    # ham", and at 0.78 it is a guess -- searched as said, exactly as before.
    ranked = LR._ranked(_GEO, LR._phrase_key("Packing ham"), "VIC")
    assert ranked and ranked[0][1] == "Pakenham" and ranked[0][0] < LR.FUZZY_RATIO, "premise"
    assert by["Packing ham"]["method"] == "unresolved"
    assert by["Packing ham"]["used"] == ["Packing ham"]
    assert by["Packing ham"]["note"].startswith("not a locality in VIC; searched as said (")
    assert len(groq.calls) == (0 if failure == "no key" else 1)


def test_a_real_locality_of_the_state_never_reaches_the_backstop(groq):
    groq.respond = UNKNOWN
    # Each is one letter from another VIC locality, and each is spelt as itself.
    names = [locality(n, "VIC") for n in ("Aberfeldie", "Aberfeldy", "Addington", "Eddington",
                                          "Armadale", "Taradale", "Ascot Vale", "Pascoe Vale")]
    out = LR.resolve(names, "VIC", _GEO)
    assert [e["method"] for e in out["location_resolution"]] == ["exact"] * len(names)
    assert out["suburbs"] == names and groq.calls == []


def test_the_backstop_thresholds_are_the_measured_ones():
    """Measured 15 Sep 2026 over every locality in data/au_suburbs.csv, with the production
    _ranked and _fuzzy (a replica of the decision swept the grid; the production function
    then agreed with it on every one of the 87,546 cases). Re-measured after review with the
    production functions, postal names (_POSTAL) out of the ranking and "the"/"of" refused:

        17,296 localities, each said as itself and left out of the ranking:
            0 read as a different locality (773 at 0.86 / 0.06 without the crowd rule)
        69,026 misspellings reaching the backstop (drop, double, swap, vowel):
            50,881 recovered (73.7%), 7 read as another locality (0.01%)
            (51,182 and 7 without the "the"/"of" rule)
        140,296 lookups of every real name in every state through resolve():
            0 reached the backstop (first measurement)

    Change a value here and those numbers no longer describe the code: measure again.
    """
    assert (LR.FUZZY_RATIO, LR.FUZZY_MARGIN, LR.FUZZY_MIN_KEY, LR.FUZZY_CROWD_SLACK) == \
        (0.85, 0.08, 6, 0.0)
    # The ceiling on the ratio: live, "Wyndum Vale" is this far from Wyndham Vale.
    best = LR._ranked(_GEO, LR._phrase_key("Wyndum Vale"), "VIC")[0]
    assert best[1] == "Wyndham Vale" and LR.FUZZY_RATIO <= best[0] < 0.86


# Real localities one letter from another of their state. Measured 15 Sep 2026: with ratio
# and margin alone, each of these -- said as itself, and left out of the ranking as if the
# index did not know it -- was read as its twin. Nothing tells a misspelling of one from the
# other, so a locality with a twin that close is never the backstop's pick (FUZZY_CROWD_SLACK).
REAL_TWINS = [("VIC", "Bradford", "Broadford"), ("VIC", "Bolinda", "Olinda"),
              ("QLD", "Alexandra", "Alexandria"), ("NSW", "Black Creek", "Back Creek"),
              ("NSW", "Barellan", "Narellan"), ("NSW", "Allambie", "Yallambie"),
              ("NSW", "Boggabri", "Noggabri"), ("NSW", "Bradfield", "Braefield"),
              ("NSW", "Bedgerabong", "Bedgerebong"), ("NSW", "Arncliffe", "Warnecliffe")]


@pytest.mark.parametrize("state,name,twin", REAL_TWINS)
def test_a_real_locality_missing_from_the_ranking_is_never_read_as_its_twin(state, name, twin):
    locality(name, state), locality(twin, state)
    ranked = [r for r in LR._ranked(_GEO, LR._phrase_key(name), state) if r[1] != name]
    assert ranked[0][1] == twin, "premise: the twin is the closest spelling left"
    second = ranked[1][0] if len(ranked) > 1 else 0.0
    assert ranked[0][0] >= LR.FUZZY_RATIO and ranked[0][0] - second >= LR.FUZZY_MARGIN, \
        "premise: ratio and margin alone would take the twin"
    got, why = LR._fuzzy(_GEO, name, state, ranked)
    assert got == ""
    assert f"its closest spelling in {state}, {twin} (" in why
    assert "is itself spelt almost like another locality there" in why


@pytest.mark.parametrize("phrase,first,second", [
    ("Aradale", "Armadale", "Taradale"),          # 0.93 and 0.93
    ("Asco Vale", "Ascot Vale", "Pascoe Vale"),   # 0.94 and 0.89
])
def test_two_localities_spelt_almost_as_closely_are_not_chosen_between(groq, phrase, first,
                                                                       second):
    groq.respond = UNKNOWN
    out = LR.resolve([phrase], "VIC", _GEO)
    entry = said(out)[phrase]
    assert entry["method"] == "unresolved" and out["suburbs"] == [phrase]
    assert f"its closest spellings in VIC, {first} (" in entry["note"]
    assert f"and {second} (" in entry["note"] and "too alike to choose between" in entry["note"]


@pytest.mark.parametrize("phrase", [
    "Cragieburn 3064",          # a digit: a postcode or a lot, not a spelling
    "Cragieburn estate",        # an estate is in a locality, it is not a spelling of one
    "near Cragieburn",          # an area
    "Cragieburn B",             # a one-letter word is a fragment
])
def test_only_a_phrase_spelt_like_one_name_is_read_by_the_backstop(groq, phrase):
    groq.respond = UNKNOWN
    out = LR.resolve([phrase], "VIC", _GEO)
    assert said(out)[phrase]["method"] == "unresolved", phrase


def test_a_region_said_with_the_is_not_read_as_the_locality_one_letter_away(groq, monkeypatch):
    """Review, 15 Sep 2026: "The Hills" -- Sydney's Hills District -- was read by spelling as
    The Hill, NSW, in Newcastle 117 km from Sydney, and the search went there."""
    locality("The Hill", "NSW")
    ranked = LR._ranked(_GEO, LR._phrase_key("The Hills"), "NSW")
    assert ranked[0][1] == "The Hill" and ranked[0][0] >= LR.FUZZY_RATIO and \
        ranked[0][0] - ranked[1][0] >= LR.FUZZY_MARGIN, "premise: spelling alone would take it"
    assert not LR._spelt_like_one_name("The Hills")

    groq.respond = _picks_the_first_candidate
    out = LR.resolve(["The Hills"], "NSW", _GEO)
    assert "candidates" not in groq.calls[0]["phrases"][0], "a region is not a spelling"
    entry = said(out)["The Hills"]
    assert entry["method"] == "unresolved" and out["suburbs"] == ["The Hills"]

    monkeypatch.delenv("GROQ_API_KEY")
    out = LR.resolve(["The Hills"], "NSW", _GEO)
    assert said(out)["The Hills"]["method"] == "unresolved" and out["suburbs"] == ["The Hills"]


@pytest.mark.parametrize("phrase,state,postal", [
    ("Gold Coast", "QLD", "Gold Coast Mc"),
    ("Sunshine Coast", "QLD", "Sunshine Coast Mc"),
    ("Central Coast", "NSW", "Central Coast Mc"),
    ("Gippsland", "VIC", "Gippsland Mc"),       # one postal point, 221 km from Melbourne
])
def test_a_mail_centre_is_never_the_reading_of_a_region(groq, phrase, state, postal):
    """Review, 15 Sep 2026: each region was read as its mail centre, a name no stock row
    carries, and the same mail centre led the candidates sent to Groq."""
    locality(postal, state)
    groq.respond = _picks_the_first_candidate
    out = LR.resolve([phrase], state, _GEO)
    offered = groq.calls[0]["phrases"][0].get("candidates", [])
    assert not any(LR._POSTAL.search(n) for n in offered), offered
    entry = said(out)[phrase]
    assert postal not in entry["used"] and entry["method"] != "fuzzy"
    ranked = LR._ranked(_GEO, LR._phrase_key(phrase), state)
    assert not any(LR._POSTAL.search(name) for _s, name, _k in ranked)
    # Said exactly, it is still what it is.
    assert said(LR.resolve([postal], state, _GEO))[postal]["method"] == "exact"


@pytest.mark.parametrize("phrase,meant", [("Ballart", "Ballarat"), ("Bendgo", "Bendigo")])
def test_a_mail_centre_no_longer_hides_its_towns_misspellings_from_the_backstop(groq, phrase,
                                                                              meant):
    """The builder's open issue, 15 Sep 2026: Ballarat Mc and Bendigo Dc were twins of their
    towns under the crowd rule, so no misspelling of either was ever read."""
    assert any(LR._POSTAL.search(n) and n.startswith(meant + " ")
               for n in _GEO.localities("VIC")), "premise: the town has a postal twin"
    groq.respond = UNKNOWN
    out = LR.resolve([phrase], "VIC", _GEO)
    entry = said(out)[phrase]
    assert entry["method"] == "fuzzy" and entry["used"] == [meant]


# Review, 15 Sep 2026, with a model that answers with the first candidate: each of these was
# kept as method "groq" although the backstop refuses it -- a near tie (Aradale: Armadale and
# Taradale, 0.93 each), a twin (Narellan and Barellan), or a region or estate whose closest
# spelling is somewhere else (Yarra Valley as Tarra Valley in Gippsland, Cloverton as Laverton,
# Everleigh as Riverleigh). "Tarn eat" is here too: Tarneit is only 0.04 clear of Tarwin East,
# and live, without candidates, Groq read it right.
@pytest.mark.parametrize("phrase,state", [
    ("Aradale", "VIC"), ("Asco Vale", "VIC"), ("Bradfor", "VIC"), ("Olindah", "VIC"),
    ("Narelan", "NSW"), ("Yarra Valley", "VIC"), ("Cloverton", "VIC"), ("Everleigh", "QLD"),
    ("Tarn eat", "VIC"),
])
def test_no_candidates_when_the_closest_spelling_is_not_one_clear_name(groq, phrase, state):
    assert LR._ranked(_GEO, LR._phrase_key(phrase), state), "premise: there were spellings"
    groq.respond = _picks_the_first_candidate
    out = LR.resolve([phrase], state, _GEO)
    assert "candidates" not in groq.calls[0]["phrases"][0], phrase
    entry = said(out)[phrase]
    assert entry["method"] == "unresolved" and out["suburbs"] == [phrase]


def test_no_state_means_no_candidates_and_no_backstop(groq):
    groq.respond = UNKNOWN
    out = LR.resolve(["Cragieburn"], "", _GEO)
    assert "candidates" not in groq.calls[0]["phrases"][0]
    assert said(out)["Cragieburn"]["method"] == "unresolved"
    assert out["suburbs"] == ["Cragieburn"] and out["state"] == ""


def test_the_backstop_is_held_to_the_search_cap(groq):
    full = [locality(n, "VIC") for n in ("Tarneit", "Werribee", "Point Cook", "Melton",
                                         "Sunbury", "Truganina", "Hoppers Crossing",
                                         "Manor Lakes", "Williams Landing", "Laverton",
                                         "Altona Meadows", "Sunshine")]
    out = LR.resolve(full + ["Cragieburn", "Wyndum Vale"], "VIC", _GEO)
    assert groq.calls == [], "the brief already names 12 suburbs"
    assert out["suburbs"] == full + ["Cragieburn", "Wyndum Vale"]
    assert [e["method"] for e in out["location_resolution"][-2:]] == ["unresolved"] * 2

    out = LR.resolve(full + ["Tarnet"], "VIC", _GEO)
    ranked = LR._ranked(_GEO, "tarnet", "VIC")
    assert ranked[0][1] == "Tarneit", "premise"
    if LR._fuzzy(_GEO, "Tarnet", "VIC", ranked)[0]:
        entry = said(out)["Tarnet"]
        assert entry["method"] == "fuzzy" and entry["used"] == ["Tarneit"]
        assert "already in the search" in entry["note"], "named, not added"
        assert out["suburbs"] == full


def test_a_failure_while_ranking_spellings_costs_nothing_else(groq, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("index exploded")

    monkeypatch.setattr(LR, "_ranked", boom)
    groq.respond = answer([{"id": 1, "localities": ["Craigieburn"], "confidence": "high"}])
    out = LR.resolve(["Cragieburn"], "VIC", _GEO)
    assert len(groq.calls) == 1 and "candidates" not in groq.calls[0]["phrases"][0]
    assert out["suburbs"] == ["Craigieburn"] and said(out)["Cragieburn"]["method"] == "groq"

    monkeypatch.undo()
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    monkeypatch.setattr(LR, "_fuzzy", boom)
    LR._CACHE.clear()
    groq.respond = UNKNOWN
    out = LR.resolve(["Cragieburn"], "VIC", _GEO)
    assert said(out)["Cragieburn"]["method"] == "unresolved"
    assert out["suburbs"] == ["Cragieburn"]


# What Groq returned live for "western suburbs of Melbourne", in its order.
LIVE_WEST = ["Footscray", "Sunshine", "Williamstown", "Altona", "Maribyrnong", "Essendon",
             "Coburg", "Brunswick"]


def test_western_suburbs_of_melbourne_keeps_only_the_names_that_lie_west(groq):
    for n in LIVE_WEST:
        locality(n, "VIC")
    groq.respond = answer([{"id": 1, "localities": LIVE_WEST, "confidence": "medium"}])
    out = LR.resolve(["western suburbs of Melbourne"], "VIC", _GEO)
    # Coburg lies at 2 degrees from the Melbourne locality and Brunswick at 355: due north.
    # Essendon, at 329, is 59 degrees off due west -- north-west, and inside the 67.5.
    assert out["suburbs"] == [n for n in LIVE_WEST if n not in ("Coburg", "Brunswick")]
    entry = said(out)["western suburbs of Melbourne"]
    assert entry["method"] == "groq"
    assert ("2 dropped as not west of Melbourne (Coburg, Brunswick: more than 67.5 degrees "
            "off due west)") in entry["note"]


def test_a_compound_direction_is_a_sector_too(groq):
    names = [locality(n, "VIC") for n in ("Dandenong", "Berwick", "Frankston", "Werribee",
                                          "Craigieburn")]
    groq.respond = answer([{"id": 1, "localities": names, "confidence": "medium"}])
    out = LR.resolve(["south-eastern suburbs of Melbourne"], "VIC", _GEO)
    assert out["suburbs"] == ["Dandenong", "Berwick", "Frankston"]
    assert "2 dropped as not south-east of Melbourne (Werribee, Craigieburn" in \
        said(out)["south-eastern suburbs of Melbourne"]["note"]


@pytest.mark.parametrize("phrase", [
    "western suburbs",                    # a direction with no place named: no centre to judge from
    "suburbs near North Melbourne",       # "North" is part of a name, not a direction
    "north and south of Melbourne",       # two that cancel
    "inner suburbs of Melbourne",         # how far, not which way
])
def test_no_direction_gate_without_a_direction_from_a_named_place(groq, phrase):
    groq.respond = answer([{"id": 1, "localities": LIVE_WEST, "confidence": "medium"}])
    out = LR.resolve([phrase], "VIC", _GEO)
    assert out["suburbs"] == LIVE_WEST, phrase
    assert "dropped as not" not in said(out)[phrase]["note"]


# Review, 15 Sep 2026: the gate measured from any place the phrase named, so a suburb given as
# an example inside the area became its centre, and the area's other suburbs were dropped.
@pytest.mark.parametrize("phrase,names", [
    ("western suburbs around Werribee", ["Werribee", "Tarneit", "Hoppers Crossing", "Point Cook",
                                         "Wyndham Vale", "Truganina"]),
    ("northern suburbs like Craigieburn", ["Craigieburn", "Mickleham", "Kalkallo",
                                           "Roxburgh Park", "Greenvale", "Epping"]),
    ("western suburbs near Sunshine", ["Sunshine", "Footscray", "Braybrook", "St Albans",
                                       "Deer Park", "Albion", "Ardeer"]),
])
def test_a_suburb_given_as_an_example_is_not_where_a_direction_is_measured_from(groq, phrase,
                                                                              names):
    for n in names:
        locality(n, "VIC")
    groq.respond = answer([{"id": 1, "localities": names, "confidence": "medium"}])
    out = LR.resolve([phrase], "VIC", _GEO)
    assert out["suburbs"] == names, phrase
    assert "dropped as not" not in said(out)[phrase]["note"]
    assert LR._direction_origin(_GEO, phrase, "VIC") == ""


def test_a_suburb_with_a_compass_word_in_its_name_is_the_place_named(groq):
    """Review, 15 Sep 2026: "Clyde North" was skipped for holding "North", Clyde became the
    place named, and the caller's own suburb was dropped as not south-east of Clyde."""
    names = [locality(n, "VIC") for n in ("Clyde North", "Clyde", "Cranbourne East", "Berwick",
                                          "Officer")]
    phrase = "south east, Clyde North area"
    assert LR._named_in_phrase(_GEO, phrase, "VIC") == "Clyde North"
    groq.respond = answer([{"id": 1, "localities": names, "confidence": "medium"}])
    out = LR.resolve([phrase], "VIC", _GEO)
    assert out["suburbs"] == names
    assert "dropped as not" not in said(out)[phrase]["note"]

    LR._CACHE.clear()
    phrase = "suburbs south-east of Clyde North"
    assert LR._direction_origin(_GEO, phrase, "VIC") == "Clyde North"
    out = LR.resolve([phrase], "VIC", _GEO)
    assert "Clyde North" in said(out)[phrase]["used"], "measured from Clyde North, it stays"


@pytest.mark.parametrize("phrase,state,expected", [
    ("western suburbs of Melbourne", "VIC", "Melbourne"),
    ("north of Brisbane", "QLD", "Brisbane"),
    ("North West of Sydney", "NSW", "Sydney"),
    ("south-eastern suburbs of Melbourne", "VIC", "Melbourne"),
    ("west of North Melbourne", "VIC", "North Melbourne"),
    ("western Sydney", "NSW", ""),                  # no "of": the anchor still applies
    ("western suburbs around Werribee", "VIC", ""),
    ("near West Footscray", "VIC", ""),
])
def test_where_a_direction_is_measured_from(phrase, state, expected):
    assert LR._direction_origin(_GEO, phrase, state) == expected


@pytest.mark.parametrize("phrase,state,expected", [
    ("western suburbs of Melbourne", "VIC", (270, "west")),
    ("north of Brisbane", "QLD", (0, "north")),
    ("south-east of Melbourne", "VIC", (135, "south-east")),
    ("southeastern suburbs", "VIC", (135, "south-east")),
    ("North West of Sydney", "NSW", (315, "north-west")),
    ("near West Footscray", "VIC", None),
    ("near Eastern Heights", "QLD", None),
    ("near Ipswich", "QLD", None),
    ("Wyndum Vale", "VIC", None),
])
def test_the_direction_a_phrase_says(phrase, state, expected):
    got = LR._heading(_GEO, phrase, state)
    assert (None if got is None else (round(got[0]) % 360, got[1])) == expected


# ------------------------------------------------------------------ no state in the brief

def test_no_state_is_taken_only_from_a_locality_of_one_state(groq):
    assert _GEO.states_for_suburb("Tarneit") == ["VIC"], "premise"
    groq.respond = answer([{"id": 1, "localities": ["Tarneit"], "confidence": "high"}])
    out = LR.resolve(["Tarn eat"], "", _GEO)
    assert out["state"] == "VIC" and out["suburbs"] == ["Tarneit"]
    assert "no state was given" in said(out)["Tarn eat"]["note"]


def test_no_state_and_a_name_in_several_states_infers_nothing(groq):
    assert len(_GEO.states_for_suburb("Springfield")) > 1, "premise"
    groq.respond = answer([{"id": 1, "localities": ["Springfield"], "confidence": "high"}])
    out = LR.resolve(["Spring feeled"], "", _GEO)
    assert out["state"] == "" and out["suburbs"] == ["Spring feeled"]
    assert said(out)["Spring feeled"]["method"] == "unresolved"


def test_no_state_and_answers_in_two_states_infers_nothing(groq):
    groq.respond = answer([
        {"id": 1, "localities": ["Tarneit"], "confidence": "high"},
        {"id": 2, "localities": ["Springfield Lakes"], "confidence": "high"},
    ])
    out = LR.resolve(["Tarn eat", "Spring field lakes estate"], "", _GEO)
    assert out["state"] == ""
    assert out["suburbs"] == ["Tarn eat", "Spring field lakes estate"]
    assert "do not share one" in said(out)["Tarn eat"]["note"]


# ------------------------------------------------------------------ the endpoint, offline

def _snapshot():
    path = os.environ.get("SPB_SNAPSHOT_JSON") or os.path.join(APP, "vercel_site",
                                                               "stock.json")
    if not os.path.exists(path):
        pytest.skip("no local stock.json snapshot")
    return path


def _research(monkeypatch, name, radius=10, state="VIC", budget=5000000):
    monkeypatch.setenv("SPB_SNAPSHOT_JSON", _snapshot())
    import research
    return research.run_research({"client_brief": {
        "client_name": "T", "state": state, "primary_suburbs": [name],
        "budget_max": budget, "search_radius_km": radius}})


def test_research_searches_tarneit_for_tarn_eat(groq, monkeypatch):
    groq.respond = answer([{"id": 1, "localities": ["Tarneit"], "confidence": "high"}])
    said_it = _research(monkeypatch, "Tarn eat")
    assert len(groq.calls) == 1

    monkeypatch.delenv("GROQ_API_KEY")
    spelt = _research(monkeypatch, "Tarneit")
    assert len(groq.calls) == 1, "an exact name never reaches Groq"
    assert len(spelt["search_area"]) > 1, "premise: 10 km around Tarneit is an area"
    assert said_it["search_area"] == spelt["search_area"]
    assert said_it["shortlist_count"] == spelt["shortlist_count"]
    assert said_it["location_resolution"] == [
        {"said": "Tarn eat", "used": ["Tarneit"], "method": "groq",
         "note": said_it["location_resolution"][0]["note"]}]
    assert said_it["location_resolution"][0]["note"]
    assert spelt["location_resolution"][0]["method"] == "exact"


def test_research_with_nothing_found_still_reports_the_location(groq, monkeypatch):
    groq.respond = answer([{"id": 1, "localities": ["Tarneit"], "confidence": "high"}])
    out = _research(monkeypatch, "Tarn eat", budget=1)
    assert out["shortlist_count"] == 0 and out["shortlist"] == [], "premise: the empty path"
    assert out["location_resolution"][0]["method"] == "groq"
    assert out["search_area"][0]["origin"] == "Tarneit"


def test_research_survives_a_groq_failure_exactly_as_before(groq, monkeypatch):
    groq.respond = urllib.error.HTTPError(LR.GROQ_URL, 500, "boom", {}, None)
    failed = _research(monkeypatch, "Tarn eat")
    monkeypatch.delenv("GROQ_API_KEY")
    before = _research(monkeypatch, "Tarn eat")
    assert failed["status"] == "success"
    assert failed["search_area"] == before["search_area"]
    assert [a["suburb"] for a in failed["search_area"]] == ["Tarn Eat"], \
        "an area of one labelled as said: visibly a place we could not find"
    assert failed["location_resolution"][0]["method"] == "unresolved"
    assert "Groq returned 500" in failed["location_resolution"][0]["note"]


def test_research_searches_wyndham_vale_for_wyndum_vale_when_groq_does_not_know(groq,
                                                                              monkeypatch):
    """Live, 15 Sep 2026: Groq answered "Wyndum Vale" unknown and the search ran as said."""
    groq.respond = answer([{"id": 1, "localities": [], "confidence": "unknown"}])
    misspelt = _research(monkeypatch, "Wyndum Vale")
    monkeypatch.delenv("GROQ_API_KEY")
    spelt = _research(monkeypatch, "Wyndham Vale")
    assert len(spelt["search_area"]) > 1, "premise: 10 km around Wyndham Vale is an area"
    assert misspelt["search_area"] == spelt["search_area"]
    assert misspelt["shortlist_count"] == spelt["shortlist_count"]
    entry = misspelt["location_resolution"][0]
    assert entry["method"] == "fuzzy" and entry["used"] == ["Wyndham Vale"]


def test_research_reports_a_deeply_nested_answer_instead_of_losing_the_record(groq,
                                                                             monkeypatch):
    """Review, 15 Sep 2026: RecursionError escaped resolve(), so research.py's backstop
    returned location_resolution [] and the hook had nothing to show."""
    groq.respond = answer(content='{"results": ' + "[" * 100000 + "]" * 100000 + "}")
    out = _research(monkeypatch, "Tarn eat")
    assert out["location_resolution"] and \
        out["location_resolution"][0]["method"] == "unresolved"
    assert "not the JSON asked for" in out["location_resolution"][0]["note"]
