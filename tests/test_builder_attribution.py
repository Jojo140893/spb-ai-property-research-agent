"""Tests for naming the builder on E-Agent stock, and for not naming the wrong one.

The bug these guard: 229 rows in the client's database carried an ESTATE in
builder_name at attribution_scope='builder' — 'Emerald Grove - Jordan Springs',
'Kemps Estate - Austral', 'Leppington Rise - Leppington' and 'Bingara Gorge - Wilton'.

Two faults, both reproduced here:

  1. E-Agent's NSW House & Land page divides itself into a "Builders" half (five builder
     sections) and a "Projects" half (ten ESTATE sections). One heading up the two halves
     look identical, and the crawl read only the nearest heading, so every estate in the
     Projects half was filed as a builder.

  2. Eight of those ten sections link the SAME Creation Homes workbook, and Google spells
     that one tab two ways — `edit?gid=0#gid=0` and `edit?pli=1&gid=0#gid=0`. Keyed on the
     raw href, the seen-set let both through and stored the same 107 listings twice.

All offline: the DOM shapes are the ones read off the live page on 2026-07-30, and the
stocklist banners are the real first rows of the real files.
"""

from sources.e_agent import (_builder_from_banner, EAgentSource,
                             stocklist_file_key)
from sources.spreadsheet_extract import extract_stocklist


# The heading chain the live NSW page produces for each of its stocklist links, nearest
# first. Trimmed to the headings that matter; the real chains are longer.
_NSW_BUILDER_HALF = ["Bramwell Homes", "Hudson Homes", "Thomas Paul Constructions",
                     "G-Developments", "Eternal Homes", "Builders",
                     "Wholesale Agent Platform"]
_NSW_PROJECT_HALF = ["Kemps Estate - Austral", "Projects", "Bramwell Homes",
                     "Hudson Homes", "Eternal Homes", "Builders",
                     "Wholesale Agent Platform"]


def test_page_divider_separates_builders_from_estates():
    """The 'Projects' divider is what tells an estate from a builder."""
    assert EAgentSource._section_kind(_NSW_BUILDER_HALF) == "builder"
    # 'Projects' is nearer than 'Builders', and the nearest divider governs.
    assert EAgentSource._section_kind(_NSW_PROJECT_HALF) == "project"
    # Pages with no divider must behave exactly as before.
    assert EAgentSource._section_kind(["Taylors Run", "Wholesale Agent Platform"]) == ""
    assert EAgentSource._section_kind([]) == ""
    assert EAgentSource._section_kind(None) == ""
    # Matched whole: a builder whose name merely contains the word is not a divider.
    assert EAgentSource._section_kind(["Verv Projects", "Builders"]) == "builder"


def test_an_estate_heading_is_never_taken_for_a_builder():
    """The exact four labels that got into the client's database."""
    src = EAgentSource.__new__(EAgentSource)          # no credentials needed
    for estate in ("Kemps Estate - Austral", "Emerald Grove - Jordan Springs",
                   "Leppington Rise - Leppington", "Bingara Gorge - Wilton",
                   "Harvest Hill - Wyee", "Sapphire - Rouse Hill",
                   "Clarke Grounds - Rouse Hill", "Settlers Place - Werrington"):
        chain = [estate] + _NSW_PROJECT_HALF[1:]
        section = EAgentSource._section_kind(chain)
        is_builder = src._is_builder_heading(estate) and section != "project"
        assert not is_builder, f"{estate!r} would still be filed as a builder"
    # ...and the builder half of the SAME page is unaffected.
    for builder in ("Eternal Homes", "G-Developments", "Thomas Paul Constructions",
                    "Hudson Homes", "Bramwell Homes"):
        chain = [builder] + _NSW_BUILDER_HALF[1:]
        section = EAgentSource._section_kind(chain)
        assert src._is_builder_heading(builder) and section != "project", builder


def test_builder_is_read_from_the_stocklist_files_own_title_row():
    """The one place a multi-estate workbook names its builder."""
    assert _builder_from_banner("CREATION HOMES NSW STOCK LIST ") == "Creation Homes"
    assert _builder_from_banner(
        "THOMAS PAUL CONSTRUCTIONS MASTER PRICE LIST 15-6-26") == "Thomas Paul Constructions"
    assert _builder_from_banner("Gallery Group Price List VIC") == "Gallery Group"
    assert _builder_from_banner("Paramount Living Availability") == "Paramount Living"
    assert _builder_from_banner("Land Build Direct SA Pricelist") == "Land Build Direct"


