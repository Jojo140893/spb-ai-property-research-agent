"""
Tests for per-listing links in stocklists.

Coleen: "if someone wants to click, they can click here and see the PDF."

Today every row from a stocklist file shares that file's URL, so the export has no
per-lot link at all. The link exists in the source — as an XLSX cell hyperlink
(often on a bare "Download" cell, which is why the text alone was useless) or as a
PDF page annotation. These tests build both file types and assert the link reaches
the listing, and lands in the right column.
"""

import io

import openpyxl

from sources.spreadsheet_extract import (_annot_links, _classify_link,
                                         _link_fields, _links_in_band,
                                         extract_from_xlsx)

FLYER = "https://e-agent.com.au/_files/ugd/lot82-package.pdf"
PLAN = "https://e-agent.com.au/_files/ugd/lot82-floorplan.pdf"
# Row shape taken from the live "VIC Regional" stocklist.
_ROW = ["Lot 82 Aberdeen", 282, 142.8, 12.0, "Sep-26", "Empley 15", "3x2x2",
        "$205,000", "$335,220", "$540,220", "Available"]


def _workbook(hyperlink=True) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Aberdeen - Winter Valley - VIC - House & Land"])
    ws.append(_ROW + ["Download", "Floorplan"])
    if hyperlink:
        ws.cell(row=2, column=12).hyperlink = FLYER
        ws.cell(row=2, column=13).hyperlink = PLAN
    else:                                   # the =HYPERLINK() form, whose target is
        ws.cell(row=2, column=12).value = f'=HYPERLINK("{FLYER}","Download")'
        ws.cell(row=2, column=13).value = f'=HYPERLINK("{PLAN}","Floorplan")'
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_cell_hyperlink_reaches_the_listing():
    rows = extract_from_xlsx(_workbook(), source_label="stock.xlsx")
    assert len(rows) == 1, f"expected 1 listing, got {len(rows)}"
    r = rows[0]
    assert r["listing_url"] == FLYER, f"per-lot link lost: {r.get('listing_url')}"
    assert r["floorplan_url"] == PLAN, f"floorplan misfiled: {r.get('floorplan_url')}"
    # and the row still parses as it did before
    assert r["advertised_package_price"] == 540_220
    assert r["lot_address"] == "Lot 82, Aberdeen"


def test_hyperlink_formula_target_is_recovered():
    """data_only=True returns the cached text of a formula, discarding its URL."""
    rows = extract_from_xlsx(_workbook(hyperlink=False), source_label="stock.xlsx")
    assert len(rows) == 1
    assert rows[0]["listing_url"] == FLYER
    assert rows[0]["floorplan_url"] == PLAN


def test_rows_without_links_are_unaffected():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(_ROW)
    buf = io.BytesIO()
    wb.save(buf)
    rows = extract_from_xlsx(buf.getvalue(), source_label="stock.xlsx")
    assert len(rows) == 1
    assert rows[0].get("listing_url") is None
    assert rows[0]["advertised_package_price"] == 540_220


def test_link_kind_is_taken_from_anchor_or_url():
    assert _classify_link("https://x/a.pdf", "Download") == "listing_url"
    assert _classify_link("https://x/lot-9-floorplan.pdf", "") == "floorplan_url"
    assert _classify_link("https://x/a.pdf", "Floor Plan") == "floorplan_url"
    assert _classify_link("https://x/EstateBrochure.pdf", "") == "brochure_url"
    assert _classify_link("https://x/marketing-flyer.pdf", "") == "brochure_url"
    # first of each kind wins; a second generic link does not overwrite the first
    got = _link_fields([("Download", "https://x/1.pdf"), ("More", "https://x/2.pdf"),
                        ("Plan", "https://x/lot9-floorplan.pdf")])
    assert got == {"listing_url": "https://x/1.pdf",
                   "floorplan_url": "https://x/lot9-floorplan.pdf"}, got


