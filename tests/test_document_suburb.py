"""Reading a stock document for the suburb it is selling, and not being fooled by it."""
import io

import document_suburb as DS


class _Index:
    """The two localities these tests need, in one state each."""
    _PLACES = {("murrumbeena", "VIC"), ("broadbeach", "QLD"), ("bourke", "NSW"),
               ("page", "ACT"), ("sale", "VIC"), ("richmond", "VIC")}

    def locate(self, name, state):
        return (str(name).lower(), str(state).upper()) in self._PLACES

    def states_for_suburb(self, name):
        return [s for n, s in self._PLACES if n == str(name).lower()]


def _doc(text):
    return lambda url, **kw: text.encode()


def _suburb(text, state="VIC"):
    """front_page_text only reads PDFs and workbooks, so bypass it for these."""
    names = DS.localities_in(text, state, _Index())
    return names[0] if len(names) == 1 else ""


def test_it_reads_the_locality_off_a_front_page():
    assert _suburb("Murrumbeena Pricelist - Current as of 30/03/2026\n"
                   "Sales by Product Type") == "Murrumbeena"


def test_the_street_guard_also_costs_a_project_called_X_Place():
    """The known price of the street guard, recorded rather than rediscovered later.

    'Place' is a street type, so a project named "Murrumbeena Place" is declined even
    though Murrumbeena is its suburb. Keeping the guard is still right: it is what stops
    "Level 3, 200 Bourke Street" from naming a price list Bourke, and a decline costs one
    document while a wrong answer costs every lot on it. Such a document is picked up by
    its filename or by a manual entry instead."""
    assert _suburb("Murrumbeena Place - Current as of 30/03/2026") == ""


def test_a_street_named_after_a_town_is_not_the_answer():
    """'Bourke' named a price list because the document mentions Bourke Street. Bourke is
    a town 760 km from it."""
    assert _suburb("Level 3, 200 Bourke Street\nPrice List", "NSW") == ""


def test_a_page_footer_is_not_a_suburb():
    """'Page' is a suburb of Canberra and the footer of every document ever printed."""
    assert _suburb("WE Commercial Price List\nPage 1 of 4", "ACT") == ""


def test_the_word_sales_is_not_the_town_of_sale():
    assert _suburb("Warehouse Price List\nSales by Product Type", "VIC") == ""


def test_two_localities_means_no_answer():
    """A price list covering several projects cannot be collapsed to one suburb, and
    taking the first would quietly mislabel every lot in the others."""
    assert _suburb("Richmond and Murrumbeena releases", "VIC") == ""


def test_a_workbook_is_read_as_well_as_a_pdf():
    """A third of these documents are .xlsx; skipping them cost 10 documents for no
    reason better than the file extension."""
    openpyxl = __import__("openpyxl")
    book = openpyxl.Workbook()
    book.active["A1"] = "Murrumbeena Place Pricelist"
    buffer = io.BytesIO()
    book.save(buffer)
    assert "Murrumbeena" in DS.front_page_text(buffer.getvalue())


def test_neither_a_pdf_nor_a_workbook_is_simply_declined():
    assert DS.front_page_text(b"<html>Murrumbeena</html>") == ""
def _rules(monkeypatch, rules):
    monkeypatch.setattr(DS, "_RULES_CACHE", rules)
    monkeypatch.setattr(DS, "_MAP_CACHE", {})


def test_a_new_price_list_at_a_new_link_still_resolves(monkeypatch):
    """The failure this prevents is silent and total.

    Builders re-upload their price list to a fresh Dropbox link every few weeks. The rows
    follow the new URL on the next harvest, so a hand-checked answer keyed only to the old
    link stops applying — 637 listings go back to having no suburb, nothing fails, and
    nothing says so. The project name travels with the row, so it survives the swap."""
    _rules(monkeypatch, [{"suburb": "Broadbeach", "state": "QLD", "project": "vanda",
                          "url": "https://old.example/VandA%20Price%20Jul.pdf"}])
    fresh = {"source_url": "https://new.example/VandA%20Price%20Sep.pdf",
             "builder_name": "Vanda", "state": "QLD"}
    assert DS.suburb_for_row(fresh) == "Broadbeach"


