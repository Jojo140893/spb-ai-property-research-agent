"""POST /api/lead_notes -- the team's notes and saved links per Clare lead, via the hook.

Coleen, 28 Sep 2026: "Please add a free-text area where we can record what action has been
taken on each lead ... It would also be useful to have a field where we can paste a Google
Drive link or other relevant property link for future reference."

The proxy carries SPB_SERVICE_KEY exactly as api/hot_leads.py does, so it is held to the same
rules: the key goes upstream, over https, on no redirect, and nowhere else; a missing setting
is a 503 that names it, not a 502 to refresh past; nothing is ever a 401, which the tab reads
as "your sign-in expired". And the answer is masked unless the contact-details password was
given, because a note can hold a phone number as easily as a lead's summary can.

NOTHING HERE TOUCHES THE NETWORK: urlopen is stubbed and any real socket raises.
"""
import email.message
import io
import json
import socket
import urllib.error
import urllib.request

import pytest

from api import lead_notes as N

KEY = "svc-test-key-not-the-real-one-7f3a"
PASSWORD = "contact-test-password-not-the-real-one"
REA = "https://www.realestate.com.au/property-house-qld-jubilee-143456789"
ANSWER = {"status": "success", "callId": "call-678", "notes": {
    "entries": [{"id": "e1", "at": "2026-09-28T02:00:00.000Z", "by": "Coleen",
                 "action": "follow_up",
                 "text": "Follow up 2026-10-02 on 0400 111 678 or joseph.test@example.com"}],
    "links": [{"id": "l1", "at": "2026-09-28T02:01:00.000Z", "by": "Coleen", "url": REA,
               "label": "Lot 12"},
              {"id": "l2", "at": "2026-09-28T02:02:00.000Z", "by": "Coleen",
               "url": "https://wa.me/61400111678", "label": "chat"}],
    "rev": 2}}
ADD = {"callId": "call-678", "op": "add_entry", "action": "follow_up",
       "text": "Follow up 2026-10-02", "by": "Coleen"}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test tried to open a real network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    for name in ("SPB_SERVICE_KEY", "SPB_LEAD_NOTES_URL", "SPB_CONTACT_PASSWORD",
                 "SPB_HOT_LEADS_URL"):
        monkeypatch.delenv(name, raising=False)


class _Resp:
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
    def __init__(self, respond):
        self.respond, self.calls = respond, []

    def __call__(self, req, timeout=None):
        self.calls.append((req, timeout))
        return self.respond(req)


def _install(monkeypatch, respond):
    up = _Upstream(respond)
    monkeypatch.setattr(urllib.request, "urlopen", up)
    return up


def _ok(monkeypatch, body=None):
    raw = json.dumps(ANSWER if body is None else body).encode()
    return _install(monkeypatch, lambda req: _Resp(raw))


def _http_error(code, body):
    def respond(req):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        raise urllib.error.HTTPError(req.full_url, code, "upstream", {}, io.BytesIO(payload))
    return respond


def _call(method="POST", body=ADD, raw=None, ctype="application/json", cookie=None,
          length=None):
    data = raw if raw is not None else json.dumps(body).encode()
    h = N.handler.__new__(N.handler)
    h.command, h.path, h.request_version = method, "/api/lead_notes", "HTTP/1.1"
    h.requestline = f"{method} /api/lead_notes HTTP/1.1"
    h.client_address = ("127.0.0.1", 0)
    h.headers = email.message.Message()
    if ctype is not None:
        h.headers["Content-Type"] = ctype
    h.headers["Content-Length"] = str(len(data) if length is None else length)
    if cookie:
        h.headers["Cookie"] = cookie
    h.rfile, h.wfile = io.BytesIO(data), io.BytesIO()
    getattr(h, "do_" + method)()
    head, _, out = h.wfile.getvalue().partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {k.strip().lower(): v.strip()
               for k, v in (line.split(":", 1) for line in lines[1:] if ":" in line)}
    return int(lines[0].split()[1]), headers, out


def _message(body):
    parsed = json.loads(body)
    assert parsed["status"] == "error"
    return parsed["message"]


def _unlocked():
    return N._contact.unlock_cookie().split(";")[0]


# ------------------------------------------------------------------ settings

