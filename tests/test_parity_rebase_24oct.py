"""The parity READ bar re-bases an old Proxima counter on the project's own reads (24 Oct 2026).

The CARLING (Proxima project 762) has held a counter of 79 since 27 Sep, the last night it
was on Proxima's index. Every night since it has been read whole -- "70 lot(s) of 70" on
24 Oct -- and every night the bar failed it, because 70 is 9 under 79 and an old counter
excuses at most max(3, 10%). Its own reads were 72 on 8-12 Oct and 70 after the 12-22 Oct
outage: two lots sold, not a narrowed list. A project selling down is now a note, while a
sudden narrowing, tonight or on any night still in the history, fails as before.
"""
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import parity_check as P  # noqa: E402

KEY = "Proxima:pid:762"


def _carling(printed, whole="uncapped"):
    return {"channel": "Proxima", "kind": "project", "pid": "762",
            "name": "The CARLING Carlingford", "outcome": "read", "printed": printed,
            "stored": {"new": 0, "updated": 0, "unchanged": printed, "rejected": 0},
            "on_index": False, "via": "accordion", "header_counter": None, "stated": 79,
            "stated_source": "stored", "stated_on": "27/09/2026", "whole": whole,
            "replays": 0, "bands": 0, "short": False, "stale_counter": False}


def _night(day, lots, whole_reads=None):
    """A history entry as parity_check.history_entry writes it. whole_reads None: a night
    recorded before 24 Oct, which kept only the lots each entry stored."""
    n = {"night": "%02d/10/2026" % day, "states": {},
         "manifest": {KEY: lots} if lots else {}}
    if whole_reads is not None:
        n["proxima_whole"] = whole_reads
    return n


def _read(printed, history, night="24/10/2026", whole="uncapped"):
    return P.read_bar({"schema": 1, "generated": night + " 01:13",
                       "entries": [_carling(printed, whole)]},
                      history, night, required=True, facts={})


def _proxima_failures(failures):
    return [f for f in failures if f["group"] == "Proxima"]


# The history the 24 Oct run read: 72 on 10-11 Oct, the 12 Oct afternoon run and the
# 13-22 Oct nights with Proxima down (no read of 762), 70 on 23 Oct (its last run that day).
HISTORY_24OCT = ([_night(10, 72), _night(11, 72)] + [_night(d, 0) for d in range(12, 23)]
                 + [_night(23, 70)])


def test_the_carling_selling_down_is_a_note_not_a_failure():
    failures, summary, notes = _read(70, HISTORY_24OCT)
    assert _proxima_failures(failures) == [], failures
    said = " ".join(notes)
    assert "re-based" in said and "The CARLING Carlingford read 70" in said, notes
    assert "72 (10/10), 72 (11/10), 70 (23/10), 70 (tonight)" in said, notes
    assert summary["proxima_rebased"] == 1


def test_without_its_own_reads_the_old_counter_still_fails_it():
    """The 24 Oct failure exactly, when there is no history to re-base on."""
    failures, summary, _notes = _read(70, [])
    text = " ".join(f["text"] for f in _proxima_failures(failures))
    assert "The CARLING Carlingford read 70 of its 79 available" in text, failures
    assert "not re-based: no whole read on the night before" in text, text
    assert summary["proxima_rebased"] == 0


def test_a_sudden_narrowing_tonight_still_fails():
    """A session filter narrows the next harvest: 70 last night, 30 tonight."""
    failures, _s, _n = _read(30, HISTORY_24OCT)
    text = " ".join(f["text"] for f in _proxima_failures(failures))
    assert "read 30 of its 79 available" in text and "fell from 70 to 30 tonight" in text, text


def test_a_narrowing_that_persists_keeps_failing_while_it_is_in_the_history():
    """Three nights at 30 agree with each other, and still the drop from 70 on 21 Oct is
    in the history -- so it fails, rather than becoming the new baseline."""
    history = [_night(20, 70), _night(21, 70), _night(22, 30), _night(23, 30)]
    failures, _s, _n = _read(30, history)
    text = " ".join(f["text"] for f in _proxima_failures(failures))
    assert "fell from 70 to 30 on 22/10" in text, text


def test_one_read_is_never_its_own_evidence():
    """70 tonight, and the night before has no read of the project (an outage): one whole
    read against a counter it is 9 under, so it fails as before."""
    failures, _s, _n = _read(70, [_night(21, 72), _night(22, 0)], night="23/10/2026")
    text = " ".join(f["text"] for f in _proxima_failures(failures))
    assert "read 70 of its 79 available" in text and "no whole read on the night before" \
        in text, text


def test_a_slow_sell_down_over_two_weeks_is_re_based():
    """A lot a night for two weeks: 79 down to 65, each step within what lots leave a
    list by."""
    history = [_night(10 + i, 78 - i) for i in range(13)] + [_night(23, 66)]
    failures, summary, _n = _read(65, history)
    assert _proxima_failures(failures) == [], failures
    assert summary["proxima_rebased"] == 1


def test_a_read_of_nothing_is_never_re_based():
    failures, _s, _n = _read(0, HISTORY_24OCT)
    text = " ".join(f["text"] for f in _proxima_failures(failures))
    assert "nothing read, which an old counter never excuses" in text, text


def test_a_read_not_proven_whole_is_never_re_based():
    failures, _s, _n = _read(70, HISTORY_24OCT, whole="")
    assert _proxima_failures(failures), failures


def test_the_history_keeps_each_nights_whole_reads_and_only_those():
    """What the next nights re-base on. A night recorded since 24 Oct keeps whole reads
    only, so a read not proven whole on the night before is no evidence."""
    _f, summary, _n = _read(70, HISTORY_24OCT)
    entry = P.history_entry({"today": "24/10/2026", "groups": {}}, summary)
    assert entry["proxima_whole"] == {KEY: 70}
    _f, summary, _n = _read(70, HISTORY_24OCT, whole="")
    assert P.history_entry({"today": "24/10/2026", "groups": {}}, summary)[
        "proxima_whole"] == {}
    # A new-format night before with the project read but not whole.
    history = HISTORY_24OCT[:-1] + [_night(23, 70, whole_reads={})]
    failures, _s, _n = _read(70, history)
    assert _proxima_failures(failures), failures
    # ...and with it read whole, the same night re-bases.
    history = HISTORY_24OCT[:-1] + [_night(23, 70, whole_reads={KEY: 70})]
    failures, _s, _n = _read(70, history)
    assert _proxima_failures(failures) == [], failures