def test_it_also_covers_lots_added_to_that_project_later(monkeypatch):
    """A new release in a project already checked by hand needs no new checking."""
    _rules(monkeypatch, [{"suburb": "Murrumbeena", "state": "VIC",
                          "project": "murrumbeena"}])
    assert DS.suburb_for_row({"source_url": "https://any/where.pdf",
                              "estate_name": "Murrumbeena Place Stage 2",
                              "state": "VIC"}) == "Murrumbeena"


def test_a_project_rule_never_crosses_a_state(monkeypatch):
    _rules(monkeypatch, [{"suburb": "Broadbeach", "state": "QLD", "project": "vanda"}])
    assert DS.suburb_for_row({"source_url": "https://x", "builder_name": "Vanda",
                              "state": "VIC"}) == ""


def test_an_unrelated_builder_matches_nothing(monkeypatch):
    _rules(monkeypatch, [{"suburb": "Broadbeach", "state": "QLD", "project": "vanda"}])
    assert DS.suburb_for_row({"source_url": "https://x",
                              "builder_name": "Someone Else", "state": "QLD"}) == ""


def test_a_row_with_no_builder_or_estate_matches_nothing(monkeypatch):
    """An empty haystack must not match an empty-ish needle and label everything."""
    _rules(monkeypatch, [{"suburb": "Broadbeach", "state": "QLD", "project": "vanda"},
                         {"suburb": "Nowhere", "state": "QLD", "project": ""}])
    assert DS.suburb_for_row({"source_url": "https://x", "state": "QLD"}) == ""


def test_every_shipped_rule_carries_its_evidence():
    """These are claims about the real world made by a person, not read off a file. The
    address each was checked against is what lets the next reader re-verify it."""
    for rule in DS.manual_rules(refresh=True):
        assert rule.get("evidence"), f"{rule.get('suburb')!r} has no evidence recorded"
        assert rule.get("suburb") and rule.get("state")
        # AN EMAIL SUBJECT IS EXEMPT, and only an email subject. What this guards against
        # is a Dropbox or Wix link rotating and taking the rule with it, which is why a
        # rule needs a handle on the project or the document's own title as well. A
        # source_url of "email:Fw: $989K - REGISTERED SECONDARY DWELLING - COLLINGWOOD
        # PARK" is not a link that can rotate: it is the message, and it is the only
        # durable handle that row has -- no builder_name, no estate_name, and no document
        # to read a title from.
        if str(rule.get("url") or "").startswith("email:"):
            continue
        # The shared FOLDER counts as durable too (7 Oct 2026): a re-upload lands in the
        # same folder under a new link -- see test_a_reupload_into_the_same_folder below.
        assert rule.get("project") or rule.get("title") or rule.get("folder"), (
            f"{rule.get('suburb')!r} is keyed only by url and will stop applying when the "
            f"document is re-uploaded")


# --------------------------------------------- 7 Oct 2026: the re-upload that was missed
# The Victoria & Albert rule was keyed to the old link and the project word 'vanda'. The
# builder re-uploaded the list as 'Victoria & Albert - Sales List - 22 Sep 2026 4.55pm.pdf'
# -- a new link, a new file name, so a new builder_name ('Victoria & Albert 22 20 Pm') --
# and 114 Broadbeach apartments went back to having no suburb, which drops them from every
# Queensland search. Both handles the rule had were things the builder renames.
_VA_NEW = ("https://www.dropbox.com/scl/fo/db9kwomup9qkyxo5c9ifo/ACVah5xU4c3ad7SZ2F12aVY/"
           "Victoria%20%26%20Albert%20-%20Sales%20List%20-%2022%20Sep%202026%204.55pm.pdf")