def test_no_key_on_the_dashboard_is_a_503_and_nothing_is_sent(monkeypatch):
    up = _ok(monkeypatch)
    status, _, body = _call()
    assert status == 503
    assert _message(body) == "SPB_SERVICE_KEY is not set on the dashboard project"
    assert up.calls == []


@pytest.mark.parametrize("url", ["http://vapi-kommo-hook.vercel.app/api/lead-notes",
                                 "file:///etc/passwd", "lead-notes", "https://"])
def test_a_non_https_upstream_is_refused_before_the_key_is_attached(monkeypatch, url):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_LEAD_NOTES_URL", url)
    up = _ok(monkeypatch)
    status, _, body = _call()
    assert status == 503 and "https" in _message(body)
    assert up.calls == []


# ------------------------------------------------------------------ the request

def test_the_note_goes_to_the_hook_with_the_key_and_nothing_else(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up = _ok(monkeypatch)
    assert _call(cookie="spb_session=abc")[0] == 200
    (req, timeout), = up.calls
    assert req.full_url == N.DEFAULT_URL == "https://vapi-kommo-hook.vercel.app/api/lead-notes"
    assert req.get_method() == "POST" and timeout == 20
    assert json.loads(req.data) == ADD
    sent = {k.lower(): v for k, v in req.header_items()}
    assert sent["x-spb-service-key"] == KEY
    assert sent["content-type"] == "application/json"
    assert "cookie" not in sent, "the browser's cookies are not the hook's business"


def test_the_key_is_not_carried_onto_a_redirect(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up = _ok(monkeypatch)
    _call()
    req = up.calls[0][0]
    followed = urllib.request.HTTPRedirectHandler().redirect_request(
        req, io.BytesIO(), 302, "Found", email.message.Message(),
        "https://elsewhere.example/collect")
    assert followed is not None, "the stdlib would follow this redirect"
    assert all(k.lower() != "x-spb-service-key" for k, _ in followed.header_items()), \
        "the service key would be handed to the redirect's host"


def test_SPB_LEAD_NOTES_URL_overrides_the_default(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_LEAD_NOTES_URL", "https://hook.example/api/lead-notes#frag")
    up = _ok(monkeypatch)
    assert _call()[0] == 200
    assert up.calls[0][0].full_url == "https://hook.example/api/lead-notes"


@pytest.mark.parametrize("ctype", [None, "text/plain", "application/x-www-form-urlencoded",
                                   "multipart/form-data"])
def test_only_json_is_forwarded(monkeypatch, ctype):
    """A cross-site form can post text/plain or a form; it cannot post application/json."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up = _ok(monkeypatch)
    status, _, _ = _call(ctype=ctype)
    assert status == 415 and up.calls == []


def test_json_with_a_charset_is_json(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _ok(monkeypatch)
    assert _call(ctype="application/json; charset=utf-8")[0] == 200


@pytest.mark.parametrize("raw,length,code", [
    (b"", 0, 400),
    (b"not json", None, 400),
    (b"[1]", None, 400),
    (b"{}", "abc", 400),
    (b"{" + b" " * (16 * 1024) + b"}", None, 413),
])
def test_a_body_it_does_not_take_never_reaches_the_hook(monkeypatch, raw, length, code):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up = _ok(monkeypatch)
    status, _, body = _call(raw=raw, length=length)
    assert status == code and _message(body)
    assert up.calls == []


# ------------------------------------------------------------------ the answer

def test_the_masked_answer_hides_numbers_and_emails_but_keeps_the_links(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _ok(monkeypatch)
    status, headers, body = _call()
    assert status == 200
    text = body.decode()
    for leak in ("0400 111 678", "61400111678", "joseph.test@example.com"):
        assert leak not in text, leak
    doc = json.loads(body)
    assert doc["contact"] == "masked" and doc["callId"] == "call-678"
    entry, = doc["notes"]["entries"]
    assert entry["text"] == "Follow up 2026-10-02 on [hidden] or [hidden]"
    assert entry["at"] == "2026-09-28T02:00:00.000Z"
    rea, wa = doc["notes"]["links"]
    assert rea["url"] == REA and wa["url"] == "[hidden]"
    assert headers["vary"] == "Cookie" and headers["cache-control"] == "no-store"


def test_the_absence_check_sees_a_leak_when_the_masking_is_switched_off(monkeypatch):
    monkeypatch.setattr(N._contact, "mask_notes_answer", lambda doc: doc)
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _ok(monkeypatch)
    assert "0400 111 678" in _call()[2].decode()


def test_the_password_holder_gets_the_notes_as_written(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", PASSWORD)
    _ok(monkeypatch)
    status, _, body = _call(cookie=_unlocked())
    assert status == 200
    assert json.loads(body) == {**ANSWER, "contact": "full"}


# ------------------------------------------------------------------ review fixes, 28 Sep 2026
# The hook's answer now carries the lead's own details, "contact": {"email", "phone"}, read
# from the lead record it checks anyway, so a save is masked with the lead's rules exactly as
# GET /api/hot_leads masks the same notes -- and the block itself never reaches the browser.

LEAD_CONTACT = {"email": "joseph.shahid@example.com", "phone": "+61 400 111 678"}
RID = "r-20260928-12345678-ab"
OWN_NOTES = {
    "entries": [
        {"id": "e1", "at": "2026-09-28T02:00:00.000Z", "by": "Coleen", "action": "other",
         "text": "Emailed joseph dot shahid at example dot com", "requestId": RID},
        {"id": "e2", "at": "2026-09-28T02:01:00.000Z", "by": "Coleen", "action": "other",
         "text": "Texted 0400/111/678, then rang 0400 111 678"}],
    "links": [{"id": "l1", "at": "2026-09-28T02:02:00.000Z", "by": "Coleen", "url": REA,
               "label": "Lot 12 for joseph_shahid at example"}],
    "rev": 3}
WITH_CONTACT = {"status": "success", "callId": "call-678", "notes": OWN_NOTES,
                "contact": LEAD_CONTACT}


def _get_view(notes):
    """The same notes as GET /api/hot_leads shows them to a masked viewer, beside the lead."""
    lead = {"callId": "call-678", "customer": {"name": "Joseph Shahid", **LEAD_CONTACT}}
    code, body = N._hl.view(json.dumps({"status": "success", "count": 1, "leads": [lead],
                                        "notes": {"call-678": notes},
                                        "notesErrors": []}).encode(), "")
    assert code == 200, body
    return json.loads(body)["notes"]["call-678"]


def test_a_masked_save_hides_the_leads_own_spellings_exactly_as_reading_does(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _ok(monkeypatch, WITH_CONTACT)
    status, _, body = _call()
    assert status == 200, body
    doc = json.loads(body)
    assert doc["contact"] == "masked", "the view, never the hook's block of the lead's details"
    assert [e["text"] for e in doc["notes"]["entries"]] == [
        "Emailed [hidden]", "Texted [hidden], then rang [hidden]"]
    assert doc["notes"]["links"][0]["label"] == "Lot 12 for [hidden]"
    assert doc["notes"]["links"][0]["url"] == REA
    assert doc["notes"]["entries"][0]["requestId"] == RID
    assert doc["notes"] == _get_view(OWN_NOTES), "saving shows no more than reading"
    text = body.decode()
    for leak in ("joseph dot shahid", "joseph_shahid", "joseph.shahid@example.com",
                 "0400/111/678", "0400 111 678", "+61 400 111 678"):
        assert leak not in text, leak


def test_the_password_holder_never_gets_the_contact_block_either(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", PASSWORD)
    _ok(monkeypatch, WITH_CONTACT)
    status, _, body = _call(cookie=_unlocked())
    assert status == 200
    assert json.loads(body) == {**{k: v for k, v in WITH_CONTACT.items() if k != "contact"},
                                "contact": "full"}
    assert "joseph.shahid@example.com" not in body.decode()


def test_an_older_hook_with_no_contact_block_gets_the_generic_rules(monkeypatch):
    """Before the hook sent "contact": any address and any 8+ digit phone-shaped run go, and
    the lead's other spellings wait for GET, which has the lead in hand. Not a 502: the note
    was saved and its answer is safe on the generic rules."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _ok(monkeypatch, {k: v for k, v in WITH_CONTACT.items() if k != "contact"})
    status, _, body = _call()
    assert status == 200, body
    doc = json.loads(body)
    assert doc["contact"] == "masked"
    assert doc["notes"]["entries"][1]["text"].endswith("then rang [hidden]")
    assert "0400 111 678" not in body.decode()


# Since the second review (28 Sep 2026) an answer the masking cannot make safe is no longer a
# 502 for a note the hook had saved: a 200 that says the save went through, with the notes
# left out (notesHidden) and nothing else of the hook's answer.
HIDDEN_ANSWER = {"status": "success", "callId": "call-678", "notesHidden": True,
                 "contact": "masked"}
# Every spelling of the lead's details the answers below carry, looked for in the raw bytes.
LEAD_SPELLINGS = ("0400 111 678", "0400/111/678", "then 678", "joseph.shahid@example.com",
                  "joseph dot shahid", "joseph_shahid", "+61 400 111 678")


def _hides_the_notes(body):
    assert json.loads(body) == HIDDEN_ANSWER
    text = body.decode()
    assert [s for s in LEAD_SPELLINGS if s in text] == []


@pytest.mark.parametrize("contact", ["joseph.shahid@example.com", ["x"], 5, False])
def test_a_contact_block_that_is_not_an_object_hides_the_notes(monkeypatch, contact):
    """A contact block this file does not know cannot give the lead's rules, so the notes
    are not sent -- and the block itself, whatever it holds, is not sent either."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _ok(monkeypatch, dict(WITH_CONTACT, contact=contact))
    status, _, body = _call()
    assert status == 200, body
    _hides_the_notes(body)


def test_a_saved_number_written_past_every_pattern_hides_the_notes(monkeypatch):
    """The second look, on this path too: the digits with every non-digit removed."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    notes = dict(OWN_NOTES, entries=[dict(OWN_NOTES["entries"][0],
                                          text="ring 0400 or 111 then 678")])
    _ok(monkeypatch, dict(WITH_CONTACT, notes=notes))
    status, _, body = _call()
    assert status == 200, body
    _hides_the_notes(body)