def test_pdf_annotations_are_matched_to_their_row_by_position():
    """A PDF stocklist's links are page annotations with no row of their own — they
    are only usable if matched to the row they sit on."""
    class _Page:
        annots = [
            {"uri": FLYER, "top": 100.0, "bottom": 110.0, "x0": 400.0},
            {"uri": PLAN, "top": 101.0, "bottom": 109.0, "x0": 480.0},
            {"uri": "https://x/other.pdf", "top": 300.0, "bottom": 310.0, "x0": 400.0},
            {"uri": None, "top": 100.0, "bottom": 110.0, "x0": 10.0},      # not a link
            {"uri": FLYER, "top": None, "bottom": None, "x0": 10.0},       # no position
        ]
    annots = _annot_links(_Page())
    assert len(annots) == 3, f"annotation parsing wrong: {annots}"
    on_row = _links_in_band(annots, 98.0, 112.0)
    assert [u for _, u in on_row] == [FLYER, PLAN], on_row   # left to right
    assert _links_in_band(annots, 200.0, 220.0) == []        # nothing on an empty band
    assert _link_fields(on_row) == {"listing_url": FLYER, "floorplan_url": PLAN}


def test_address_column_is_a_label_not_the_whole_row():
    """The bug Coleen pointed at on screen. `parse_fields` sets lot_address to the
    whole line containing "Lot N", which for a flattened stocklist row is the entire
    row — so the stocklist path must override it."""
    from sources.spreadsheet_extract import _address_label
    from sources.adaptive_extract import parse_fields
    from sources.feature_extract import parse_listing_features

    def label(text, ctx=""):
        f = parse_fields(text)
        f.update({k: v for k, v in parse_listing_features(
            text, ctx, f.get("advertised_package_price")).items() if v is not None})
        return _address_label(text, f, ctx)

    assert label(" ".join(str(c) for c in _ROW),
                 "Aberdeen - Winter Valley - VIC - House & Land") == "Lot 82, Aberdeen"
    # a real street address must win over the lot number and survive intact
    assert label("Available 25/03/2026 105 Almond Street $890,000",
                 "Denman - Highfields Estate") == "105 Almond Street, Highfields"
    # stock codes are already labels; don't prefix them with "Lot"
    assert label("CC-0114 506 Titled 200 5 + 5 + 3 MORETON $ 839,000 $ 694,199 "
                 "$ 1,533,199") == "CC-0114"
    # nothing recognisable -> None, so the caller keeps whatever it already had
    assert label("Package from $650,000 enquire now") is None

    # Rows from the per-builder crawl. Several builders' files ARE just an address, so
    # composing a label from parts would only throw half of it away.
    assert label("Lot 444, Hillcrest $1,353,596") == "Lot 444, Hillcrest"
    assert label("4 Windsor Street, BRISBANE NORTH $980,780") == "4 Windsor Street, BRISBANE NORTH"
    assert label("520 Stony Drive, Alluvium $680,294") == "520 Stony Drive, Alluvium"
    assert label("1368 Margery St, Toolern Waters , Melton South 3338 "
                 "$596,000") == "1368 Margery St, Toolern Waters"
    # a bare leading number is the lot on several stocklists ("103 Samara Estate ...")
    assert label("103 Samara Estate Fraser Rise Available Bristol 15 $350,000 $335,970 "
                 "$685,970") == "Lot 103, Samara"
    # ...but Gallery Group's sheet title is not a locality
    assert label("100 Park Royal Crescent, GALLERY STOCK LIST 2026 "
                 "$1,328,810") == "100 Park Royal Crescent"
    # a grid of figures is not an address, even when it is short
    assert label("12 Brittlewood 300 26 CTM 5 Yes 3 2 N/A Double $1,430,000") == "Lot 12"


def run_all():
    tests = [
        ("address column is a label", test_address_column_is_a_label_not_the_whole_row),
        ("xlsx cell hyperlink survives", test_cell_hyperlink_reaches_the_listing),
        ("=HYPERLINK() target recovered", test_hyperlink_formula_target_is_recovered),
        ("rows without links unaffected", test_rows_without_links_are_unaffected),
        ("link kind from anchor or url", test_link_kind_is_taken_from_anchor_or_url),
        ("pdf annots matched by position", test_pdf_annotations_are_matched_to_their_row_by_position),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] links: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] links: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
