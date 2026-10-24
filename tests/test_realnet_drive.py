"""RealNet's WA packages, read from its Google Drive folder (24 Oct 2026).

Coleen: "can we add these WA properties from E-Agent. They are all in the drive here".
The folder holds one PDF per package. Its file names are RealNet's own summary of each
package; its PDFs are made from templates whose text layers still carry an earlier
package. These tests pin what is read from which, and that nothing reaches the network.
"""
from datetime import datetime, timedelta

import pytest

from sources import realnet_drive as R

FOLDER = "113X94kcwbypLLEnmdGPVBHOg9nKd6hrO"


# ------------------------------------------------------------------ file names

def test_a_file_name_says_product_rooms_place_state_builder_code_and_price():
    n = R.parse_file_name("DUAL (3+2), Ravenswood Waters Estate, Ravenswood, BG - $975k.pdf")
    assert (n["kind"], n["config"], n["place"], n["suburb"], n["state"], n["builder_code"],
            n["price"]) == ("DUAL", "3+2", "Ravenswood Waters Estate", "Ravenswood", "WA",
                            "BG", 975_000)
    assert n["identity"] == "DUAL (3+2), Ravenswood Waters Estate, Ravenswood, BG"


def test_a_tasmanian_file_says_so_and_a_wa_file_does_not():
    n = R.parse_file_name("H+L (3x2), Bayview Subdivision, Rokeby, TASMANIA, CD - $749k.pdf")
    assert (n["state"], n["suburb"], n["builder_code"]) == ("TAS", "Rokeby", "CD")
    assert R.parse_file_name("H+L (4x2), Haynes Park, Haynes, JM - $864k.pdf")["state"] == "WA"


@pytest.mark.parametrize("name, price", [
    ("TRIPLE (2+1+1), Havenwood Heights, Gosnells, MC - $1.038M.pdf", 1_038_000),
    ("DUAL (3+2), Oyster Harbour, Albany, MC -$955k.pdf", 955_000),
    ("DUAL (3+2), Lakelands Estate, Lakelands, BG - $1.021M.pdf", 1_021_000),
    ("SMSF H+L (4x2), Capricorn Beach, Yanchep, JM - $910k.pdf", 910_000),
])
def test_every_price_spelling_in_the_folder_is_read(name, price):
    assert R.parse_file_name(name)["price"] == price


def test_a_name_with_no_price_or_no_package_is_not_a_listing():
    assert R.parse_file_name("SMSF DUAL (2+1), Echo Beach, Mandurah - Brochure.pdf") is None
    assert R.parse_file_name("_EOI - Template (Realnet).xlsx") is None
    assert R.parse_file_name("_Broker LOE Template.docx") is None


def test_a_brochure_and_its_price_list_name_one_package():
    a = R.parse_file_name("DUAL (2+1), Echo Beach, Mandurah - Brochure - $760k.pdf")
    b = R.parse_file_name("DUAL (2+1), Echo Beach, Mandurah - Pricelist - $760k.pdf")
    assert a["identity"] == b["identity"] == "DUAL (2+1), Echo Beach, Mandurah"
    assert (a["doc_word"], b["doc_word"]) == ("brochure", "pricelist")


def test_rooms_count_the_main_dwelling():
    assert R.rooms_from_config("H+L", "4x2") == (4, 2)
    assert R.rooms_from_config("DUAL", "3+2") == (3, None)
    assert R.rooms_from_config("DUAL", "2x1") == (1, None)      # "2 UNIT COMPLEX", 1 bed each
    assert R.rooms_from_config("TRIPLE", "2+1+1") == (2, None)


def test_a_name_price_is_cut_to_the_thousand():
    assert R.price_matches(1_068_490, 1_068_000)
    assert R.price_matches(864_360, 864_000)
    assert not R.price_matches(1_069_000, 1_068_000)
    assert not R.price_matches(948_500, 984_000)


# ------------------------------------------------------------------ brochure text