def test_the_second_look_covers_the_whole_answer_not_only_the_notes(monkeypatch):
    """A field the hook may add later, beside the notes, is looked at too -- and is not
    sent once the answer is found unsafe."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _ok(monkeypatch, dict(WITH_CONTACT, note="ring 0400 or 111 then 678"))
    status, _, body = _call()
    assert status == 200, body
    _hides_the_notes(body)


def _deep(value, depth=1200):
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize("notes", [
    "x", {"entries": [1]}, {"entries": "x"},
    # Not only MaskError: nested past Python's recursion limit, the walk raises
    # RecursionError, and that too hides the notes rather than failing the answer.
    {"entries": [{"id": "e1", "text": _deep("0400 111 678")}], "links": [], "rev": 1},
])
def test_notes_the_masking_does_not_know_the_shape_of_are_hidden_in_a_200(monkeypatch, notes):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _ok(monkeypatch, dict(WITH_CONTACT, notes=notes))
    status, _, body = _call()
    assert status == 200, body
    _hides_the_notes(body)


def test_the_hidden_answer_is_shown_able_to_fail(monkeypatch):
    """A silent check reads as a pass: with the notes-hidden answer replaced by the hook's
    own, the same search over the same bytes finds the number."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setattr(N._contact, "notes_hidden_answer", lambda doc: dict(doc))
    notes = dict(OWN_NOTES, entries=[dict(OWN_NOTES["entries"][0],
                                          text="ring 0400 or 111 then 678")])
    _ok(monkeypatch, dict(WITH_CONTACT, notes=notes))
    status, _, body = _call()
    assert status == 200 and "then 678" in body.decode()