def test_a_banner_that_names_no_builder_names_nobody():
    """A blank is the intended answer. A wrong builder is worse than no builder."""
    for banner in (
            # the Leppington Rise file: a street address, not a company
            "Agent Stock List 167 Ingleburn Road, Leppington",
            # the Bingara Gorge Dual Key file: names the estate and the product
            "Bingara Gorge - Dual Key",
            # estate banners inside the Creation Homes workbook
            "Harvest Hill | 1377 Hue Hue Road, Wyee",
            "Emerald Grove at Jordan Springs", "Kemps Estate - Austral",
            "LAND ONLY", "Stage 2", "Tranche 2", "SIINGLE CONTRACT",
            # page furniture, and generic words that are not a name on their own
            "Projects", "Available Stock", "Our Builders", "Live Packages",
            "New Homes", "New Stock", "Homes", "Group", "Living", "Townhouses",
            # a lot row that was mis-detected as a header must never name a builder
            "Available 2 N/A Single Dualkey - Modern 15.8 502 $519,000",
            "", "   ", None):
        assert _builder_from_banner(banner) == "", f"{banner!r} produced a builder"


def test_one_google_sheets_tab_counts_as_one_file():
    """The duplication that put the same 107 listings in the database twice."""
    base = "https://docs.google.com/spreadsheets/d/1__TJNDYYCLrD8MJkwcwdbkHEZUW5uBM7oPajgEpQRc8"
    plain = stocklist_file_key(f"{base}/edit?gid=0#gid=0")
    # `pli` is Google's account-picker index; it cannot change which file is served.
    assert stocklist_file_key(f"{base}/edit?pli=1&gid=0#gid=0") == plain
    assert stocklist_file_key(f"{base}/edit?usp=sharing&gid=0#gid=0") == plain
    # /edit and /export are two views of one document.
    assert stocklist_file_key(f"{base}/export?gid=0") == plain
    # A DIFFERENT TAB is a different file — two estates really can share a workbook.
    assert stocklist_file_key(f"{base}/edit?gid=17#gid=17") != plain
    # A different workbook is a different file.
    assert stocklist_file_key(f"{base}X/edit?gid=0#gid=0") != plain
    # Non-Sheets links are untouched apart from case/trailing-slash folding.
    pdf = "https://www.e-agent.com.au/_files/ugd/069fe0_a66133.pdf"
    assert stocklist_file_key(pdf) == pdf
    assert stocklist_file_key("") == ""


def test_the_seen_set_reads_one_workbook_once():
    """Eight Projects sections, two href spellings, one workbook -> one parse."""
    base = "https://docs.google.com/spreadsheets/d/1__TJ"
    hrefs = [f"{base}/edit?gid=0#gid=0",            # Kemps Estate - Austral
             f"{base}/edit?gid=0#gid=0",            # Harvest Hill - Wyee
             f"{base}/edit?gid=0#gid=0",            # Sapphire - Rouse Hill
             f"{base}/edit?pli=1&gid=0#gid=0",      # Emerald Grove - Jordan Springs
             f"{base}/edit?pli=1&gid=0#gid=0",      # Gundari - Rouse Hill
             f"{base}/edit?gid=9#gid=9"]            # a genuinely different tab
    seen, kept = set(), []
    for h in hrefs:
        key = stocklist_file_key(h)
        if key in seen:
            continue
        seen.add(key)
        kept.append(h)
    assert len(kept) == 2, f"expected the workbook once plus the other tab, got {kept}"


