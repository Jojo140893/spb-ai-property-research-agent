"""Run the retirement tests in LEDGER MODE (24 Oct 2026).

repair_rows.main judges retirement through list_ledger. tests/test_withdrawn_from_source.py
calls find_withdrawn_from_source and partial_reads without a ledger, which is the rule as
it stood on 24 Oct 2026 -- so on its own it guards the old path. This plugin makes every
such call go through the ledger with an EMPTY history (no recorded reads, no manifest),
which is what the nightly does on the ledger's first night, so the same unchanged tests
guard the live path:

    python -m pytest -p tests.ledger_mode_plugin tests/test_withdrawn_from_source.py
        tests/test_eagent_stocklist_pipeline.py     (one command line)

And to show the plugin really routes them through the ledger's verdicts, force every
verdict WHOLE and one whole read enough -- exactly the three safety tests must fail
(test_a_list_that_came_back_missing_most_of_itself_retires_nothing,
test_the_half_read_lists_are_reported_not_just_skipped,
test_a_portal_returning_a_fraction_still_retires_nothing):

    SPB_LEDGER_PLUGIN_FORCE_WHOLE=1 python -m pytest -p tests.ledger_mode_plugin ...

Not collected by pytest (no test_ prefix); loaded only with -p.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _retirement_in_ledger_mode(monkeypatch):
    import list_ledger
    import repair_rows as RR
    find, partial = RR.find_withdrawn_from_source, RR.partial_reads

    def find_in_ledger_mode(rows, today=None, ledger=None, verdicts=None):
        if ledger is None and verdicts is None:
            ledger = list_ledger.Ledger.empty(today)
        return find(rows, today, ledger=ledger, verdicts=verdicts)

    def partial_in_ledger_mode(rows, today=None, ledger=None, verdicts=None):
        if ledger is None and verdicts is None:
            ledger = list_ledger.Ledger.empty(today)
        return partial(rows, today, ledger=ledger, verdicts=verdicts)

    monkeypatch.setattr(RR, "find_withdrawn_from_source", find_in_ledger_mode)
    monkeypatch.setattr(RR, "partial_reads", partial_in_ledger_mode)
    if os.environ.get("SPB_LEDGER_PLUGIN_FORCE_WHOLE") == "1":
        judge = list_ledger.Ledger.judge

        def forced(self, key, members, newest, *a, **k):
            v = judge(self, key, members, newest, *a, **k)
            v.verdict, v.basis, v.vetoed = list_ledger.WHOLE, "forced whole", False
            return v
        monkeypatch.setattr(list_ledger.Ledger, "judge", forced)
        monkeypatch.setattr(list_ledger, "MIN_WHOLE_READS_ABSENT", 1)