@pytest.mark.parametrize("flags", [{"duplicate": True},
                                   {"duplicate": True, "duplicateRemoved": True}])
def test_the_hooks_duplicate_flags_are_passed_on_in_every_answer(monkeypatch, flags):
    """The hook's word that the Save was a click it had already stored (and whether that
    note has since been removed), so the tab does not draw it twice: unchanged in both
    views, and in the answer that hides the notes."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", PASSWORD)
    _ok(monkeypatch, dict(WITH_CONTACT, **flags))
    for cookie, view in ((None, "masked"), (_unlocked(), "full")):
        status, _, body = _call(cookie=cookie)
        doc = json.loads(body)
        assert status == 200 and doc["contact"] == view, view
        assert {k: doc.get(k) for k in ("duplicate", "duplicateRemoved")} == {
            "duplicate": True, "duplicateRemoved": flags.get("duplicateRemoved")}, view
        assert "notes" in doc and "notesHidden" not in doc, view
    _ok(monkeypatch, dict(WITH_CONTACT, contact=5, **flags))
    status, _, body = _call()
    assert status == 200 and json.loads(body) == {**HIDDEN_ANSWER, **flags}


@pytest.mark.parametrize("upstream", [ANSWER, WITH_CONTACT,
                                      {k: v for k, v in WITH_CONTACT.items() if k != "contact"},
                                      dict(WITH_CONTACT, contact=5)])
def test_every_answer_says_which_view_it_was_built_in(monkeypatch, upstream):
    """"contact" at the top is "masked" or "full", as GET /api/hot_leads says it -- never the
    hook's block of the lead's own details."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", PASSWORD)
    _ok(monkeypatch, upstream)
    for cookie, view in ((None, "masked"), (_unlocked(), "full")):
        status, _, body = _call(cookie=cookie)
        assert status == 200, (view, body)
        assert json.loads(body)["contact"] == view
        assert "joseph.shahid@example.com" not in body.decode(), view


