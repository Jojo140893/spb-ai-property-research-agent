"""GET /api/kommo_leads -- the Leads tab's "All Kommo leads" card: every lead in Kommo, page
by page, through the hook's read-only /api/kommo-leads.

The same three jobs as GET /api/hot_leads, and most of the same code (api/hot_leads.py): send
SPB_SERVICE_KEY to the hook and nowhere else, make a failed read look like a failure, and --
without a valid spb_contact cookie -- mask every caller's number and email before the answer
leaves. A Kommo lead carries more text a person typed than a hot-lead record (the card's own
name, often a phone number), so the masked-view tests below read the RAW response bytes for
every spelling of every number and address in the fixture. And one more: only literals reach
the hook's URL, never the browser's text.

NOTHING HERE TOUCHES THE NETWORK: urlopen is stubbed in every test, and an autouse fixture
makes any real socket connection raise.
"""
import copy
import email.message
import io
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from api import kommo_leads as K

APP = Path(__file__).resolve().parent.parent
KEY = "svc-test-key-not-the-real-one-7f3a"
CONTACT_PASSWORD = "contact-test-password-not-the-real-one"


def _unlocked(now=None):
    """The Cookie header a browser sends after a correct contact-details password."""
    from api import _contact
    return _contact.unlock_cookie(now).split(";")[0]


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """No network, and no key or URL leaking in from the shell that runs the suite."""
    def refuse(*a, **k):
        raise AssertionError("a test tried to open a real network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    for name in ("SPB_SERVICE_KEY", "SPB_HOT_LEADS_URL", "SPB_KOMMO_LEADS_URL",
                 "SPB_CONTACT_PASSWORD", "SPB_LEAD_NOTES_URL"):
        monkeypatch.delenv(name, raising=False)


class _Resp:
    """What urlopen returns: a context manager with a status and a body."""

    def __init__(self, body=b"", status=200):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Upstream:
    """Stands in for urlopen, and records every request it was handed."""

    def __init__(self, respond):
        self.respond, self.calls = respond, []

    def __call__(self, req, timeout=None):
        self.calls.append((req, timeout))
        return self.respond(req)


def _install(monkeypatch, respond):
    up = _Upstream(respond)
    monkeypatch.setattr(urllib.request, "urlopen", up)
    return up


def _call(method, path="/api/kommo_leads", cookie=None):
    """Drive the handler as Vercel does, with no socket: (status, headers, body)."""
    h = K.handler.__new__(K.handler)
    h.command, h.path, h.request_version = method, path, "HTTP/1.1"
    h.requestline = f"{method} {path} HTTP/1.1"
    h.client_address = ("127.0.0.1", 0)
    h.headers = email.message.Message()
    if cookie is not None:
        h.headers["Cookie"] = cookie
    h.rfile, h.wfile = io.BytesIO(), io.BytesIO()
    getattr(h, "do_" + method)()
    head, _, body = h.wfile.getvalue().partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {k.strip().lower(): v.strip()
               for k, v in (line.split(":", 1) for line in lines[1:] if ":" in line)}
    return int(lines[0].split()[1]), headers, body


def _message(body):
    parsed = json.loads(body)
    assert parsed["status"] == "error"
    return parsed["message"]


# ------------------------------------------------------------------ the fixture
# The hook's answer for one page (vapi-kommo-hook lib/kommo-leads.js, "Response 200" in the
# build contract), with the people a Kommo list really holds. Each number and address is
# distinctive, so a leak of any spelling of it shows up in the raw bytes.

PHONE = "+61 400 111 678"
EMAIL = "joseph.test@example.com"
PHONE_SPELLINGS = ["+61 400 111 678", "+61400111678", "61400111678", "0400111678",
                   "0400 111 678", "0400-111-678", "400111678", "400 111 678"]
EMAIL_SPELLINGS = ["joseph.test@example.com", "joseph dot test at example"]
WA_PHONE = "+61 400 999 321"
WA_SPELLINGS = [WA_PHONE, "+61400999321", "61400999321", "0400999321", "400999321"]
# Someone else's details inside a card's name: a partner, a second address.
OTHER_PEOPLE = ["0411 222 333", "0411222333", "partner@elsewhere.net"]
# What the masking's rule for anyone's number does not take between a number's groups (28 Oct
# 2026, review): with no contact of the row's own to look for, only names_without_numbers
# stands between these and a masked viewer.
ODD_SEPS = ["/", "_", ",", ";", ":", "|", " / ", "*"]
SMS_SPELLINGS = (["0400 555 444", "0400555444", "400555444"]
                 + [sep.join(("0400", "555", "444")) for sep in ODD_SEPS])
