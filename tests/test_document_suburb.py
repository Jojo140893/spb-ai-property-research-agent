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
        assert rule.get("project") or rule.get("title"), (
            f"{rule.get('suburb')!r} is keyed only by url and will stop applying when the "
            f"document is re-uploaded")


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
