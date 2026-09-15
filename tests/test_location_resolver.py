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


def test_research_reports_a_deeply_nested_answer_instead_of_losing_the_record(groq,
                                                                             monkeypatch):
    """Review, 15 Sep 2026: RecursionError escaped resolve(), so research.py's backstop
    returned location_resolution [] and the hook had nothing to show."""
    groq.respond = answer(content='{"results": ' + "[" * 100000 + "]" * 100000 + "}")
    out = _research(monkeypatch, "Tarn eat")
    assert out["location_resolution"] and \
        out["location_resolution"][0]["method"] == "unresolved"
    assert "not the JSON asked for" in out["location_resolution"][0]["note"]