# The JM template, as its text layer reads: a hidden $879,000 package first, then its own.
JM_HAYNES = """HOUSE
& LAND
PACKAGE
$879,000
PACKAGE PRICE
ESTATE
Haynes Park Estate
BED/BATH/GARAGE
MAIN: 4x2   GUEST: 2x1
LAND SIZE
604m2
LAND PRICE
$270,000
BUILD SIZE
224m2
TITLE REGISTERED
Yes
SUBURB
Haynes
HOUSE DESIGN
The Panthenon
AT A GLANCE
$864,360
Haynes Park
Haynes
ESTATE
Haynes Park
BED/BATH/GARAGE
4x2x2
LAND SIZE
315m2
LAND PRICE
$376,425
BUILD SIZE
163.564m2
TITLE REGISTERED
Sept 26
SUBURB
Haynes
HOUSE DESIGN
Baymont
"""

# The BG street-lot template prints its fields first and the price last.
BG_KALLANG = """Kallang Road
Coodanup WA 6210
SUBURB
COODANUP
LOCATION
5A KALLANG ROAD
LAND SIZE
450 SQM
LAND PRICE
$390,000
HOUSE DESIGN
THE WEST VIEW - 5 BED ALT
BUILDING SIZE
201 SQM
TITLE REGISTERED
YES (READY TO BUILD)
LAND DEPOSIT
$3,000
Overview
HOUSE &
LAND PACKAGE
Price Starts at
$1,034,490
"""

# A DG template that prints its labels in one run and their values in the next.
DG_CHAMPION = """Package Price:
$1,143,343
House & Land Package
Lot 82 Pierrepoint Loop, Champion Lakes
Land Details:
CHAMPION LAKES ESTATE
ESTATE:
LAND SIZE:
LAND PRICE:
LAND DEPOSIT:
TITLED DATE:
SUBURB:
Champion Lakes
420m2
$504,000
$5,000
October 2026
Champion Lakes
Build Details:
BUILD SIZE:
BUILD PRICE:
BUILD DEPOSIT (Pre-works):
ESTIMATED BUILD TIME:
HOUSE DESIGN:
214.68m2
$639,343
$5,000
10 Months
Broadwater
"""


def _named(name):
    return R.parse_file_name(name)


def test_the_hidden_template_package_is_never_read():
    d, why = R.parse_brochure([JM_HAYNES], _named("H+L (4x2), Haynes Park, Haynes, JM - $864k.pdf"))
    assert why == ""
    assert d["price"] == 864_360
    assert (d["land_sqm"], d["land_price"], d["house_sqm"], d["house_design"], d["rooms"]) == \
        (315, 376_425, 163.564, "Baymont", "4x2x2")
    assert d["title"] == "Sept 26"


def test_fields_printed_before_the_price_are_read():
    d, _ = R.parse_brochure([BG_KALLANG],
                            _named("DUAL (3+2), 5A Kallang Road, Coodanup, BG - $1.034M.pdf"))
    assert (d["price"], d["land_sqm"], d["land_price"], d["house_sqm"]) == \
        (1_034_490, 450, 390_000, 201)
    assert R._title_status(d["title"]) == "Titled"


def test_a_column_of_labels_lines_up_with_its_column_of_values():
    d, _ = R.parse_brochure([DG_CHAMPION], _named(
        "DUAL (3+2), Champion Lakes Estate, Champion Lakes, DG - $1.143M.pdf"))
    assert (d["land_sqm"], d["land_price"], d["house_sqm"], d["build_price"]) == \
        (420, 504_000, 214.68, 639_343)
    assert (d["house_design"], d["title"], d["lot_number"]) == ("Broadwater", "October 2026", "82")


def test_a_brochure_that_does_not_print_its_names_price_gives_no_details():
    """"H+L (4x2), The Spires Estate, Baldivis, BG - $984k" prints $948,500."""
    d, why = R.parse_brochure(["Price Starts at\n$948,500\nLAND SIZE\n400 SQM\n"], _named(
        "H+L (4x2), The Spires Estate, Baldivis, BG - $984k.pdf"))
    assert d == {} and "does not print the price" in why


def test_fields_naming_another_suburb_are_not_taken():
    d, why = R.parse_brochure(["$864,360\nSUBURB\nBaldivis\nLAND SIZE\n315m2\n"],
                              _named("H+L (4x2), Haynes Park, Haynes, JM - $864k.pdf"))
    assert "land_sqm" not in d and "Baldivis" in why