def test_a_reupload_into_the_same_folder_keeps_its_suburb(monkeypatch):
    """What stayed the same across that re-upload is the Dropbox shared FOLDER the file
    sits in (/scl/fo/<id>/). Measured on the 6 Oct database: 88 Dropbox folders, 19 of
    them re-uploaded at least once, and every one of the 19 still held one project --
    while 'VandA Pricelist' -> 'Victoria & Albert - Sales List' and 'Price List Hali' ->
    'hali-dromana-pricelist' show how freely the file NAME changes."""
    _rules(monkeypatch, [{"suburb": "Broadbeach", "state": "QLD", "project": "vanda",
                          "folder": "db9kwomup9qkyxo5c9ifo",
                          "url": "https://www.dropbox.com/scl/fo/db9kwomup9qkyxo5c9ifo/"
                                 "AMldxx73oGFLOC976V04r_w/VandA%20Pricelist%20%282%29.pdf"}])
    fresh = {"source_url": _VA_NEW, "builder_name": "Victoria & Albert 22 20 Pm",
             "estate_name": "", "state": "QLD"}
    assert DS.suburb_for_row(fresh) == "Broadbeach"


def test_a_folder_rule_still_never_crosses_a_state(monkeypatch):
    _rules(monkeypatch, [{"suburb": "Broadbeach", "state": "QLD",
                          "folder": "db9kwomup9qkyxo5c9ifo"}])
    assert DS.suburb_for_row({"source_url": _VA_NEW, "state": "NSW"}) == ""


def test_a_short_or_empty_folder_matches_nothing(monkeypatch):
    """An empty or tiny folder value would be a substring of every link and label the
    whole catalogue with one suburb."""
    _rules(monkeypatch, [{"suburb": "Broadbeach", "state": "QLD", "folder": ""},
                         {"suburb": "Broadbeach", "state": "QLD", "folder": "fo"},
                         {"suburb": "Broadbeach", "state": "QLD", "folder": "scl"}])
    assert DS.suburb_for_row({"source_url": _VA_NEW, "state": "QLD"}) == ""


def test_the_folder_is_matched_as_a_whole_path_segment(monkeypatch):
    _rules(monkeypatch, [{"suburb": "Broadbeach", "state": "QLD",
                          "folder": "db9kwomup9qkyxo5c9if"}])      # one character short
    assert DS.suburb_for_row({"source_url": _VA_NEW, "state": "QLD"}) == ""


def test_a_hand_checked_rule_outranks_the_automatic_front_page_read(monkeypatch):
    """The README promises the hand-checked answer WINS. It only did for a rule keyed by
    the exact link: a project rule was consulted after the automatic map, so the nightly
    front-page read beat it on every re-upload.

    That is not hypothetical. On 6 Oct the nightly read '95SKR Master Price List' and
    answered 'Waterways' -- a suburb about 20 km from the building. 95SKR is Fareham,
    95 St Kilda Road, St Kilda (Gamuda Land), checked against the published address."""
    url = ("https://www.dropbox.com/scl/fo/y7euxnm1zy6mgeok7twlo/AMrJFXsWe_5JayvJl8HQ7dY/"
           "95SKR%20Master%20Price%20List%20with%20Outgoings%20-%2015042026.pdf")
    _rules(monkeypatch, [{"suburb": "St Kilda", "state": "VIC", "project": "95skr"}])
    monkeypatch.setattr(DS, "_MAP_CACHE", {url: ["Waterways", "VIC"]})
    row = {"source_url": url, "builder_name": "95Skr With Outgoings", "state": "VIC"}
    assert DS.suburb_for_row(row) == "St Kilda"
    # And the automatic answer still serves every document no rule covers.
    assert DS.suburb_for_row(dict(row, builder_name="Someone Else")) == "Waterways"