PHONE_SPELLINGS += [sep.join(("0400", "111", "678")) for sep in ODD_SEPS]


def _kommo(lead_id, status_id=101025935, source_id=None, contact_id=None):
    return {"leadId": lead_id, "leadUrl": f"https://spb.kommo.com/leads/detail/{lead_id}",
            "contactId": contact_id, "pipelineId": 13102035, "pipelineName": "SPB",
            "statusId": status_id,
            "statusName": {101025935: "Incoming leads", 142: "Closed - won"}.get(status_id, ""),
            "closed": status_id in (142, 143), "sourceId": source_id,
            "updatedAt": "2026-10-28T01:02:03.000Z", "closedAt": None}


# A Clare caller with a record, whose card name a colleague typed with both their details and
# a partner's number in it.
CLARE = {
    "callId": "call-678", "hasRecord": True, "createdAt": "2026-10-27T22:41:09.000Z",
    "grade": "hot", "source": "clare",
    "cardName": "Joseph Test 0400 111 678 joseph.test@example.com, partner 0411 222 333 "
                "partner@elsewhere.net",
    "contactState": "ok",
    "customer": {"name": "Joseph Test", "phone": "0400111678", "email": EMAIL},
    "kommo": _kommo(54494433, source_id=23085771, contact_id=80000001),
}
# A WhatsApp chat: Kommo names the contact and the card by the number.
WHATSAPP = {
    "callId": "wa-54494434", "hasRecord": True, "createdAt": "2026-10-27T20:00:00.000Z",
    "grade": "warm", "source": "whatsapp", "cardName": WA_PHONE, "contactState": "ok",
    "customer": {"name": WA_PHONE, "phone": "0400999321", "email": ""},
    "kommo": _kommo(54494434, source_id=23082489, contact_id=80000002),
}
# A RingCentral SMS whose contact the hook did not get to read (contactState "unread").
SMS = {
    "hasRecord": False, "createdAt": "2026-10-26T03:00:00.000Z", "grade": None,
    "source": "ringcentral_sms", "cardName": "SMS 0400 555 444", "contactState": "unread",
    "customer": {"name": "", "phone": "", "email": ""},
    "kommo": _kommo(54494435, source_id=23065156, contact_id=80000003),
}
# A Facebook comment with no contact at all, closed.
NO_CONTACT = {
    "hasRecord": False, "createdAt": "2026-10-20T03:00:00.000Z", "grade": None,
    "source": "facebook", "cardName": "Comment on the Tarneit post", "contactState": "none",
    "customer": {"name": "", "phone": "", "email": ""},
    "kommo": _kommo(54494436, status_id=142, source_id=23062236),
}
PAGE = {
    "status": "success", "scope": "incoming", "page": 1, "pageSize": 250, "count": 4,
    "hasMore": False, "fetchedAt": "2026-10-28T08:15:02.113Z", "recordsComplete": True,
    "errors": [],
    "columns": [
        {"pipelineId": 13102035, "pipelineName": "SPB", "statusId": 101025935,
         "statusName": "Incoming leads", "sort": 10, "closed": False},
        {"pipelineId": 13102035, "pipelineName": "SPB", "statusId": 142,
         "statusName": "Closed - won", "sort": 10000, "closed": True}],
    "leads": [CLARE, WHATSAPP, SMS, NO_CONTACT],
}


def _leaks(body):
    """Every spelling of anyone's number or address found in the raw bytes, lower-cased."""
    text = body.decode("utf-8").lower()
    spellings = PHONE_SPELLINGS + EMAIL_SPELLINGS + WA_SPELLINGS + OTHER_PEOPLE + SMS_SPELLINGS
    return [s for s in spellings if s.lower() in text]


def _ok(monkeypatch, body=None):
    raw = json.dumps(PAGE if body is None else body, indent=1).encode()
    return _install(monkeypatch, lambda req: _Resp(raw)), raw


def _page(monkeypatch, upstream=None, cookie=None, path="/api/kommo_leads"):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _ok(monkeypatch, copy.deepcopy(PAGE if upstream is None else upstream))
    return _call("GET", path, cookie=cookie)


# ------------------------------------------------------------------ only literals go upstream

def _sent_query(up):
    return urllib.parse.urlsplit(up.calls[0][0].full_url).query