# ------------------------------------------------------------------ the folder, offline

def _listing_html(files):
    entries = "".join(
        '<div class="flip-entry" id="entry-%s" tabindex="0" role="link"><div class="flip-entry-info">'
        '<a href="https://drive.google.com/%s/%s/view?usp=drive_web" target="_blank">'
        '<div class="flip-entry-title">%s</div></a></div>'
        '<div class="flip-entry-last-modified"><div>Oct 9</div></div></div>'
        % (fid, "drive/folders" if name.endswith("/") else "file/d", fid, name.rstrip("/"))
        for fid, name in files)
    return '<html><title>RealNet Stocklist</title><div class="flip-entries">%s</div></html>' % entries


def _pdf(text):
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 60), text, fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


class _Resp:
    def __init__(self, status, body=b"", url=""):
        self.status_code, self.content, self.url = status, body, url
        self.text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body

    def iter_content(self, n):
        for i in range(0, len(self.content), n):
            yield self.content[i:i + n]

    def close(self):
        pass


class _Drive:
    """A folder listing and its files, served from memory. Anything else is a test bug."""

    def __init__(self, files, pdfs, listing_status=200, listing_url=""):
        self.files, self.pdfs = files, pdfs
        self.listing_status, self.listing_url = listing_status, listing_url
        self.asked = []

    def get(self, url, timeout=None, stream=False):
        self.asked.append(url)
        if "embeddedfolderview" in url:
            return _Resp(self.listing_status, _listing_html(self.files).encode(),
                         self.listing_url or url)
        for fid, body in self.pdfs.items():
            if "id=%s&" % fid in url:
                return _Resp(200, body, url)
        return _Resp(404, b"", url)


def _source(files, pdfs, **kw):
    return R.RealNetDriveSource(folder=FOLDER, session=_Drive(files, pdfs, **kw))


HAYNES = ("fHaynes", "H+L (4x2), Haynes Park, Haynes, JM - $864k.pdf")
ROKEBY = ("fRokeby", "H+L (3x2), Bayview Subdivision, Rokeby, TASMANIA, CD - $749k.pdf")


def test_a_row_is_wa_names_the_folder_as_its_list_and_the_file_as_its_lot_page():
    src = _source([HAYNES], {"fHaynes": _pdf(JM_HAYNES)})
    [row] = src.search({})
    assert (row["source_channel"], row["state"], row["source_state_hint"], row["suburb"]) == \
        ("RealNet Stocklist", "WA", "WA", "Haynes")
    assert row["source_url_or_ref"] == row["stocklist_file"] == \
        "https://drive.google.com/drive/folders/%s" % FOLDER
    assert row["listing_url"] == "https://drive.google.com/file/d/fHaynes/view"
    assert (row["advertised_package_price"], row["land_size_sqm"], row["land_price"]) == \
        (864_360, 315, 376_425)
    assert (row["bedrooms"], row["bathrooms"], row["car_spaces"]) == (4, 2, 2)
    # Never stated, never invented.
    assert row["availability_status"] is None and row["builder_name"] == ""
    assert row["product_type"] == "House & Land"
    assert "RealNet builder code JM" in row["source_text"]
    assert src.state == "ok"


def test_a_tasmanian_package_is_filed_tas():
    text = ("TASMANIAN HOUSE AND LAND\n$749,000\nLOT 38 BAYVIEW SUBDIVISION, ROKEBY 7019\n"
            "House Area\nBuild Price\nLand Area\nLand Price\nEst. Rent P/W\nLIVING SPACE\n"
            "132 m2\n$391,000\n483 m2\n$358,000\n$650-$670\n")
    [row] = _source([ROKEBY], {"fRokeby": _pdf(text)}).search({})
    assert (row["state"], row["source_state_hint"], row["suburb"]) == ("TAS", "TAS", "Rokeby")
    assert (row["land_size_sqm"], row["house_size_sqm"], row["land_price"], row["build_price"]) == \
        (483, 132, 358_000, 391_000)
    assert row["lot_number"] == "38"
    assert row["lot_address"] == "House and land 3x2, Lot 38 Bayview Subdivision, Rokeby TAS"