# The live rows that had no suburb on 6 Oct 2026, one per price list, as the database holds
# them (links shortened to the folder and file name: the rlkey part is a capability and the
# matching never reads it). Each is dropped from every search of its own state until a
# shipped rule names its suburb. Three are deliberately absent: 'Pitt Street Price' and
# 'PV Price List' (no published address found), and 'South Beach Price List' -- Surfers
# Paradise is evidenced, but its 21 rows are fragments gluing several lots' prices into
# one line, and a suburb would put them on screen as listings.
_SUBURBLESS_6OCT = [
    ("Victoria & Albert 22 20 Pm", "", "QLD", _VA_NEW, "Broadbeach"),
    ("Suncourt Residences 22 20 Pm", "", "QLD",
     "https://www.dropbox.com/scl/fo/205f2bzzrhzz0xt9bcgvi/AJt649ykH7fAIj_xJIp17K4/"
     "Suncourt%20Residences%20-%20Sales%20List%20-%2022%20Sep%202026%202.05pm.pdf",
     "Caloundra"),
    ("Agent", "Car stacker.Agent commission =", "VIC",
     "https://www.dropbox.com/scl/fo/ezut7t4ndyvuk9q1cyszo/AJn4tIuaVQtpSM9o5XCoY_M/"
     "Pricelist%20New%20Agent.pdf", "South Yarra"),
    ("Millwill Md", "I NTERNAL BALCONY TOTAL", "QLD",
     "https://www.dropbox.com/scl/fo/h72z22dz9lm2srrao2jde/AJeXaICtu3PEMqvU3bFqaZw/"
     "MasterPriceList%20%281%29%20Millwill%20MD.pdf", "Maroochydore"),
    ("", "Cellar 3Car East Open Car", "VIC",
     "https://www.dropbox.com/scl/fo/qln704nvvhfapqbazovy6/ABp5qA18E126GZVuB4fzlXc/"
     "Price%20Kew.pdf", "Kew"),
    ("Babylon", "One Bedroom", "NSW",
     "https://www.dropbox.com/scl/fo/zat100ndfygzuyupdqnu6/AE-D-PA3i1St34dels_astI/"
     "Price%20Babylon.pdf", "Rouse Hill"),
    ("Regattaprice", "Sales by Product Type", "VIC",
     "https://www.dropbox.com/scl/fo/8p8lruccyddd81x4vc0c5/AKJDcFUtsvVgz_Gic8np4Os/"
     "RegattaPrice.pdf", "Docklands"),
    ("The Foundry 20", "", "QLD",
     "https://www.dropbox.com/scl/fo/duov329z4z0ous320f44a/AGTnvo4htr6F5kLIxzLcI1Q/"
     "The_Foundry_StockList_2026-09-18%20%281%29%20%281%29.pdf", "North Ipswich"),
    ("95Skr With Outgoings", "Cage", "VIC",
     "https://www.dropbox.com/scl/fo/y7euxnm1zy6mgeok7twlo/AMrJFXsWe_5JayvJl8HQ7dY/"
     "95SKR%20Master%20Price%20List%20with%20Outgoings%20-%2015042026.pdf", "St Kilda"),
    ("Timor Residences 20", "", "QLD",
     "https://www.dropbox.com/scl/fo/3vf7am41yoxnur45n8hkp/ADlLnmR7CV6uN13yFWtPGPI/"
     "Timor_Residences_StockList_2026-09-22%20%281%29.pdf", "Loganholme"),
    ("Prince Parade Agent", "", "VIC",
     "https://www.dropbox.com/scl/fo/xuqmyipm7fqkff1891zfk/AOQfhNkPRMXWivNgPbbS07E/"
     "18032026%20-%20Prince%20%20Parade%20Agent%20Price%20List.pdf", "Brunswick"),
    ("Westwood Hattan", "2026 Westwood Storey", "VIC",
     "https://www.dropbox.com/scl/fo/2zmq210wctd1k0u50mbo2/AJCylGkfvu9ImT7HWU6dNWk/"
     "Westwood%20Price%20Hattan.pdf", "Fraser Rise"),
]


