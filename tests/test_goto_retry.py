"""A portal that is slow once must not cost the whole channel.

The scheduled harvest of 20 Aug 2026 read NOTHING from Proxima — 3,922 lots left to go
stale, and a red run whose advice was to re-authenticate a session that was working — 
because the first page.goto of the run hit a single 30s timeout. A manual re-run three
hours later read all 3,922 back.
"""
import time
import pytest

from sources.scraper_base import PlaywrightScraper


class _Page:
    """Just enough page to exercise goto()."""

    def __init__(self, fail_times, exc=None):
        self.fail_times = fail_times
        self.exc = exc or TimeoutError("Page.goto: Timeout 30000ms exceeded.")
        self.calls = []
        self.slept = []

    def goto(self, url, wait_until=None, timeout=None):
        self.calls.append((url, timeout))
        if len(self.calls) <= self.fail_times:
            raise self.exc
        return None

    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_timeout(self, ms):
        self.slept.append(ms)


def _scraper(page):
    s = PlaywrightScraper.__new__(PlaywrightScraper)
    s.page = page
    s.rate_limit_seconds = 0
    return s


def test_a_single_timeout_is_retried_and_succeeds():
    page = _Page(fail_times=1)
    _scraper(page).goto("https://portal.proxima.com.au/agent/projects/index/")
    assert len(page.calls) == 2, "one timeout should be retried, not fatal"


def test_the_retry_budget_grows_so_a_slow_portal_can_finish():
    page = _Page(fail_times=2)
    _scraper(page).goto("https://example.test/")
    budgets = [t for _, t in page.calls]
    assert budgets == sorted(budgets) and budgets[0] < budgets[-1], (
        "a portal mid-deploy needs more than the 30s default, not the same 30s again")


def test_a_portal_that_is_genuinely_down_still_fails():
    page = _Page(fail_times=99)
    with pytest.raises(Exception):
        _scraper(page).goto("https://example.test/")
    assert len(page.calls) == PlaywrightScraper._GOTO_ATTEMPTS, (
        "retrying forever would hang the nightly run; a dead portal must still go red")


def test_it_waits_between_attempts_rather_than_hammering():
    page = _Page(fail_times=2)
    _scraper(page).goto("https://example.test/")
    assert page.slept and all(ms > 0 for ms in page.slept)