def test_a_package_whose_brochure_prints_another_price_is_held_back():
    files = [("fSpires", "H+L (4x2), The Spires Estate, Baldivis, BG - $984k.pdf"), HAYNES]
    src = _source(files, {"fSpires": _pdf("Price Starts at\n$948,500\n"),
                          "fHaynes": _pdf(JM_HAYNES)})
    rows = src.search({})
    assert [r["suburb"] for r in rows] == ["Haynes"]
    held = [f for f in src.files if f[0].startswith("H+L (4x2), The Spires")]
    assert held and held[0][1] == "zero-yield" and "does not print the price" in held[0][3]


def test_a_brochure_and_its_price_list_are_one_listing_read_from_the_one_that_prints_it():
    files = [("fBro", "DUAL (2+1), Echo Beach, Mandurah - Brochure - $760k.pdf"),
             ("fPri", "DUAL (2+1), Echo Beach, Mandurah - Pricelist - $760k.pdf")]
    src = _source(files, {"fBro": _pdf("A development brochure with no unit prices\n"),
                          "fPri": _pdf("UNIT 8\n$760,000\n2x1 + 1x1\n")})
    [row] = src.search({})
    assert row["listing_url"].endswith("/fPri/view")
    assert row["advertised_package_price"] == 760_000


def test_two_packages_named_alike_keep_two_identities_and_two_labels():
    """supersede_duplicates retires one of two live rows that share a label, place and
    spec, so the two Oyster Harbour packages must not."""
    files = [("fA", "DUAL (3+2), Oyster Harbour, Albany, MC -$955k.pdf"),
             ("fB", "DUAL (3+2), Oyster Harbour, Albany, MC -$975k.pdf")]
    rows = _source(files, {"fA": _pdf("$955,000\n"), "fB": _pdf("$975,000\n")}).search({})
    assert len({r["content_hash"] for r in rows}) == 2
    assert len({r["lot_address"] for r in rows}) == 2


def test_identity_survives_a_price_change_but_not_a_new_file():
    one = _source([HAYNES], {"fHaynes": _pdf(JM_HAYNES)}).search({})[0]
    repriced = ("fHaynes", "H+L (4x2), Haynes Park, Haynes, JM - $870k.pdf")
    two = _source([repriced], {"fHaynes": _pdf(JM_HAYNES.replace("$864,360", "$870,100"))}
                  ).search({})[0]
    assert two["advertised_package_price"] == 870_100
    assert one["content_hash"] == two["content_hash"]
    other = _source([("fNew", HAYNES[1])], {"fNew": _pdf(JM_HAYNES)}).search({})[0]
    assert other["content_hash"] != one["content_hash"]


def test_sub_folders_templates_and_unpriced_names_are_listed_not_read():
    files = [("fHY", "High Yield Investments/"), ("fT", "_EOI - Template (Realnet).xlsx"),
             ("fS", "SMSF DUAL (2+1), Echo Beach, Mandurah - Brochure.pdf"), HAYNES]
    drive = _Drive(files, {"fHaynes": _pdf(JM_HAYNES)})
    src = R.RealNetDriveSource(folder=FOLDER, session=drive)
    assert len(src.search({})) == 1
    outcomes = {name: outcome for name, outcome, _n, _w in src.files}
    assert outcomes["High Yield Investments"] == "not a price list"
    assert outcomes["_EOI - Template (Realnet).xlsx"] == "not a price list"
    assert outcomes["SMSF DUAL (2+1), Echo Beach, Mandurah - Brochure.pdf"] == "no price list"
    # Only the one PDF that names a package was downloaded.
    assert sum("download" in u for u in drive.asked) == 1


def test_a_folder_that_is_no_longer_shared_reads_nothing_and_says_so():
    src = _source([HAYNES], {}, listing_status=403,
                  listing_url="https://accounts.google.com/ServiceLogin")
    assert src.search({}) == []
    assert src.state == "not_shared" and "anyone with the link" in src.why