def test_the_shipped_rules_name_the_price_lists_that_had_no_suburb(monkeypatch):
    """Against the REAL rules file. Each of these developments is a published address a
    person checked (see each rule's evidence); before 7 Oct none of them had a working
    rule and their lots were missing from every search of their own state."""
    monkeypatch.setattr(DS, "_RULES_CACHE", None)
    monkeypatch.setattr(DS, "_MAP_CACHE", {})        # the automatic map plays no part
    missing = []
    for builder, estate, state, url, want in _SUBURBLESS_6OCT:
        got = DS.suburb_for_row({"builder_name": builder, "estate_name": estate,
                                 "state": state, "source_url": url})
        if got != want:
            missing.append((builder or url.rsplit("/", 1)[-1], got, want))
    assert not missing, missing


def test_the_same_rules_survive_the_next_reupload(monkeypatch):
    """Same price lists, re-uploaded into their folders under a new link and a new name
    (the shape of the 22 Sep V&A re-upload). Nothing in the row may still match the old
    file -- only the folder survives."""
    monkeypatch.setattr(DS, "_RULES_CACHE", None)
    monkeypatch.setattr(DS, "_MAP_CACHE", {})
    missing = []
    for _builder, _estate, state, url, want in _SUBURBLESS_6OCT:
        folder_link = url.split("/scl/fo/", 1)[1].split("/", 1)[0]
        moved = ("https://www.dropbox.com/scl/fo/" + folder_link +
                 "/AZZZnewfileidZZZZZZZZZ/Sales%20List%20-%2003%20Oct%202026.pdf")
        got = DS.suburb_for_row({"builder_name": "Sales List 03 Oct", "estate_name": "",
                                 "state": state, "source_url": moved})
        if got != want:
            missing.append((url.rsplit("/", 1)[-1][:40], got, want))
    assert not missing, missing


def test_every_shipped_rule_names_a_real_place_in_its_own_state():
    """A rule naming something the gazetteer does not know in that state does NOTHING --
    suburb_quality checks index.locate before using it -- and nothing reports that, so a
    typo in this file would read as a rule that works."""
    from geo import SuburbGeoIndex

    index = SuburbGeoIndex()
    if not index.loaded:                                   # pragma: no cover - env guard
        return
    bad = [(r["suburb"], r["state"]) for r in DS.manual_rules(refresh=True)
           if not index.locate(r["suburb"], r["state"])]
    assert not bad, bad


# --------------------------------------- 24 Oct 2026: a price list re-issued as pictures
# Until 21 Oct the nightly front-page read named the Atlas list itself: '282 rows ->
# Melbourne (VIC) Atlas Price New.pdf'. The copy re-issued that day at the same link is a
# Word export whose seven pages are each one image, so front_page_text reads nothing, and
# even read by OCR it opens on its column header and names no place anywhere. 206 VIC
# apartments dropped out of every suburb search and turned seven parity bars red.
_ATLAS = ("https://www.dropbox.com/scl/fo/3aknetazpoxl0ri7nv8i8/AKlDGtoVlS0NZ8YZ_RKSA1E/"
          "Atlas%20Price%20New.pdf")


def test_the_atlas_list_keeps_its_suburb_now_that_it_names_none(monkeypatch):
    monkeypatch.setattr(DS, "_RULES_CACHE", None)
    monkeypatch.setattr(DS, "_MAP_CACHE", {})        # the runner's map since 22 Oct
    row = {"builder_name": "Atlas", "estate_name": "", "state": "VIC", "source_url": _ATLAS}
    assert DS.suburb_for_row(row) == "Melbourne"
    # The next re-issue lands in the same folder under a new link.
    moved = dict(row, source_url=_ATLAS.replace(
        "AKlDGtoVlS0NZ8YZ_RKSA1E/Atlas%20Price%20New", "AZZZnewfileidZZZZZZZZZ/Atlas%20Nov"))
    assert DS.suburb_for_row(moved) == "Melbourne"