def test_a_project_scoped_file_that_names_its_builder_is_builder_scoped():
    """`project` scope makes the API blank builder_name, so a name the FILE gave us has
    to carry builder scope or it is thrown away."""
    rows = [{"source_banner": "CREATION HOMES NSW STOCK LIST", "builder_name": ""}
            for _ in range(3)]
    used = EAgentSource._name_from_banner(rows, builder_hint="", allow_banner=True)
    assert used == "Creation Homes"
    assert all(r["builder_name"] == "Creation Homes" for r in rows)
    assert all(r["builder_source"] == "e-agent stocklist banner" for r in rows)

    # A heading that already named the builder wins: the page is the better evidence.
    rows = [{"source_banner": "CREATION HOMES NSW STOCK LIST", "builder_name": "Hudson Homes"}]
    assert EAgentSource._name_from_banner(rows, "Hudson Homes", True) == ""
    assert rows[0]["builder_name"] == "Hudson Homes"

    # Apartment/townhouse/land/commercial pages do not promote a banner: the thing being
    # sold there really is a development, and re-scoping those rows would change identity.
    rows = [{"source_banner": "CREATION HOMES NSW STOCK LIST", "builder_name": ""}]
    assert EAgentSource._name_from_banner(rows, "", allow_banner=False) == ""
    assert rows[0]["builder_name"] == ""

    # Nothing to attribute is not an error.
    assert EAgentSource._name_from_banner([], "", True) == ""


def test_extractor_surfaces_the_files_title_row_on_every_row():
    """`source_banner` is what `_builder_from_banner` reads, so it has to survive the
    estate banners that overwrite the running context beneath it."""
    csv = (
        "CREATION HOMES NSW STOCK LIST,,,,,\n"
        "Harvest Hill | 1377 Hue Hue Road Wyee,,,,,\n"
        "Status,Lot,Frontage (m),Land Size (sqm),House Size (sqm),Package Price\n"
        "Available,2,15.8,502,245.16,\"$1,251,800\"\n"
        "Available,3,15.8,503,252.9,\"$1,255,000\"\n"
        "Birling | 975 The Northern Road Bringelly,,,,,\n"
        "Available,7,12.5,375,180.84,\"$1,054,000\"\n"
    ).encode("utf-8")
    rows = extract_stocklist(csv, source_label="test.csv", builder_hint="")
    assert len(rows) == 3, f"expected 3 listings, got {len(rows)}"
    # Every row keeps the DOCUMENT title, including rows under the second estate banner.
    assert all(r["source_banner"] == "CREATION HOMES NSW STOCK LIST" for r in rows), \
        [r.get("source_banner") for r in rows]
    # ...while estate_name still follows the estate banner the row sits under, so the
    # document title has not displaced the per-estate context.
    assert rows[0]["estate_name"].startswith("Harvest Hill"), rows[0]["estate_name"]
    assert rows[1]["estate_name"].startswith("Harvest Hill"), rows[1]["estate_name"]
    assert rows[2]["estate_name"].startswith("Birling"), rows[2]["estate_name"]
    assert _builder_from_banner(rows[0]["source_banner"]) == "Creation Homes"


def test_a_file_with_no_title_row_reports_no_banner():
    """No banner must mean no builder, not a crash and not the column header."""
    csv = (
        "Status,Lot,Frontage (m),Land Size (sqm),House Size (sqm),Package Price\n"
        "Available,2,15.8,502,245.16,\"$1,251,800\"\n"
    ).encode("utf-8")
    rows = extract_stocklist(csv, source_label="test.csv", builder_hint="")
    assert len(rows) == 1
    assert rows[0].get("source_banner") == ""
    assert _builder_from_banner(rows[0].get("source_banner")) == ""


def run_all():
    tests = [
        ("page divider separates builders from estates",
         test_page_divider_separates_builders_from_estates),
        ("an estate heading is never a builder",
         test_an_estate_heading_is_never_taken_for_a_builder),
        ("builder read from the file's title row",
         test_builder_is_read_from_the_stocklist_files_own_title_row),
        ("a banner naming no builder names nobody",
         test_a_banner_that_names_no_builder_names_nobody),
        ("one sheets tab is one file", test_one_google_sheets_tab_counts_as_one_file),
        ("seen-set reads one workbook once", test_the_seen_set_reads_one_workbook_once),
        ("file-named builder keeps builder scope",
         test_a_project_scoped_file_that_names_its_builder_is_builder_scoped),
        ("extractor surfaces the title row",
         test_extractor_surfaces_the_files_title_row_on_every_row),
        ("no title row means no banner", test_a_file_with_no_title_row_reports_no_banner),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] attribution: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] attribution: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