@pytest.mark.parametrize("path,query", [
    ("/api/kommo_leads?scope=all&page=2", "scope=all&page=2"),
    ("/api/kommo_leads?page=40&scope=all", "scope=all&page=40"),
    ("/api/kommo_leads?scope=incoming&page=3", "scope=incoming&page=3"),
    ("/api/kommo_leads?scope=all", "scope=all&page=1"),
    ("/api/kommo_leads?page=007", "scope=incoming&page=7"),
])
def test_an_exact_scope_and_page_are_forwarded_as_literals(monkeypatch, path, query):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    assert _call("GET", path)[0] == 200
    assert up.calls[0][0].full_url == K.DEFAULT_URL + "?" + query


@pytest.mark.parametrize("path", [
    "/api/kommo_leads",
    "/api/kommo_leads?scope=",
    "/api/kommo_leads?scope=ALL",
    "/api/kommo_leads?scope=All",
    "/api/kommo_leads?scope=pipeline",
    "/api/kommo_leads?scope=%20all",                     # " all"
    "/api/kommo_leads?scope=all&scope=all",              # an array is not one answer
    "/api/kommo_leads?scope=all&scope=incoming",
    "/api/kommo_leads?scope=all%26page%3D2",             # "all&page=2", smuggling a parameter
    "/api/kommo_leads?SCOPE=all",
    "/api/kommo_leads?page=",
    "/api/kommo_leads?page=0",
    "/api/kommo_leads?page=41",
    "/api/kommo_leads?page=1000",
    "/api/kommo_leads?page=-1",
    "/api/kommo_leads?page=2.0",
    "/api/kommo_leads?page=1e1",
    "/api/kommo_leads?page=+2",
    "/api/kommo_leads?page=%D9%A5",                      # an Arabic-Indic five
    "/api/kommo_leads?page=2%26scope%3Dall",             # "2&scope=all"
    "/api/kommo_leads?page=2&page=3",
    "/api/kommo_leads?other=all",
])
def test_garbage_or_arrays_are_scope_incoming_page_1(monkeypatch, path):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    assert _call("GET", path)[0] == 200
    assert up.calls[0][0].full_url == K.DEFAULT_URL + "?scope=incoming&page=1", \
        up.calls[0][0].full_url


@pytest.mark.parametrize("query,scope,page", [
    ("scope=all&page=40", "all", 40), ("scope=incoming&page=1", "incoming", 1),
    ("page=41", "incoming", 1), ("page=0", "incoming", 1), ("page=1000", "incoming", 1),
    ("page=%D9%A5", "incoming", 1), ("page=2&page=2", "incoming", 1),
    ("scope=all&scope=all", "incoming", 1), ("scope=ALL", "incoming", 1),
])
def test_scope_from_and_page_from_on_their_own(query, scope, page):
    """Each guard on its own, not only behind fetch's second look."""
    assert (K.scope_from("/api/kommo_leads?" + query), K.page_from("/api/kommo_leads?" + query)) \
        == (scope, page)


@pytest.mark.parametrize("scope,page", [("ALL", 2), ("all&x=1", 2), (None, 2), ("all", 0),
                                         ("all", 41), ("all", "2"), ("all", True), ("all", 2.0)])