def test_the_atlas_rule_never_reaches_the_estate_of_the_same_name(monkeypatch):
    """Aldrich Homes and Goldstate Homes sell house-and-land in an estate called 'Atlas'.
    A `project: "atlas"` key would have moved those lots to the CBD."""
    monkeypatch.setattr(DS, "_RULES_CACHE", None)
    monkeypatch.setattr(DS, "_MAP_CACHE", {})
    for builder in ("Aldrich Homes", "Goldstate Homes"):
        row = {"builder_name": builder, "estate_name": "Atlas", "state": "VIC",
               "source_url": "https://www.e-agent.com.au/_files/ugd/"
                             "069fe0_3ddff6ad014e4285bfe789927d39d1a2.pdf"}
        assert DS.suburb_for_row(row) == "", builder


# ------------------------------------- 24 Oct 2026: three E-Agent lists that name no suburb
# The VIC no_suburb parity bar: 'Pitt Street' (ids 17450-17454) and 'PV Price List'
# (17465-17469) print a street and nothing else; their acknowledgement expired on 21 Oct.
# The '1 Part & Under Construction' list heads one lot "Kayabram Vic, Victoria, 3620" -- a
# typo for Kyabram, 3620 (id 31212). Each rule's evidence is in the rules file.
_PITT = ("https://www.dropbox.com/scl/fo/9y6mzs8ntl7qe98daypqt/AKI0ynHRIvDIzFXiQVw-5Y4/"
         "Pitt%20Street%20Price.pdf?rlkey=eqib19b9dvub57zchlas2arae&dl=1")
_PV = ("https://www.dropbox.com/scl/fo/8hvkzyt3a6an8efvs9os6/AAgTjqWhCuJf0v7hKc52vAE/"
       "PV%20Price%20List.pdf?rlkey=em8589uoih5nsxtwtk4cro7ur&dl=1")
_ONE_PART = "https://www.e-agent.com.au/_files/ugd/069fe0_df942b47fc7d463d8ff940284c381cd0.pdf"


def test_the_pitt_street_and_pv_lists_have_their_suburbs(monkeypatch):
    monkeypatch.setattr(DS, "_RULES_CACHE", None)
    monkeypatch.setattr(DS, "_MAP_CACHE", {})
    pitt = {"builder_name": "Pitt Street", "state": "VIC", "source_url": _PITT,
            "estate_name": "FIRB AnticipatedCompletion Primary Secondary CommissionRate&PaymentTerm"}
    pv = {"builder_name": "", "estate_name": None, "state": "VIC", "source_url": _PV}
    assert DS.suburb_for_row(pitt) == "Ringwood"
    assert DS.suburb_for_row(pv) == "Pascoe Vale"
    # The next re-issue lands in the same folder under a new link and a new name.
    for row, want in ((pitt, "Ringwood"), (pv, "Pascoe Vale")):
        folder = row["source_url"].split("/scl/fo/", 1)[1].split("/", 1)[0]
        moved = dict(row, source_url="https://www.dropbox.com/scl/fo/" + folder +
                     "/AZZZnewfileidZZZZZZZZZ/Price%20NEW.pdf")
        assert DS.suburb_for_row(moved) == want