def test_half_a_surrogate_pair_in_a_note_is_not_a_502_in_either_view(monkeypatch):
    """JSON.stringify writes "\\ud83d" for an emoji a .slice() cut in half; json.loads reads it
    back, and UTF-8 cannot write it. It is sent as U+FFFD, in both views."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", PASSWORD)
    entry = dict(ANSWER["notes"]["entries"][0], text="Keen " + chr(0xD83D))
    _ok(monkeypatch, dict(ANSWER, notes=dict(ANSWER["notes"], entries=[entry])))
    for cookie, view in ((None, "masked"), (_unlocked(), "full")):
        status, _, body = _call(cookie=cookie)
        assert status == 200, (view, body)
        doc = json.loads(body.decode("utf-8"))
        assert doc["notes"]["entries"][0]["text"] == "Keen " + chr(0xFFFD), view


def test_a_request_id_goes_to_the_hook_as_the_tab_sent_it(monkeypatch):
    """The tab's random id per Save or Add click, reused on a retry of the same click: the
    hook needs it to tell a retry from a second note, so the body check passes it on."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up = _ok(monkeypatch)
    for body in ({**ADD, "requestId": "k3J-9_x2"},
                 {"callId": "call-678", "op": "add_link", "url": REA, "requestId": RID}):
        assert _call(body=body)[0] == 200
        assert json.loads(up.calls[-1][0].data) == body


def test_the_notes_follow_an_overridden_leads_address(monkeypatch):
    """Leads read from a staging hook had their notes written to production, which answered
    "no such lead"."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_HOT_LEADS_URL",
                       "https://staging-hook.example/api/hot-leads?region=vic#top")
    up = _ok(monkeypatch)
    assert _call()[0] == 200
    assert up.calls[0][0].full_url == "https://staging-hook.example/api/lead-notes"


def test_SPB_LEAD_NOTES_URL_still_wins_over_the_leads_address(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_HOT_LEADS_URL", "https://staging-hook.example/api/hot-leads")
    monkeypatch.setenv("SPB_LEAD_NOTES_URL", "https://notes.example/api/lead-notes")
    up = _ok(monkeypatch)
    assert _call()[0] == 200
    assert up.calls[0][0].full_url == "https://notes.example/api/lead-notes"


@pytest.mark.parametrize("url", ["http://staging-hook.example/api/hot-leads", "hot-leads"])
def test_a_non_https_leads_address_is_refused_for_the_notes_too(monkeypatch, url):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_HOT_LEADS_URL", url)
    up = _ok(monkeypatch)
    status, _, body = _call()
    assert status == 503 and up.calls == [], "the key must not be sent in the clear"
    msg = _message(body)
    assert "SPB_HOT_LEADS_URL" in msg and "https" in msg


@pytest.mark.parametrize("upstream", [
    [1], "saved", 5, None,
    {"callId": "call-678", "notes": ANSWER["notes"]},               # no status
])
def test_an_answer_of_a_shape_the_hook_does_not_send_is_still_a_502(monkeypatch, upstream):
    """Not an object, or no status: not a lead-notes answer at all. A 502 in both views,
    never the body."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", PASSWORD)
    raw = json.dumps(upstream).encode()                  # _ok would read None as ANSWER
    _install(monkeypatch, lambda req: _Resp(raw))
    for cookie in (None, _unlocked()):
        status, _, body = _call(cookie=cookie)
        assert status == 502 and "Refresh" in _message(body)
        assert "0400 111 678" not in body.decode()


