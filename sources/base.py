"""
Abstract PropertySource Base Interface.
All search channels (E-Agent, Builder Portals, Drive Ingest, Proxima, REA/Domain) inherit from this interface.
"""

from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime
from typing import List, Dict, Any, Optional


# ONE READ DATE PER RUN (23 Oct 2026). date_checked is the DAY a row was read, and every
# channel stamped it per ROW, from datetime.now() at the moment that row was built. A
# harvest dispatched by hand at 23:04 Melbourne read Proxima from 23:40 to 00:00:44, so
# the last six projects -- 288 lots -- carried 24/10 and the other 8,075 carried 23/10.
# One whole read became two days, and three things downstream believed the split:
#   - the Settings tab counts "Refreshed" on a channel's newest day, and called a read of
#     8,363 lots from 109 of 109 projects 3%, "ONLY PART OF ITS STOCK";
#   - the search ages rows on a fixed UTC+10 clock, so until 14:00 UTC the 24/10 rows were
#     a day in the future, and every one of them went to Needs confirming;
#   - parity, run at 13:46 UTC on the same clock, left all 288 out of its obvious set and
#     said nothing -- NSW fell by 250 lots and no bar moved.
#
# The harvest runs E-Agent, its Stocklist, the portals, Proxima and the inbox one after
# another in ONE process, and all five stamped this way. So a run that crosses midnight
# splits a channel inside itself AND splits the channels from each other: that night the
# inbox, swept after Proxima, was all 24/10 while E-Agent was all 23/10.
#
# So harvest_buildings opens one_read_date() before the first channel reads, and every row
# read inside it carries the day the run STARTED. The moment is not lost: checked_at
# (database.record_building) still records each row to the second. Outside a harvest -- a
# diagnostic script reading one portal, say -- read_date() is today's date, exactly what
# datetime.now() gave before, so nothing that is not a run can be stuck on an old day.
_DAY = "%d/%m/%Y"
_RUN_DATE: Optional[str] = None


def read_date() -> str:
    """The date a row read now carries ("%d/%m/%Y", as date_checked has always been
    written): the run's date inside one_read_date(), today's outside it."""
    return _RUN_DATE or datetime.now().strftime(_DAY)


@contextmanager
def one_read_date():
    """Every row read inside this block carries the date the block opened on.

    A block opened inside another keeps the OUTER date, so a harvest that is itself run
    from inside a longer job stamps that job's day; the date is put back on the way out,
    so a process that runs two harvests (the tests do) never carries one into the next."""
    global _RUN_DATE
    outer = _RUN_DATE
    _RUN_DATE = outer or datetime.now().strftime(_DAY)
    try:
        yield _RUN_DATE
    finally:
        _RUN_DATE = outer


class PropertySource(ABC):
    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Name of the property source channel."""
        pass

    @abstractmethod
    def search(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Executes property search based on client brief filters.
        Returns a list of raw candidate package dictionaries.
        """
        pass

    @abstractmethod
    def verify(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """
        Verifies live listing availability & price accuracy.
        Returns verification result status.
        """
        pass
