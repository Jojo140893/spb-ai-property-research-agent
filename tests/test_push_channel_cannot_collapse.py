"""A source that delivers cannot "collapse", and treating it as if it can was fatal.

"digital email: read 9 this run, against 170 stored (expected at least 42)" failed the
harvest step every night from 27 to 31 August. The database was still pushed each time —
16,908 rows on the 31st — but the workflow gates "Publish to Vercel" on success(), so the
site went four days without being republished while the data underneath it was fresh.

The collapse test compares what a channel handed back against what it holds. That means
something for a channel which re-lists everything each run, and nothing for an inbox: a
builder emails a price list when they have one, and a quiet day is a normal day.

The same false positive was fixed on the Settings tab (build_web.CHANNEL_PUSH_SOURCES)
and this second copy of the reasoning was missed — which is the lesson worth keeping.
"""
import sys

sys.path.insert(0, "api")

import harvest_buildings as H


def test_a_quiet_inbox_is_not_a_collapse():
    read = {"digital email": 9, "E-Agent": 3900}
    stored = {"digital email": 170, "E-Agent": 5500}
    assert H._collapsed_channels(read, stored, previous={}) == []


def test_a_real_collapse_is_still_caught():
    """The failure this test exists for: a channel that re-lists and came back thin."""
    read = {"E-Agent": 200}
    stored = {"E-Agent": 5500}
    got = H._collapsed_channels(read, stored, previous={})
    assert got and got[0][0] == "E-Agent"


def test_an_inbox_that_goes_silent_is_still_caught():
    """Exempting it from the fraction must not exempt it from silence — that is a
    different test and it is untouched."""
    assert H._dead_channels({"digital email": 0}, {"digital email": 170})


def test_the_two_copies_of_this_rule_agree():
    """One list here, one in build_web. They describe the same fact about the world, and
    they drifted once already."""
    import build_web

    assert H.PUSH_CHANNELS == build_web.CHANNEL_PUSH_SOURCES, (
        "the harvest and the dashboard disagree about which channels push")