# ------------------------------------------------------------------ hook failures

@pytest.mark.parametrize("code,message", [
    (400, "text is over 2000 characters"),
    (404, "no such lead"),
    (409, "the notes changed while saving; try again"),
    (413, "this lead has 200 entries"),
    (415, "send JSON"),
])
def test_a_request_the_hook_refused_is_passed_on_with_its_message(monkeypatch, code, message):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, _http_error(code, {"status": "error", "message": message}))
    status, _, body = _call()
    assert status == code and _message(body) == message


def test_a_hook_message_is_scrubbed_of_contact_details(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, _http_error(400, {"status": "error",
                                            "message": "bad url https://wa.me/0400111678"}))
    status, _, body = _call()
    assert status == 400 and "0400111678" not in body.decode()


def test_a_bare_404_is_the_route_missing_not_the_lead(monkeypatch):
    """Vercel's own NOT_FOUND page: the hook deployed without /api/lead-notes."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, _http_error(404, b"The page could not be found\n\nNOT_FOUND\n"))
    status, _, body = _call()
    msg = _message(body)
    assert status == 503 and "/api/lead-notes" in msg and "vapi-kommo-hook project" in msg


@pytest.mark.parametrize("code,message,names", [
    (401, "bad key", "does not match"),
    (401, None, "deployment protection"),
    (500, "SPB_SERVICE_KEY not configured", "SPB_SERVICE_KEY"),
    (500, "HOOK_SERVICE_KEY not configured", "SPB_SERVICE_KEY"),
    (503, "lead-notes store not configured", "BLOB_READ_WRITE_TOKEN"),
    (503, "BLOB_READ_WRITE_TOKEN not set", "BLOB_READ_WRITE_TOKEN"),
    (500, None, "SPB_SERVICE_KEY or BLOB_READ_WRITE_TOKEN"),
])
def test_a_setting_missing_on_the_hook_is_a_503_naming_it(monkeypatch, code, message, names):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, _http_error(code, {"status": "error", "message": message}))
    status, _, body = _call()
    msg = _message(body)
    assert status == 503 and names in msg and "vapi-kommo-hook project" in msg, msg
    assert KEY not in body.decode()


@pytest.mark.parametrize("code", [400, 401, 403, 404, 405, 409, 413, 415, 429, 500, 502, 503,
                                  504])
@pytest.mark.parametrize("message", [None, "bad key", "not configured"])
def test_no_upstream_answer_becomes_a_401_from_this_endpoint(monkeypatch, code, message):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, _http_error(code, {"status": "error", "message": message}))
    status, _, _ = _call()
    assert status != 401


@pytest.mark.parametrize("exc", [TimeoutError("timed out"),
                                 urllib.error.URLError("connection refused"),
                                 ConnectionResetError(104, "reset")])
def test_a_network_failure_is_a_502(monkeypatch, exc):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)

    def respond(req):
        raise exc
    _install(monkeypatch, respond)
    status, _, body = _call()
    assert status == 502 and "could not reach" in _message(body)


def test_a_200_that_is_not_json_is_a_502(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, lambda req: _Resp(b"<!doctype html><title>Log in</title>"))
    status, _, body = _call()
    assert status == 502 and "not JSON" in _message(body)


def test_an_upstream_error_quoting_the_key_does_not_pass_it_on(monkeypatch, capsys):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, _http_error(400, {"status": "error", "message": f"bad {KEY}"}))
    _, _, body = _call()
    out = capsys.readouterr()
    assert KEY not in body.decode() and KEY not in out.err and KEY not in out.out
    assert "[api/lead_notes]" in out.err


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "PATCH"])
def test_only_POST_is_served(monkeypatch, method):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up = _ok(monkeypatch)
    status, headers, _ = _call(method)
    assert status == 405 and headers.get("allow") == "POST" and up.calls == []