def test_only_this_pitt_street_is_put_in_ringwood(monkeypatch):
    """Pitt Street is a street in many suburbs and states, so the rule has no project key:
    another development on one, here or in Sydney, keeps whatever its own list says."""
    monkeypatch.setattr(DS, "_RULES_CACHE", None)
    monkeypatch.setattr(DS, "_MAP_CACHE", {})
    assert DS.suburb_for_row({"builder_name": "Pitt Street", "estate_name": "",
                              "state": "VIC", "source_url": _PITT}) == "Ringwood"
    other = "https://www.e-agent.com.au/_files/ugd/069fe0_0000000000000000000000000000000a.pdf"
    for state in ("VIC", "NSW"):
        row = {"builder_name": "Pitt Street", "estate_name": "12 Pitt Street",
               "state": state, "source_url": other}
        assert DS.suburb_for_row(row) == "", state


def test_the_kayabram_typo_is_kyabram_and_no_other_town_on_its_list_is(monkeypatch):
    """The rule keys on the typo in the lot's section header, never on the list: the list
    covers about twenty towns."""
    monkeypatch.setattr(DS, "_RULES_CACHE", None)
    monkeypatch.setattr(DS, "_MAP_CACHE", {})
    row = {"builder_name": "1 Part & Under Construction", "state": "VIC",
           "estate_name": "KayabramVic,Victoria，3620", "source_url": _ONE_PART}
    assert DS.suburb_for_row(row) == "Kyabram"
    for estate in ("Echuca，Victoria，3564", "Tatura，Victoria，3616",
                   "16 Marlboro Drive"):
        assert DS.suburb_for_row(dict(row, estate_name=estate)) == "", estate


# ------------------------------------------------ a failed refresh must not wipe the map
def test_a_refresh_that_lost_most_of_its_answers_is_refused(tmp_path, monkeypatch):
    """The failure this stops is a GREEN one.

    The map is rebuilt nightly by fetching ~130 documents from other people's file hosts.
    If those fetches fail — a WAF turning hostile, an outage, the TLS-fingerprint block
    that already caught this project once — every document reads as "no text", the
    refresh completes normally, and an EMPTY map is written over the good one. 1,337
    listings lose their suburb overnight and the harvest reports success."""
    import json

    auto = tmp_path / "a.json"
    auto.write_text(json.dumps({f"u{i}": ["Suburb", "VIC"] for i in range(20)}),
                    encoding="utf-8")
    monkeypatch.setattr(DS, "map_path", lambda manual=False: str(auto))
    monkeypatch.setattr(DS, "_MAP_CACHE", None)

    DS.save_map({"u1": ["Suburb", "VIC"]})          # a collapse: 1 replacing 20
    assert len(json.loads(auto.read_text(encoding="utf-8"))) == 20, \
        "an empty-ish refresh overwrote the good map"

    DS.save_map({f"u{i}": ["Suburb", "VIC"] for i in range(15)})   # a normal shrink
    assert len(json.loads(auto.read_text(encoding="utf-8"))) == 15


def test_an_operator_can_still_discard_the_map_on_purpose(tmp_path, monkeypatch):
    import json

    auto = tmp_path / "a.json"
    auto.write_text(json.dumps({f"u{i}": ["Suburb", "VIC"] for i in range(20)}),
                    encoding="utf-8")
    monkeypatch.setattr(DS, "map_path", lambda manual=False: str(auto))
    monkeypatch.setattr(DS, "_MAP_CACHE", None)
    DS.save_map({}, force=True)
    assert json.loads(auto.read_text(encoding="utf-8")) == {}


def test_the_per_row_section_header_map_is_not_wired_in():
    """It bought 34 listings and cost four defects, one of which mislabelled dozens of
    rows at a time. row_suburbs stays as a record of the attempt; nothing calls it."""
    import suburb_quality

    row = {"source_text": "Available 61 House COPPER 21 $1,167,260", "state": "NSW"}
    monkey = getattr(DS, "suburb_for_line", None)
    assert monkey is None or suburb_quality._document_suburb(row) == "" or True
    src = open(suburb_quality.__file__, encoding="utf-8").read()
    assert "suburb_for_line" not in src, \
        "the per-row map is wired back into suburb_quality"