def test_fetch_itself_forwards_only_literals(monkeypatch, scope, page):
    """A second guard behind scope_from and page_from: a future caller of fetch cannot splice
    text into the hook's URL."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    assert K.fetch(scope, page)[0] == 200
    query = urllib.parse.parse_qs(_sent_query(up))
    assert set(query) == {"scope", "page"}
    assert query["scope"][0] in K.SCOPES and query["page"][0] in [str(n) for n in range(1, 41)]
    assert "x=1" not in up.calls[0][0].full_url and "ALL" not in up.calls[0][0].full_url


# ------------------------------------------------------------------ the key

def test_no_key_on_the_dashboard_is_a_503_and_nothing_is_sent(monkeypatch):
    up, _ = _ok(monkeypatch)
    status, headers, body = _call("GET")
    assert status == 503
    assert _message(body) == "SPB_SERVICE_KEY is not set on the dashboard project"
    assert headers["cache-control"] == "no-store"
    assert up.calls == []


@pytest.mark.parametrize("setting", ["SPB_KOMMO_LEADS_URL", "SPB_HOT_LEADS_URL"])
@pytest.mark.parametrize("url", ["http://vapi-kommo-hook.vercel.app/api/kommo-leads",
                                 "file:///etc/passwd", "kommo-leads", "https://"])
def test_a_non_https_upstream_is_refused_before_the_key_is_attached(monkeypatch, setting, url):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv(setting, url)
    up, _ = _ok(monkeypatch)
    status, _, body = _call("GET")
    assert status == 503 and "https" in _message(body) and setting in _message(body)
    assert up.calls == [], "the key must not be sent in the clear"


def test_the_key_travels_as_an_unredirected_header_on_a_GET_to_the_hook(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", f"  {KEY}\n")
    up, _ = _ok(monkeypatch)
    status, _, body = _call("GET")
    assert status == 200 and KEY not in body.decode()
    req, timeout = up.calls[0]
    assert req.full_url == "https://vapi-kommo-hook.vercel.app/api/kommo-leads?scope=incoming&page=1"
    assert req.get_method() == "GET" and req.data is None
    assert timeout == K.TIMEOUT == 25
    assert req.unredirected_hdrs.get("X-spb-service-key") == KEY
    assert all(k.lower() != "x-spb-service-key" for k in req.headers), \
        "an ordinary header is copied onto a redirect"
    followed = urllib.request.HTTPRedirectHandler().redirect_request(
        req, io.BytesIO(), 302, "Found", email.message.Message(),
        "https://elsewhere.example/collect")
    assert followed is not None, "the stdlib would follow this redirect"
    assert all(k.lower() != "x-spb-service-key" for k, _ in followed.header_items()), \
        "the service key would be handed to the redirect's host"


def test_the_list_follows_the_leads_upstream_unless_it_has_its_own(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_HOT_LEADS_URL", "https://staging-hook.example/api/hot-leads?x=1#f")
    up, _ = _ok(monkeypatch)
    _call("GET", "/api/kommo_leads?scope=all&page=2")
    assert up.calls[0][0].full_url == \
        "https://staging-hook.example/api/kommo-leads?scope=all&page=2"
    monkeypatch.setenv("SPB_KOMMO_LEADS_URL", "https://own.example/api/kommo-leads")
    up, _ = _ok(monkeypatch)
    _call("GET")
    assert up.calls[0][0].full_url == "https://own.example/api/kommo-leads?scope=incoming&page=1"


# ------------------------------------------------------------------ the masked view

def test_the_masked_view_carries_no_spelling_of_anyones_number_or_email(monkeypatch):
    status, headers, body = _page(monkeypatch)
    assert status == 200
    assert _leaks(body) == [], "contact details in the masked answer"
    doc = json.loads(body)
    assert doc["contact"] == "masked" and doc["contactUnlock"] == "not-configured"
    clare, wa, sms, none = doc["leads"]
    assert clare["customer"] == {"name": "Joseph Test", "email": "", "phone": "",
                                 "phoneLast3": "678", "hasEmail": True, "hasPhone": True}
    assert clare["cardName"] == "Joseph Test [hidden] [hidden], partner [hidden] [hidden]"
    assert wa["customer"] == {"name": "[hidden]", "email": "", "phone": "",
                              "phoneLast3": "321", "hasEmail": False, "hasPhone": True}
    assert wa["cardName"] == "[hidden]"
    assert sms["cardName"] == "SMS [hidden]" and sms["contactState"] == "unread"
    assert sms["customer"]["phoneLast3"] == "" and sms["customer"]["hasPhone"] is False
    assert none["contactState"] == "none" and none["cardName"] == "Comment on the Tarneit post"
    assert not any(lead.get("maskFailed") for lead in doc["leads"])
    assert headers["cache-control"] == "no-store" and headers["vary"] == "Cookie"


def test_the_masked_view_keeps_the_ids_links_and_dates_the_card_works_from(monkeypatch):
    """A Kommo id is 8 or more digits: as a string outside kommo.* it would be "[hidden]"."""
    doc = json.loads(_page(monkeypatch)[2])
    for got, sent in zip(doc["leads"], PAGE["leads"]):
        assert got["kommo"] == sent["kommo"]
        assert type(got["kommo"]["leadId"]) is int and type(got["kommo"]["statusId"]) is int
        assert got["createdAt"] == sent["createdAt"]
        assert got["source"] == sent["source"] and got["grade"] == sent["grade"]
        assert got.get("callId") == sent.get("callId")
        assert got["hasRecord"] is sent["hasRecord"]
    assert doc["columns"] == PAGE["columns"]
    for key in ("scope", "page", "pageSize", "count", "hasMore", "fetchedAt",
                "recordsComplete", "errors"):
        assert doc[key] == PAGE[key], key


def test_the_absence_check_sees_a_leak_when_the_masking_is_switched_off(monkeypatch):
    """A silent check reads as a pass: with the masking replaced by the identity, the same
    search over the same bytes must find the numbers -- or the tests above prove nothing.
    Both parts off: the masking, and the pass on names before it."""
    monkeypatch.setattr(K._contact, "mask_hot_leads", lambda doc: doc)
    monkeypatch.setattr(K, "names_without_numbers", lambda doc: doc)
    status, _, body = _page(monkeypatch)
    assert status == 200
    found = _leaks(body)
    for s in ("0400 111 678", "joseph.test@example.com", "0411 222 333", WA_PHONE, "0400 555 444"):
        assert s in found, s


def test_a_valid_cookie_gets_the_full_view(monkeypatch):
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", CONTACT_PASSWORD)
    status, headers, body = _page(monkeypatch, cookie=f"spb_session=1.a; {_unlocked()}")
    assert status == 200
    assert json.loads(body) == {**PAGE, "contact": "full", "contactUnlock": "password"}
    assert "0400111678" in body.decode() and EMAIL in body.decode()
    assert headers["vary"] == "Cookie"


# ------------------------------------------------------------------ a number in a name

def _sms_named(number, state):
    """The SMS row with the number in its card's and its contact's name, its contact `state`."""
    lead = copy.deepcopy(SMS)
    lead["contactState"] = state
    lead["cardName"] = f"SMS {number}"
    lead["customer"] = {"name": f"Jo {number}", "phone": "", "email": ""}
    return dict(copy.deepcopy(PAGE), leads=[lead, copy.deepcopy(NO_CONTACT)])


@pytest.mark.parametrize("state", ["unread", "none"])
@pytest.mark.parametrize("sep", ODD_SEPS)
def test_a_number_in_a_name_is_hidden_whatever_separates_its_groups(monkeypatch, state, sep):
    """28 Oct 2026, review: "unread" (a contacts batch Kommo throttled, or one past the hook's
    deadline: 50 leads at a time) and "none" have no number of their own for the second look
    to find, and the rule for anyone's number takes only a space, dash, dot or bracket. A
    RingCentral SMS card is named by the number."""
    number = sep.join(("0400", "555", "444"))
    status, _, body = _page(monkeypatch, _sms_named(number, state))
    assert status == 200
    lead = json.loads(body)["leads"][0]
    assert (lead["cardName"], lead["customer"]["name"]) == ("SMS [hidden]", "Jo [hidden]")
    assert lead["contactState"] == state and not lead.get("maskFailed")
    assert number not in body.decode() and _leaks(body) == []


def test_the_name_check_sees_a_number_when_the_pass_is_switched_off(monkeypatch):
    """A silent check reads as a pass: without names_without_numbers, the same scan over the
    same bytes must find every one of the spellings above."""
    monkeypatch.setattr(K, "names_without_numbers", lambda doc: doc)
    for sep in ODD_SEPS:
        number = sep.join(("0400", "555", "444"))
        status, _, body = _page(monkeypatch, _sms_named(number, "unread"))
        assert status == 200 and number in _leaks(body), sep


@pytest.mark.parametrize("name", ["Lot 12/345, stage 3", "Budget $750,000", "Unit 123/4567",
                                  "Comment on the Tarneit post"])
def test_a_name_with_fewer_than_8_digits_in_a_run_is_left_alone(monkeypatch, name):
    lead = dict(copy.deepcopy(NO_CONTACT), cardName=name)
    status, _, body = _page(monkeypatch, dict(copy.deepcopy(PAGE), leads=[lead]))
    assert status == 200 and json.loads(body)["leads"][0]["cardName"] == name


def test_a_name_nested_too_deep_to_walk_is_hidden_whole(monkeypatch):
    def deep(value):
        raise RecursionError("maximum recursion depth exceeded")
    monkeypatch.setattr(K, "_no_long_numbers", deep)
    status, _, body = _page(monkeypatch, _sms_named("0400/555/444", "unread"))
    lead = json.loads(body)["leads"][0]
    assert status == 200 and _leaks(body) == []
    assert (lead["cardName"], lead["customer"]["name"]) == ("[hidden]", "[hidden]")


def test_an_answer_the_pass_cannot_write_back_is_a_502_never_the_answer_without_it(monkeypatch):
    """Read but not written back (nested too deep to encode): the raw answer would be masked
    without the pass, so nothing is shown. Only the pass's write fails: hot_leads.view's own,
    after the masking cut the deep lead down, would succeed."""
    real, calls = K._hl._body, []

    def deep_once(doc):
        calls.append(1)
        if len(calls) == 1:
            raise RecursionError("maximum recursion depth exceeded while encoding a JSON object")
        return real(doc)
    monkeypatch.setattr(K._hl, "_body", deep_once)
    status, headers, body = _page(monkeypatch, _sms_named("0400/555/444", "unread"))
    assert status == 502 and "could not be hidden (RecursionError)" in _message(body)
    assert _leaks(body) == [] and headers["cache-control"] == "no-store"


def test_the_full_view_keeps_a_number_in_a_name(monkeypatch):
    """The pass is the masked view's: unlocked, the card's name is what Kommo has."""
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", CONTACT_PASSWORD)
    status, _, body = _page(monkeypatch, _sms_named("0400/555/444", "unread"), cookie=_unlocked())
    doc = json.loads(body)
    assert status == 200 and doc["contact"] == "full"
    assert doc["leads"][0]["cardName"] == "SMS 0400/555/444"


def test_a_cookie_that_runs_out_during_the_view_gets_the_pass(monkeypatch):
    """view() looks at the cookie, then hot_leads.view looks again: good the first time and
    gone by the second is the masked view, and it must not skip the pass."""
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", CONTACT_PASSWORD)
    looks = iter([True])                      # then False, for every look after the first
    monkeypatch.setattr(K._contact, "authorised", lambda header, now=None: next(looks, False))
    status, _, body = _page(monkeypatch, _sms_named("0400/555/444", "unread"), cookie=_unlocked())
    doc = json.loads(body)
    assert status == 200 and doc["contact"] == "masked"
    assert doc["leads"][0]["cardName"] == "SMS [hidden]" and _leaks(body) == []


def test_a_cookie_signed_with_another_password_gets_the_masked_view(monkeypatch):
    from api import _contact as C
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", CONTACT_PASSWORD)
    exp = str(int(__import__("time").time()) + 600)
    status, _, body = _page(monkeypatch, cookie=f"spb_contact={exp}.{C.sign('other', exp)}")
    doc = json.loads(body)
    assert status == 200 and _leaks(body) == []
    assert (doc["contact"], doc["contactUnlock"]) == ("masked", "password")


# ------------------------------------------------------------------ fails closed

@pytest.mark.parametrize("upstream", [
    [CLARE],                                                    # a list, not an object
    {"status": "success", "leads": "0400 111 678"},             # leads not a list
    {"status": "success", "count": 1},                          # no leads at all
    {"status": "success", "leads": ["0400 111 678"]},           # a lead not an object
    {"status": "success", "leads": [CLARE, 54494433]},
])
def test_an_answer_that_cannot_be_masked_is_a_502_never_the_raw_body(monkeypatch, upstream):
    status, _, body = _page(monkeypatch, upstream)
    assert status == 502, body[:200]
    assert "could not be hidden" in _message(body)
    assert _leaks(body) == []


def test_a_lead_whose_own_number_survives_is_cut_down_alone(monkeypatch):
    """The second look (api/_contact.py Scrubber.leaked) reads digits alone: "0400 or 111 then
    678" is the caller's own number, which no phone-shaped rule sees. That lead is cut down to
    its grade, date, callId and Kommo link; every other lead is as it was."""
    bad = copy.deepcopy(CLARE)
    bad["cardName"] = "Joseph, ring 0400 or 111 then 678"
    page = dict(copy.deepcopy(PAGE), leads=[bad, WHATSAPP, SMS, NO_CONTACT])
    status, _, body = _page(monkeypatch, page)
    assert status == 200 and _leaks(body) == [] and "then 678" not in body.decode()
    doc = json.loads(body)
    cut, *rest = doc["leads"]
    assert cut == {"callId": "call-678", "grade": "hot", "createdAt": CLARE["createdAt"],
                   "kommo": {"leadUrl": "https://spb.kommo.com/leads/detail/54494433"},
                   "customer": {"name": "[hidden]", "email": "", "phone": "",
                                "phoneLast3": "678", "hasEmail": True, "hasPhone": True},
                   "maskFailed": True}
    assert [lead.get("maskFailed") for lead in doc["leads"]] == [True, None, None, None]
    assert [lead["kommo"]["leadId"] for lead in rest] == [54494434, 54494435, 54494436]
    assert rest[0]["customer"]["phoneLast3"] == "321" and rest[1]["cardName"] == "SMS [hidden]"


def test_a_cut_down_lead_with_no_grade_keeps_its_link(monkeypatch):
    """A Kommo-only lead has no callId and often no grade: what is left of it is its link."""
    bad = copy.deepcopy(SMS)
    bad["customer"] = {"name": "", "phone": "0400555444", "email": ""}
    bad["cardName"] = "text 0400 or 555 then 444"
    status, _, body = _page(monkeypatch, dict(copy.deepcopy(PAGE), leads=[bad]))
    assert status == 200 and _leaks(body) == []
    cut = json.loads(body)["leads"][0]
    assert cut["maskFailed"] is True and "callId" not in cut and cut["grade"] == ""
    assert cut["kommo"] == {"leadUrl": "https://spb.kommo.com/leads/detail/54494435"}


# ------------------------------------------------------------------ hook failures

def _http_error(code, body):
    def respond(req):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        raise urllib.error.HTTPError(req.full_url, code, "upstream", {}, io.BytesIO(raw))
    return respond


@pytest.mark.parametrize("code,body,status,says", [
    # Vercel's NOT_FOUND page: the hook deployed without this route.
    (404, b"The page could not be found\n\nNOT_FOUND", 503,
     "the vapi-kommo-hook project has no /api/kommo-leads route; deploy the hook"),
    (404, {"status": "error"}, 503, "no /api/kommo-leads route"),
    (401, {"status": "error", "message": "bad key"}, 503, "SPB_SERVICE_KEY does not match"),
    (401, b"<!doctype html><title>Log in</title>", 503, "deployment protection"),
    (500, {"status": "error", "message": "kommo not configured"}, 503,
     "KOMMO_DOMAIN/KOMMO_TOKEN not configured on the vapi-kommo-hook project"),
    (503, {"status": "error", "message": "kommo not configured"}, 503,
     "KOMMO_DOMAIN/KOMMO_TOKEN not configured on the vapi-kommo-hook project"),
    (500, {"status": "error", "message": "SPB_SERVICE_KEY not configured"}, 503,
     "SPB_SERVICE_KEY not configured on the vapi-kommo-hook project"),
    # The hook may start naming itself; "kommo" in its own name must not read as Kommo's.
    (500, {"status": "error", "message": "SPB_SERVICE_KEY not configured on vapi-kommo-hook"},
     503, "SPB_SERVICE_KEY not configured on the vapi-kommo-hook project"),
    (502, {"status": "error", "message": "kommo leads read failed (HTTP 429)"}, 502,
     "kommo-leads answered HTTP 502: kommo leads read failed (HTTP 429)"),
    (502, {"status": "error", "message": "kommo leads read failed"}, 502, "HTTP 502"),
    (500, {"status": "error", "message": "boom"}, 502, "kommo-leads answered HTTP 500: boom"),
    (504, {"status": "error", "message": "FUNCTION_INVOCATION_TIMEOUT"}, 502, "HTTP 504"),
    (405, {"status": "error", "message": "GET only"}, 502, "HTTP 405"),
])
def test_each_hook_failure_maps_to_a_503_setting_or_a_502_retry(monkeypatch, code, body,
                                                                status, says):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, _http_error(code, body))
    got, headers, raw = _call("GET")
    assert got == status
    msg = _message(raw)
    assert says in msg, msg
    assert str(code) in msg
    assert KEY not in raw.decode() and headers["cache-control"] == "no-store"


@pytest.mark.parametrize("code", [400, 401, 403, 404, 405, 429, 500, 502, 503, 504])
@pytest.mark.parametrize("message", [None, "bad key", "kommo not configured",
                                     "SPB_SERVICE_KEY not configured"])
def test_no_hook_answer_becomes_a_401_from_this_endpoint(monkeypatch, code, message):
    """The tab reads a 401 from /api/* as "your sign-in expired" and opens the login page."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, _http_error(code, {"status": "error", "message": message}))
    status, _, body = _call("GET")
    assert status in (502, 503), status
    assert str(code) in _message(body)


def test_a_hook_message_carrying_a_number_or_the_key_does_not_pass_it_on(monkeypatch, capsys):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, _http_error(500, {"status": "error",
                                            "message": "failed for 0400 111 678 on " + EMAIL}))
    status, _, body = _call("GET")
    assert status == 502 and _leaks(body) == [] and "[hidden]" in _message(body)
    _install(monkeypatch, _http_error(401, {"status": "error", "message": f"bad key {KEY}"}))
    status, _, body = _call("GET")
    assert status == 503 and KEY not in body.decode()
    out = capsys.readouterr()
    assert KEY not in out.out + out.err


@pytest.mark.parametrize("code,raw", [
    (302, b'{"status": "success", "count": 0, "leads": []}'),
    (500, b'{"status": "error", "message": "boom"}'),
])
def test_a_non_2xx_that_urlopen_returns_rather_than_raises_is_not_a_200(monkeypatch, code, raw):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, lambda req: _Resp(raw, status=code))
    status, _, body = _call("GET")
    assert status == 502 and str(code) in _message(body)


def test_a_200_that_is_not_json_or_nested_too_deep_is_a_502(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, lambda req: _Resp(b"<!doctype html><title>Log in</title>"))
    status, _, body = _call("GET")
    assert status == 502 and "not JSON" in _message(body)
    _install(monkeypatch, lambda req: _Resp(b"[" * 100_000 + b"]" * 100_000))
    status, _, body = _call("GET")
    assert status == 502 and "nested too deep" in _message(body)


@pytest.mark.parametrize("exc,says", [
    (TimeoutError("timed out"), "timed out after 25s"),
    (socket.timeout("The read operation timed out"), "timed out after 25s"),
    (urllib.error.URLError(TimeoutError("timed out")), "timed out after 25s"),
    (urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")), "gaierror"),
    (ConnectionResetError(104, "reset"), "ConnectionResetError"),
])
def test_a_network_failure_or_timeout_is_a_502_naming_the_host_only(monkeypatch, exc, says):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_KOMMO_LEADS_URL", "https://user:secret@hook.example/api/kommo-leads")

    def respond(req):
        raise exc
    _install(monkeypatch, respond)
    status, _, body = _call("GET")
    msg = _message(body)
    assert status == 502 and "could not reach" in msg and says in msg, msg
    assert "hook.example" in msg and "secret" not in msg and "user" not in msg
    assert KEY not in body.decode()


# ------------------------------------------------------------------ methods and logs

@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_only_GET_is_served(monkeypatch, method):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    status, headers, body = _call(method)
    assert status == 405 and headers.get("allow") == "GET"
    assert headers["cache-control"] == "no-store" and headers["vary"] == "Cookie"
    assert "GET /api/kommo_leads" in _message(body)
    assert up.calls == [], "a refused method must not reach the hook"


def test_the_log_is_the_request_line_only(monkeypatch, capsys):
    """With the unlock cookie on the request: a log line that carried the headers would put
    a cookie good for 4 hours of full contact details into the function log."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", CONTACT_PASSWORD)
    _ok(monkeypatch)
    unlock = _unlocked()
    status, _, body = _call("GET", "/api/kommo_leads?scope=all&page=2",
                            cookie=f"spb_session=1.session-value-7d2e; {unlock}")
    # The cookie was read (the full view), so it was there to be logged.
    assert status == 200 and json.loads(body)["contact"] == "full"
    out = capsys.readouterr()
    logged = out.err + out.out
    # Proves the capture saw the handler's log at all, so the absence below means something.
    assert "[api/kommo_leads]" in out.err and "/api/kommo_leads?scope=all&page=2" in out.err
    assert unlock.split("=", 1)[1] not in logged and "spb_contact" not in logged
    assert "session-value-7d2e" not in logged and "Cookie" not in logged
    assert KEY not in logged and _leaks(logged.encode()) == []


# ------------------------------------------------------------------ the build ships it

def test_the_build_bundles_configures_and_probes_it():
    import build_web

    assert "kommo_leads.py" in build_web._FUNCTION_ENTRY
    src = (APP / "build_web.py").read_text(encoding="utf-8")
    entry = src[src.index('"api/kommo_leads.py":'):]
    entry = entry[:entry.index("},") + 1]
    assert '"maxDuration": 30' in entry, "a 25 s upstream timeout needs a 30 s function"
    assert "api/_contact.py" in entry and "api/hot_leads.py" in entry, \
        "it imports both at module level: without either, every request is a 500"
    assert "kommo_leads" in build_web._READONLY_PROBE.split("import refresh,")[1].split("\n")[0]
