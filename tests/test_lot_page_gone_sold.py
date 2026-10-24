"""A lot whose own page is gone is SOLD, everywhere the checks count lots (24 Oct 2026).

Ahsan, 24 Oct 2026: "if they are gone mark them sold". link_check.py finds a lot's own page
gone (the site's own 404/410, or the page's own Sold / Under offer) and build_web.py writes
that into the snapshot as lot_page_gone; the search already leaves such a lot out. But the
nightly parity check and the replay still picked it as an OBVIOUS lot and counted it hidden
"for an allowed reason" -- nine SA lots sold through Kenekt over four nights put SA over the
3% ceiling (9 of 124) and turned the run red. Now both read it as they read a stated Sold:
not obvious at all.

Only a page that is definitively gone counts. A site that answers 404 for most of the pages
that were fine the night before has moved or gone down; link_check holds those back as
unknown (classify, HOST_FLOOR / HOST_GONE_SHARE), so none of its lots is sold.
"""
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_web  # noqa: E402
import config  # noqa: E402
import link_check as LC  # noqa: E402
import parity_check as P  # noqa: E402
import search_regressions as SR  # noqa: E402
import _parity_fixture as F  # noqa: E402

TONIGHT = "24/10/2026"
TODAY = SR._day(TONIGHT)


def _sa_lot(n, url):
    """An SA lot read tonight, stated Available, its own page at `url`."""
    return F.lot(300 + n, state="SA", estate="Parity Vale", day=TONIGHT, listing_url=url)


def _catalogue(rows, gone):
    """A Catalogue as the checks build it: the snapshot rows carry lot_page_gone from
    `gone` ({url: (since, why)}, build_web._lot_pages_gone's shape); the DATABASE rows carry
    no such column, as in production."""
    snapshot, db = [], []
    for r in rows:
        hit = gone.get(r["listing_url"])
        snapshot.append(dict(r, lot_page_gone=hit[0] if hit else "",
                             lot_page_status=hit[1] if hit else ""))
        db.append({k: v for k, v in r.items() if k not in ("lot_page_gone", "lot_page_status")})
    return SR.Catalogue(snapshot, db_rows=db)


def _obvious_ids(catalogue):
    """The ids each check calls obvious: (parity_check.obvious_lots, the replay's
    obvious_candidates for SA at any price)."""
    parity = {lot["id"] for lot in P.obvious_lots(catalogue, TODAY, lambda rec: {},
                                                   lambda rec, row: False)}
    replay = {rec["id"] for rec in SR.obvious_candidates(catalogue, "SA", 0, 0, TODAY,
                                                         lambda rec: {})}
    return parity, replay


def test_one_gone_lot_page_is_sold_and_not_obvious():
    healthy = "https://ibportal.kenekt.app/property/BP-%d/protected/x"
    rows = [_sa_lot(n, healthy % (5000 + n)) for n in range(4)]
    gone = {healthy % 5000: ("22/10/2026", "gone"),
            healthy % 5001: ("23/10/2026", "Under offer")}
    parity, replay = _obvious_ids(_catalogue(rows, gone))
    want = {rows[2]["id"], rows[3]["id"]}
    assert parity == want, parity
    assert replay == want, replay
    assert SR.lot_page_says_sold({"lot_page_gone": "22/10/2026"})
    assert not SR.lot_page_says_sold({"lot_page_gone": ""})
    assert not SR.lot_page_says_sold(None)              # a row the snapshot does not hold


def test_a_site_that_404s_all_at_once_sells_nobody(tmp_path, monkeypatch):
    """Through link_check's own verdicts and build_web's own reader. One host moved its
    pages overnight: all twelve that opened last night answer 404 tonight, so link_check
    holds them back and none of those lots is sold. On the same night one page of twelve on
    a healthy host is gone, and that lot is."""
    moved = ["https://partners.example-builder.com.au/property/BP-%d/protected/x" % (6000 + n)
             for n in range(12)]
    healthy = ["https://ibportal.kenekt.app/property/BP-%d/protected/x" % (7000 + n)
               for n in range(12)]
    last_night = {u: {"status": 200, "verdict": "ok"} for u in moved + healthy}
    tonight = {u: 404 for u in moved}
    tonight.update({u: 200 for u in healthy})
    tonight[healthy[0]] = 404
    checked, held = LC.classify(tonight, last_night, TONIGHT)
    assert held and "partners.example-builder.com.au" in held, held
    (tmp_path / ".sessions").mkdir()
    (tmp_path / ".sessions" / "link_status.json").write_text(
        json.dumps({"date": TONIGHT, "urls": checked}), encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    gone, _date = build_web._lot_pages_gone()
    assert set(gone) == {healthy[0]}, gone

    rows = [_sa_lot(n, u) for n, u in enumerate(moved + healthy)]
    parity, replay = _obvious_ids(_catalogue(rows, gone))
    sold = {rows[len(moved)]["id"]}                      # healthy[0]'s lot
    everyone = {r["id"] for r in rows}
    assert parity == everyone - sold, sorted(everyone - parity)
    assert replay == everyone - sold, sorted(everyone - replay)