def test_the_folder_can_be_moved_or_switched_off_without_a_code_change(monkeypatch):
    monkeypatch.setenv(R.FOLDER_ENV, "https://drive.google.com/drive/folders/abcDEF123?usp=x")
    assert R.RealNetDriveSource().folder_id == "abcDEF123"
    monkeypatch.setenv(R.FOLDER_ENV, "off")
    src = R.RealNetDriveSource()
    assert src.off and src.search({}) == [] and src.state == "off"


# ------------------------------------------------------------------ the nightly

def test_the_harvest_stores_the_folder_as_its_own_channel(tmp_path, monkeypatch):
    import config
    import harvest_buildings as hb
    from database import ResearchDatabase

    db_path = tmp_path / "h.db"
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)
    monkeypatch.setattr(hb, "ResearchDatabase", lambda *a, **k: ResearchDatabase(db_path=db_path))
    monkeypatch.setattr(hb, "BuilderRegistry", lambda *a, **k: None)
    drive = _Drive([HAYNES, ROKEBY], {"fHaynes": _pdf(JM_HAYNES),
                                      "fRokeby": _pdf("$749,000\n")})
    monkeypatch.setattr(hb, "RealNetDriveSource",
                        lambda *a, **k: R.RealNetDriveSource(folder=FOLDER, session=drive))
    code = hb.harvest(eagent=False, portals=False, email=False, proxima=False, rea=False,
                      realnet=True)
    assert code == 0
    rows = ResearchDatabase(db_path=db_path).get_buildings()
    assert sorted((r["state"], r["suburb"]) for r in rows) == [("TAS", "Rokeby"), ("WA", "Haynes")]
    assert {r["source_channel"] for r in rows} == {"RealNet Stocklist"}
    # Read again: updated in place, never a second row.
    hb.harvest(eagent=False, portals=False, email=False, proxima=False, rea=False, realnet=True)
    assert len(ResearchDatabase(db_path=db_path).get_buildings()) == 2


def test_a_folder_that_stops_answering_is_a_dead_channel(tmp_path, monkeypatch):
    import config
    import harvest_buildings as hb
    from database import ResearchDatabase

    db_path = tmp_path / "h.db"
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)
    monkeypatch.setattr(hb, "ResearchDatabase", lambda *a, **k: ResearchDatabase(db_path=db_path))
    monkeypatch.setattr(hb, "BuilderRegistry", lambda *a, **k: None)
    good = _Drive([HAYNES], {"fHaynes": _pdf(JM_HAYNES)})
    gone = _Drive([HAYNES], {}, listing_status=404)
    for drive, want in ((good, 0), (gone, 1)):
        monkeypatch.setattr(hb, "RealNetDriveSource",
                            lambda *a, _d=drive, **k: R.RealNetDriveSource(folder=FOLDER, session=_d))
        assert hb.harvest(eagent=False, portals=False, email=False, proxima=False, rea=False,
                          realnet=True) == want


def test_harvest_called_directly_never_reaches_google():
    import inspect
    import harvest_buildings as hb
    assert inspect.signature(hb.harvest).parameters["realnet"].default is False


TODAY = datetime(2026, 10, 24)


def _rn_row(rid, days_ago):
    return {"id": rid, "superseded_by": "", "source_channel": "RealNet Stocklist",
            "source_url": "https://drive.google.com/drive/folders/%s" % FOLDER,
            "stocklist_file": "", "builder_name": "", "lot_address": "pkg %d" % rid,
            "price": 800_000, "date_checked": (TODAY - timedelta(days=days_ago)).strftime("%d/%m/%Y")}


def test_a_package_whose_file_left_the_folder_is_retired_after_a_fortnight():
    import repair_rows as RR
    rows = [_rn_row(i, 0) for i in range(1, 12)] + [_rn_row(50, 14), _rn_row(51, 13)]
    gone = {r["id"] for r, _days in RR.find_withdrawn_from_source(rows, today=TODAY)}
    assert gone == {50}


def test_a_folder_read_that_came_back_half_empty_retires_nothing():
    import repair_rows as RR
    rows = [_rn_row(i, 0) for i in range(1, 7)] + [_rn_row(i, 20) for i in range(7, 13)]
    assert RR.find_withdrawn_from_source(rows, today=TODAY) == []
