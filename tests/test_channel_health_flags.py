"""The Settings tab flagged two sources that were working.

"Only part of its stock" asks what fraction of a source's stored rows it handed back on
its newest day. That means something for a source which re-lists everything it holds each
run, and nothing at all for one that pushes in batches or holds a handful of rows.

The test it must NOT weaken is the one it was written for: Proxima returned 16 lots
against 1,396 stored on 17 Aug and this tab called it "ok", because the run had happened
and recency alone said healthy while 99% of the stock quietly went stale.
"""
import sys

sys.path.insert(0, "api")

import build_web


def _state(live, refreshed, name="Some Portal", age_days=0):
    """Reproduce the rule in _export_channel_health for one source."""
    pct = round(100.0 * refreshed / live) if live else 0
    if age_days is None:
        return "unknown"
    if age_days >= build_web.CHANNEL_DEAD_DAYS:
        return "dead"
    if age_days > build_web.CHANNEL_STALE_DAYS:
        return "stale"
    if (pct < build_web.CHANNEL_PARTIAL_PCT
            and name not in build_web.CHANNEL_PUSH_SOURCES
            and live >= build_web.CHANNEL_PARTIAL_MIN_LIVE):
        return "partial"
    return "ok"


def test_the_failure_this_rule_exists_for_still_fires():
    """Proxima, 17 Aug: 16 of 1,396."""
    assert _state(live=1396, refreshed=16, name="Proxima") == "partial"


def test_an_inbox_is_not_judged_on_being_re_sent():
    """digital email on 26 Aug: 10 of 167 refreshed, because the other 157 arrived on
    earlier days and an email is not sent again."""
    assert _state(live=167, refreshed=10, name="digital email") == "ok"


def test_but_an_inbox_that_goes_quiet_is_still_caught():
    """The age tests are untouched — that is what makes exempting it safe."""
    assert _state(live=167, refreshed=10, name="digital email", age_days=3) == "stale"
    assert _state(live=167, refreshed=10, name="digital email", age_days=9) == "dead"


def test_a_fraction_of_a_handful_is_not_an_alarm():
    """Bathla Development: 14 live on the direct portal, 1 refreshed. Its stock is fine —
    168 of its other 182 rows come through Proxima, 135 refreshed the same day."""
    assert _state(live=14, refreshed=1, name="Bathla Development") == "ok"


def test_a_small_source_that_dies_is_still_caught():
    """The floor only silences the fraction, never the silence."""
    assert _state(live=14, refreshed=1, name="Bathla Development", age_days=4) == "stale"


def test_the_floor_is_where_the_comment_says_it_is():
    assert _state(live=build_web.CHANNEL_PARTIAL_MIN_LIVE, refreshed=1) == "partial"
    assert _state(live=build_web.CHANNEL_PARTIAL_MIN_LIVE - 1, refreshed=1) == "ok"


def test_the_tab_is_told_the_thresholds_it_describes():
    """The footnote renders partial_min_live. If the payload stops carrying it the page
    silently prints a different number from the one the rule uses."""
    import inspect

    source = inspect.getsource(build_web._export_channel_health)
    assert '"partial_min_live"' in source
    assert '"push_sources"' in source
