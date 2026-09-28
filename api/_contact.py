"""Who may see a Clare caller's full contact details, and what everyone else is shown.

Coleen, 28 Sep 2026: "Please only display the last 3 digits of the customer's mobile number
for identification purposes. Not everyone on the team should have access to the customer's
full email address or phone number." The owner chose the rule: AUTHORISED is whoever knows a
second password, SPB_CONTACT_PASSWORD, set on the dashboard's Vercel project. The site login
lets the whole team into the dashboard; this password lets one person see who to ring.

Used by three functions, and bundled beside each of them (build_web.py includeFiles):
    api/contact_unlock.py  checks the password and sets or clears the unlock cookie
    api/hot_leads.py       masks the leads unless the request carries a valid cookie
    api/lead_notes.py      masks the notes it hands back, on the same rule

WHY ON THE SERVER AND NOT IN THE PAGE. A number the page hides with CSS or script has still
been sent to the browser: the Network tab, View Source or a saved copy of the JSON reads it in
full. So the masking happens here, before the bytes leave Vercel, and a masked viewer's
browser never receives the number at all.

THE UNLOCK COOKIE, spb_contact=<exp>.<b64url HMAC-SHA256(SPB_CONTACT_PASSWORD, "contact|"+exp)>
  * SIGNED WITH THE PASSWORD ITSELF, as middleware.js signs the site session with
    SITE_PASSWORD: one setting to configure, and changing it ends every unlock at once.
  * "contact|" is a different message from the site session's "<user>|<exp>", so neither
    cookie can ever be replayed as the other, even if the two passwords were the same.
  * Path=/api: only the functions read it; the page, stock.json and the PDFs never carry it.
  * HttpOnly (script cannot read it), Secure, SameSite=Strict, and 4 hours: a shared laptop
    should ask again the same working day.
  * AN EMPTY OR UNSET PASSWORD NEVER VALIDATES. Anyone can compute an HMAC under an empty key,
    so with no password there is nothing to unlock with and everyone gets the masked view.

WHAT THE MASKED VIEW HIDES (mask_hot_leads, mask_notes_answer). Fail closed throughout: a shape
this file does not recognise is an error the caller turns into a 502, never the raw body; ONE
lead, or one lead's notes, that cannot be made safe -- for whatever reason, any exception --
is cut down or left out on its own (hard_masked, notesHidden), so it never takes the other
leads with it.
  * customer.email and customer.phone become "", with phoneLast3 (the last 3 digits, only when
    the number has at least 6) and hasEmail / hasPhone in their place.
  * recordingUrl becomes "": the recording is the caller saying their own number.
  * Every free-text string -- summary, gradeReason, brief.*, research.message, the research
    location readings, customer.name, the notes' text, label and by, and any key this file
    does not know -- loses the lead's own email and phone in every spelling (see Scrubber),
    any email-shaped token, and any run of 8 or more digits shaped like a phone number.
    The two NAME fields (customer.name, brief.client_name) skip only the email's said-aloud
    spellings: see Scrubber.name.
  * Machine-written fields (ids, timestamps, the Kommo link, the shortlist's listings, the
    appointment) lose only the lead's own email and phone. The generic digit rule would break
    them: a Kommo lead id is 8 digits and an ISO timestamp is 8 digits with dashes.
  * A saved link is kept whole or hidden whole (see _scrub_url).
  * Before any of it, every string loses the characters that draw nothing, and is read in its
    compatibility form with ASCII digits (see plain()), so a zero-width space or a Hangul
    filler pasted into a number or an address, a full-width "@" or Arabic-Indic digits cannot
    walk it past the rules.
"""
import base64
import hashlib
import hmac
import os
import re
import time
import unicodedata
import urllib.parse

COOKIE = "spb_contact"
MAX_AGE = 4 * 60 * 60                       # 14400 s
# A correctly signed cookie that claims to last longer than MAX_AGE was never minted here.
# The slack allows for the clocks of two function instances disagreeing by a few minutes.
_FUTURE_SLACK = 300
HIDDEN = "[hidden]"
_EXP = re.compile(r"[0-9]{1,12}")           # ASCII digits only, like hot_leads' ?limit=


class MaskError(Exception):
    """The answer is not a shape this file can mask. The caller answers 502, never the raw body."""


def secret():
    return (os.environ.get("SPB_CONTACT_PASSWORD") or "").strip()


def configured():
    return bool(secret())


# ------------------------------------------------------------------ the unlock cookie

def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign(key, exp):
    """The cookie's signature, byte-compatible with middleware.js's sign()."""
    mac = hmac.new(key.encode("utf-8"), ("contact|" + exp).encode("utf-8"), hashlib.sha256)
    return _b64url(mac.digest())


def unlock_cookie(now=None):
    """The Set-Cookie value for a correct password. Only ever called with one configured."""
    key = secret()
    if not key:
        raise RuntimeError("SPB_CONTACT_PASSWORD is not set")
    exp = str(int(time.time() if now is None else now) + MAX_AGE)
    return (f"{COOKIE}={exp}.{sign(key, exp)}; Path=/api; HttpOnly; Secure; SameSite=Strict; "
            f"Max-Age={MAX_AGE}")


def lock_cookie():
    """The Set-Cookie value that clears the unlock. Same Path, or the browser keeps the old one."""
    return f"{COOKIE}=; Path=/api; HttpOnly; Secure; SameSite=Strict; Max-Age=0"


def cookie_values(header, name=COOKIE):
    """Every value the Cookie header carries under `name`, in order (the way readCookie reads)."""
    out = []
    for part in str(header or "").split(";"):
        k, eq, v = part.partition("=")
        if eq and k.strip() == name:
            out.append(v.strip())
    return out


def authorised(cookie_header, now=None):
    """True only for an unexpired spb_contact cookie signed with the configured password."""
    key = secret()
    if not key:
        return False                          # an empty key is a key anyone holds
    now = time.time() if now is None else now
    for raw in cookie_values(cookie_header)[:4]:
        exp, dot, sig = raw.rpartition(".")
        if not dot or not _EXP.fullmatch(exp):
            continue
        seconds = int(exp)
        if seconds <= now or seconds > now + MAX_AGE + _FUTURE_SLACK:
            continue
        # Bytes, not str: compare_digest raises on a str that is not pure ASCII, and a
        # cookie is whatever the browser, or someone forging one, chose to send.
        if hmac.compare_digest(sig.encode("utf-8", "replace"), sign(key, exp).encode("ascii")):
            return True
    return False


# ------------------------------------------------------------------ scrubbing

# 28 Sep 2026 (review): INVISIBLE CHARACTERS. A number or an address copied out of WhatsApp,
# Word or a web page can carry a zero-width space (U+200B), a word joiner (U+2060), a
# left-to-right mark (U+200E), a soft hyphen (U+00AD), a byte-order mark (U+FEFF) or a bidi
# control. None of them shows on screen and every one of them breaks a pattern: "0400", an
# invisible U+200B, "111 678" was two short numbers to the rules below and one number to
# whoever read the tab. The hook strips the same characters (Unicode category Cf) from what
# it stores -- the rule the two projects share -- and this is the server's copy of it, run on
# every string before any pattern here sees it, so a record stored before the hook's rule is
# masked the same way.
_ZWJ = chr(0x200D)                    # kept ASCII: see _SEP_CHARS
# The one format character kept: the zero-width joiner holds an emoji together (a family, a
# flag) and a note may carry one. Anywhere else it joins nothing a note needs, so there it
# goes too: "joseph.test@exam", a U+200D, "ple.com" looks exactly like the address and would
# match none of the rules (_on_an_emoji, below).
# The other characters Unicode says draw nothing (Default_Ignorable_Code_Point) that are not
# format characters, so the rule above let them through (28 Sep 2026, review): the combining
# grapheme joiner, the Hangul fillers -- which are LETTERS, so "0400", U+3164, "111" was two
# numbers to every rule here -- the Khmer inherent vowels, the Mongolian variation selectors,
# and the unassigned ranges kept for more of the same. Written as ranges, not a category:
# Python's unicodedata has no Default_Ignorable property. The variation selectors U+FE00-FE0F
# are kept, as the joiner is, because an emoji needs one ("red heart" is U+2764 U+FE0F); and,
# as the joiner, only on an emoji.
_IGNORABLE = frozenset(
    [0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180F, 0x2065, 0x3164,
     0xFFA0] + list(range(0xFFF0, 0xFFF9)) + list(range(0xE0000, 0xE1000)))
_VS = "".join(chr(c) for c in range(0xFE00, 0xFE10))
# 28 Sep 2026 (second review): ONLY ON AN EMOJI. The joiner and the selectors were kept
# wherever one neighbour was not ASCII, so one between a no-break space and a digit ("0413",
# NBSP, U+200D, "222"), after an accented letter in an address ("jo@ex" + a-umlaut + U+200D
# + "mple.com") or between two Arabic-Indic digits split the number or the address for every
# rule here, and drew nothing. They are kept now only where an emoji needs them: after a
# pictograph (a symbol, a skin tone, anything from U+1F000 up), before the keycap mark
# (a selector: "3", U+FE0F, U+20E3), or before a pictograph (the joiner: the rainbow flag is
# U+1F3F3 U+FE0F U+200D U+1F308, so its joiner follows a selector).
_JOINERS = re.compile("[" + _ZWJ + _VS + "]")
_KEYCAP = chr(0x20E3)
# The Braille blank draws a space and is not one to any rule (a symbol, category So, which
# the joiner rule above would also read as a pictograph): "0498", U+2800, "765" is a space.
_BRAILLE_BLANK = chr(0x2800)
_REPLACEMENT = chr(0xFFFD)
_OTHER_DIGIT = re.compile(r"(?![0-9])\d")    # a decimal digit (category Nd) that is not ASCII


def _drawn(ch):
    """`ch`, or "" for a character that draws nothing, or U+FFFD for half a surrogate pair.

    28 Sep 2026 (second review): a lone surrogate -- JSON.stringify writes one as "\\ud83d"
    when a .slice() cuts an emoji in half -- parses, but cannot be written as UTF-8, and one in
    a summary was a 502 for the whole tab. U+FFFD is what a browser draws for it anyway."""
    cat = unicodedata.category(ch)
    if cat == "Cs":
        return _REPLACEMENT
    if ch == _ZWJ or ch in _VS:
        return ch                            # decided by its neighbours: see _on_an_emoji
    return "" if cat == "Cf" or ord(ch) in _IGNORABLE else ch


def _pictograph(ch):
    return ch > "\x7f" and (ord(ch) >= 0x1F000 or unicodedata.category(ch) in ("So", "Sk"))


def _on_an_emoji(m):
    """A joiner or a selector, kept only where an emoji needs it (_JOINERS, above)."""
    text, i, ch = m.string, m.start(), m.group()
    after = text[i + 1:i + 2]
    if _pictograph(text[i - 1:i]) or (after == _KEYCAP if ch in _VS else _pictograph(after)):
        return ch
    return ""


def _ascii_digit(m):
    return str(unicodedata.decimal(m.group()))


def plain(text):
    """`text` as the rules below read it: without the characters that draw nothing (Unicode
    format characters, category Cf, and the rest of Default_Ignorable_Code_Point, except an
    emoji's joiner and variation selectors), in its compatibility form (NFKC), and with every
    decimal digit written as an ASCII one.

    28 Sep 2026 (second review): NFKC and the digits. Typed in a CJK input method's
    full-width mode, or pasted from WeChat, the lead's own address is "liwei.buyer", U+FF20,
    "gmail.com" and their number is written in full-width digits; a number can come in
    superscript or circled digits, or grouped with a thin space (U+2009), a narrow no-break
    space (U+202F) or an ideographic one (U+3000). Every rule here, and the second look
    behind them, read those as something else, and showed them whole. NFKC writes each as the
    character it stands for (an "@", a digit, a space). It leaves Arabic-Indic and the other
    scripts' digits alone, so those are written as ASCII digits after it (unicodedata.decimal),
    and the Braille blank, which NFKC keeps, becomes a space. A masked viewer is sent the
    text in this form; the full view is the hook's text untouched."""
    if text.isascii():
        return text                          # none of it applies to ASCII: the usual case
    text = unicodedata.normalize("NFKC", "".join(_drawn(ch) for ch in text))
    text = _OTHER_DIGIT.sub(_ascii_digit, text).replace(_BRAILLE_BLANK, " ")
    return _JOINERS.sub(_on_an_emoji, text) if _JOINERS.search(text) else text


# Between two digits of a phone number: a space (a no-break one too), a dash (any of the
# Unicode hyphens and dashes a phone's autocorrect produces), a dot or a bracket, up to three
# of them ("(03) 9123 4567", "0400-111-678", "+61 (0)400 111 678"). Not "/" or ",": a
# date written 14/09/2026 and a price written 750,000 are not phone numbers. Built with chr()
# so the source stays ASCII: an invisible no-break space in a regex is a trap for the next edit.
_SEP_CHARS = r" \t" + chr(0xA0) + r"\-.()" + "".join(chr(c) for c in range(0x2010, 0x2016))
_SEP = "[" + _SEP_CHARS + "]{0,3}"
# Between the digits of the LEAD'S OWN number (28 Sep 2026, review): anything that is not a
# letter or a digit, up to 10 of it -- "0400/111/678", "0400,111,678", the groups on separate
# lines (the hook keeps a note's line breaks), or spread out with tabs or a run of spaces.
# The rule above stays narrow because it guesses at ANYONE's number, where a date or a price
# is the risk; this one matches only the lead's own 9 digits in their order, and those with
# nothing but punctuation between them are that number. Bounded, so a note that is 16 KB of
# spaces is not a slow match.
_OWN_SEP = r"[\W_]{0,10}"
# 8 or more digits in a phone's shape. Not starting straight after a "$" or another digit, or
# one separator after one, so a budget ("$750 000 - 800 000") is never read as a number to
# hide, and not an ISO date ("2026-10-02"), which a note saying when a call is booked will
# often carry.
_PHONE_SHAPED = re.compile(
    r"(?<![\d$])(?<![\d$][" + _SEP_CHARS + r"])(?!\d{4}-[01]\d-[0-3]\d(?!\d))\+?\d(?:"
    + _SEP + r"\d){7,}")
# Tried only at the start of a run of address characters (28 Sep 2026, review): a match that
# starts inside a run can always start at the run's first character instead, so this finds
# the same addresses -- but without it, a 16 KB run with no "@" in it was tried from every
# one of its characters, and the time grew with the square of its length. Except straight
# after another address (28 Sep 2026, second review): "sam@law.com.au%2Cmary@gmail.com"
# (a mailto list pasted in), "a@b.com+c@d.com" or "a@b.com..c@d.com" -- the first address
# stops at the "%", "+" or second ".", which are address characters too, so the second
# address was inside a run and never tried. _emails_hidden tries _EMAIL_ANYWHERE once,
# exactly where each address ends, which is where the scan before the lookbehind went on.
_EMAIL_ANYWHERE = re.compile(r"[\w.%+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_EMAIL_SHAPED = re.compile(r"(?<![\w.%+\-])" + _EMAIL_ANYWHERE.pattern)
# In a URL, only a number in an Australian phone's own shape: national (0X XXXX XXXX),
# international (61X XXXX XXXX) or anything after a "+". A listing's id -- 9 digits on
# realestate.com.au, 10 starting 20 on domain.com.au -- and a Drive id's digits are not.
_URL_PHONE = re.compile(r"(?<!\d)(?:\+\d{8,15}|0[2-9]\d{8}|61[2-9]\d{8})(?!\d)")
# Between the parts of an email said aloud: "joseph dot test at example dot com".
_SAID = (r"(?:\s*(?:[.@_+\-]|\bdot\b|\bat\b|\bunderscore\b|\bdash\b|\bhyphen\b|\bplus\b)\s*"
         r"|\s+)?")
# Between the address's local part and its domain, said aloud: an explicit "at" or "@",
# bracketed or not ("joseph (at) example"). REQUIRED since 28 Sep 2026 (review): while it was
# optional, the address joseph@shahid.com.au read its owner's own name, "Joseph Shahid", as
# the address said aloud, and hid the name -- the one thing a masked viewer tells two callers
# apart by. Never two "\s*" side by side with only an optional bracket between them (28 Sep
# 2026, review): "joseph test" and 6,000 spaces took half a second, growing with the square
# of the run, because the spaces could be split between the two in every way. Any bracket,
# and a hyphen or an underscore, around the "at" (28 Sep 2026, second review): "{at}",
# "<at>", "-at-" and "_at_" are the obfuscations people type, and each walked the lead's own
# address past this rule and the second look. The "at" is a word of its own with "_" as a
# boundary too, which "\b" does not treat as one.
_AT = (r"\s*(?:[\[({<_\-]\s*)?(?:@|(?<![^\W_])at(?![^\W_]))(?:\s*[\])}>_\-])?\s*")


def digits(value):
    """The digits of `value`, as ASCII (plain): a number stored in full-width or Arabic-Indic
    digits is the same number to _phone_pattern, last3 and the second look."""
    return re.sub(r"\D", "", plain("" if value is None else str(value)))


def last3(phone):
    """The last 3 digits, for telling two callers apart -- only for a number of 6 digits or more."""
    d = digits(phone)
    return d[-3:] if len(d) >= 6 else ""


def _phone_pattern(phone):
    """Every written form of this number: +61 400 111 678, 0400111678, 400-111-678, ...

    The CORE is the last 9 digits (an Australian number without its 0 or 61), matched with
    any punctuation between them (_OWN_SEP); the optional prefix swallows the "+61 ", "0061",
    "(0)" or "0" in front, so what is left in the text is "[hidden]" rather than "+61 [hidden]".
    """
    d = digits(phone)
    if len(d) < 6:
        return None, ""                      # too short to be a number, and to hide safely
    core = d[-9:]
    pre = d[:-9].lstrip("0")
    prefix = r"(?:(?:\+|00)[ ]?\d{1,3}" + _OWN_SEP
    if pre:
        prefix += "|" + _OWN_SEP.join(re.escape(c) for c in pre) + _OWN_SEP
    prefix += r")?(?:\(?0\)?" + _OWN_SEP + ")?"
    return prefix + _OWN_SEP.join(re.escape(c) for c in core), core


def _is_address(email):
    return "@" in email and len(email) >= 3


# The words _SAID reads as the punctuation between an address's parts.
_SPOKEN = frozenset({"at", "dot", "underscore", "dash", "hyphen", "plus"})


def _email_patterns(email):
    """(written, said): the address as written, and as said aloud or with its punctuation lost."""
    e = str(email or "").strip()
    if not _is_address(e):
        return [], []
    local, _, domain = e.rpartition("@")
    words = [t for t in re.split(r"[\W_]+", local) if t]
    labels = [t for t in re.split(r"[\W_]+", domain) if t]
    said = []
    # Only for an address long enough that its spoken form cannot be an ordinary phrase:
    # with the separators optional, "a@b.co" would hide every "ab" in every summary. And only
    # with an explicit "at" and at least the domain's first label after it (_AT).
    # And only for an address a person could say (28 Sep 2026, second review): at most 254
    # characters (the most an address can have), 12 words before the "@" and 10 labels after
    # it, and none of those words one of the words _SAID reads as punctuation. An address
    # of 21 "at"s took 46 s to look for in a summary of 60 "at"s, every gap able to read the
    # next "at" either way; one of 500 labels nested too deep for re to compile, a 502 for
    # the whole tab. The address as written is still hidden, whatever it is.
    if (len(e) <= 254 and len(words) <= 12 and len(labels) <= 10
            and not any(w.lower() in _SPOKEN for w in words)
            and words and labels and len("".join(words) + labels[0]) >= 8):
        tail = ""
        for label in reversed(labels[1:]):   # "... at gmail" hides as surely as "gmail.com"
            tail = "(?:" + _SAID + re.escape(label) + tail + ")?"
        said.append(r"(?<!\w)" + _SAID.join(re.escape(w) for w in words) + _AT
                    + re.escape(labels[0]) + tail + r"(?!\w)")
    return [re.escape(e)], said


def _compiled(patterns):
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE) if patterns else None


def _emails_hidden(text):
    """Every email-shaped token in `text` as HIDDEN: the same tokens a plain
    _EMAIL_ANYWHERE.sub finds, in time that grows with the text's length (_EMAIL_SHAPED).

    Where one address ends, the next can start at once ("a@b.com+c@d.com"), which the
    lookbehind refuses, so that one place is tried unanchored first. Anywhere else a match
    the lookbehind refuses has an address character before it, and the match could start
    one character earlier -- so the leftmost match past that place is one it allows."""
    out, pos, m = [], 0, _EMAIL_SHAPED.search(text)
    while m:
        out += [text[pos:m.start()], HIDDEN]
        pos = m.end()
        m = _EMAIL_ANYWHERE.match(text, pos) or _EMAIL_SHAPED.search(text, pos)
    return "".join(out) + text[pos:] if out else text


def _anyones(text):
    """Any email-shaped token and any 8+ digit phone-shaped run, whoever's they are."""
    return _PHONE_SHAPED.sub(HIDDEN, _emails_hidden(text))


def _strings(value):
    """Every string under `value`, keys included, and every number as text."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, (int, float)):
        yield str(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)
    else:
        yield str(value)


class Scrubber:
    """Hides one or more leads' own email and phone, and (in free text) anyone's."""

    def __init__(self, contacts=()):
        written, said, self.cores, self.emails = [], [], [], []
        for email, phone in contacts:
            email = plain(_text(email)).strip()
            w, s = _email_patterns(email)
            written += w
            said += s
            # Only an address: a lead whose email was stored as "none" must not make every
            # "none" in the answer look like a leak.
            if _is_address(email):
                self.emails.append(email.lower())
            pattern, core = _phone_pattern(phone)
            if pattern:
                written.append(pattern)
                self.cores.append(core)
        self._own = _compiled(written + said)
        self._written = _compiled(written)   # without the said-aloud spellings: see name()

    def own(self, text):
        """The lead's own email and phone, in every spelling. For machine-written fields."""
        text = plain(text)
        return self._own.sub(HIDDEN, text) if self._own else text

    def free(self, text):
        """own(), then any email-shaped token and any 8+ digit phone-shaped run."""
        return _anyones(self.own(text))

    def name(self, text):
        """free() without the address's said-aloud spellings. For customer.name and
        brief.client_name only (28 Sep 2026, review): a caller whose address is
        firstname@lastname.com.au is called Firstname Lastname, and the name is what a masked
        viewer tells two callers apart by. The address as written, the lead's number in any
        spelling, and anyone's address or number still go -- Kommo often names a WhatsApp
        contact by their number."""
        text = plain(text)
        if self._written:
            text = self._written.sub(HIDDEN, text)
        return _anyones(text)

    def number(self, n):
        """A number that spells the lead's phone (a record that stored it as an int)."""
        s = digits(n)
        return HIDDEN if any(core in s for core in self.cores) else n

    def tree(self, value, text):
        """Apply `text` (own or free) to every string, key included, under `value`."""
        if isinstance(value, str):
            return text(value)
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            return self.number(value)
        if isinstance(value, list):
            return [self.tree(v, text) for v in value]
        if isinstance(value, dict):
            return {(text(k) if isinstance(k, str) else k): self.tree(v, text)
                    for k, v in value.items()}
        raise MaskError(f"cannot mask a {type(value).__name__}")

    def leaked(self, value, said=True):
        """True when the lead's own email or phone is still anywhere in `value`.

        The walk above is the masking; this is a second, independent look at the result, so a
        field the walk ever misses cuts that lead down (hard_masked) rather than publishing a
        number. Every string under `value` (keys too) and every number is looked at on its
        own, after plain(), three ways: the patterns the masking uses, the address as a plain
        substring, and the DIGITS (_digits_leak). said=False leaves out the address's said-aloud spellings, for
        the two name fields that are masked without them (Scrubber.name).
        """
        pattern = self._own if said else self._written
        for s in _strings(value):
            s = plain(s)
            if pattern and pattern.search(s):
                return True
            low = s.lower()
            if any(e in low for e in self.emails):
                return True
            if self._digits_leak(s):
                return True
        return False

    def _digits_leak(self, s):
        """The lead's number in `s` with EVERY non-digit removed (28 Sep 2026, review).

        A pattern can only list the separators someone thought of; this looks at the digits
        alone, so none -- "/", a line break, "0400 or 111 then 678" -- carries the number past
        it. A full number's core is its 9-digit national number without the 0, and its 11-digit
        61 form contains it, so one look covers both. A core shorter than 9 digits (a number
        stored cut short) is looked for only as an unbroken run of digits: 6 given digits in
        order turn up by chance in a note full of dates and prices, and a false match here cuts
        a lead down for nothing.
        """
        if not self.cores:
            return False
        joined, runs = digits(s), None
        for core in self.cores:
            if len(core) >= 9:
                if core in joined:
                    return True
            else:
                runs = re.findall(r"\d+", s) if runs is None else runs
                if any(core in run for run in runs):
                    return True
        return False


def _scrub_url(url, scrub):
    """A saved link, kept whole or hidden whole.

    A link with "[hidden]" spliced into it is a broken link that still looks like one, so the
    whole URL is replaced. Checked percent-decoded too ("%40" is an "@"). The generic
    8-digit rule is NOT applied to a URL: Coleen's saved property links carry listing ids of
    9 and 10 digits (realestate.com.au, domain.com.au), and hiding those would break every
    link the team saves for anyone without the contact-details password. A URL is hidden when
    it carries the lead's own email or phone, any email address, or a number in an Australian
    phone's shape (_URL_PHONE). Format characters go first (plain), from the decoded form too:
    "%E2%80%8B" is a zero-width space.
    """
    if not isinstance(url, str):
        return ""
    url = plain(url)
    both = plain(url + " " + urllib.parse.unquote(url))
    if (scrub.own(both) != both or _EMAIL_SHAPED.search(both) or _URL_PHONE.search(both)):
        return HIDDEN
    return url


# The lead record's machine-written keys (vapi-kommo-hook lib/record.js). They lose only the
# lead's own email and phone; everything else, and any key added later, is free text.
_MACHINE = frozenset({"version", "channel", "fieldsFingerprint", "test", "createdAt",
                      "endedReason", "durationSeconds", "grade", "gradeSource", "appointment",
                      "kommo", "recommendations"})
_MACHINE_RESEARCH = frozenset({"status", "record_id", "shortlist_count"})
# requestId (28 Sep 2026): the random id the tab sends with each Save or Add, which the hook
# stores on the item so a retried click is not saved twice. Random characters can hold 8
# digits in a row, and the generic rule would turn it into "[hidden]".
_MACHINE_NOTE = frozenset({"id", "at", "action", "removed", "requestId"})


def _text(value):
    return "" if value is None else (value if isinstance(value, str) else str(value))


def _lead_contact(lead):
    c = lead.get("customer")
    if c is None:
        return "", ""
    if not isinstance(c, dict):
        raise MaskError("a lead's customer is not an object")
    return _text(c.get("email")), _text(c.get("phone"))


def _mask_customer(customer, scrub):
    if customer is None:
        customer = {}
    if not isinstance(customer, dict):
        raise MaskError("a lead's customer is not an object")
    email, phone = _text(customer.get("email")), _text(customer.get("phone"))
    out = {scrub.free(k): scrub.tree(v, scrub.name if k == "name" else scrub.free)
           for k, v in customer.items() if k not in ("email", "phone")}
    out.update(email="", phone="", phoneLast3=last3(phone),
               hasEmail=bool(email.strip()), hasPhone=bool(digits(phone)))
    return out


# The two fields that hold the caller's name, masked with Scrubber.name.
_NAMES = (("customer", "name"), ("brief", "client_name"))


def mask_lead(lead, scrub):
    if not isinstance(lead, dict):
        raise MaskError("a lead is not an object")
    out = {}
    for k, v in lead.items():
        if k == "callId" and isinstance(v, str):
            out[k] = v                        # the key the notes and the page hang on
        elif k == "customer":
            out[k] = _mask_customer(v, scrub)
        elif k == "recordingUrl":
            out[k] = ""
        elif k in _MACHINE:
            out[k] = scrub.tree(v, scrub.own)
        elif k == "research" and isinstance(v, dict):
            out[k] = {scrub.free(rk): scrub.tree(rv, scrub.own if rk in _MACHINE_RESEARCH
                                                 else scrub.free)
                      for rk, rv in v.items()}
        elif k == "brief" and isinstance(v, dict):
            out[k] = {scrub.free(bk): scrub.tree(bv, scrub.name if bk == "client_name"
                                                 else scrub.free)
                      for bk, bv in v.items()}
        else:
            out[scrub.free(k)] = scrub.tree(v, scrub.free)
    # The second look. The name fields are set aside and looked at without the said-aloud
    # spellings they were masked without, or "Joseph at Shahid Realty" -- a name kept on
    # purpose -- would cut the lead down.
    body = {k: v for k, v in out.items() if not (k == "callId" and isinstance(v, str))}
    names = []
    for part, key in _NAMES:
        if isinstance(body.get(part), dict) and key in body[part]:
            body[part] = dict(body[part])
            names.append(body[part].pop(key))
    if scrub.leaked(body) or scrub.leaked(names, said=False):
        raise MaskError("a lead's own contact details survived the masking")
    return out


# 28 Sep 2026 (review): ONE LEAD, NOT THE TAB. A lead the masking could not make safe -- its
# own number still found by the second look (a spelling no rule lists, "0400 or 111 then
# 678"), or a customer that is not an object -- was a 502 for the whole answer, so one odd
# record took the Leads tab away from everyone without the password. Now that lead alone is
# cut down to the fields below, each kept only when its value is one this file knows the
# whole shape of, and says so (maskFailed); every other lead is shown as usual. Its notes are
# not sent either (notesHidden): they would be masked with details that could not be trusted.
_GRADES = frozenset({"hot", "warm", "nurture"})
_CHANNELS = frozenset({"vapi", "whatsapp"})
_ISO_TIME = re.compile(r"\d{4}-[01]\d-[0-3]\dT[0-2]\d:[0-5]\d:[0-5]\d(?:\.\d{1,6})?"
                       r"(?:Z|[+-][0-2]\d:[0-5]\d)?")
_KOMMO_LEAD_URL = re.compile(r"https://[a-z0-9-]+\.kommo\.com/leads/detail/\d{1,12}")


def hard_masked(lead, contact):
    """What is left of a lead the masking could not make safe. `contact` is its (email,
    phone), or None when even that could not be read."""
    email, phone = contact if contact is not None else ("", "")
    scrub = Scrubber([contact] if contact is not None else [])
    out = {}
    if isinstance(lead.get("callId"), str):
        out["callId"] = lead["callId"]
    kept = {}
    # A string before every "in" (28 Sep 2026, second review): a grade stored as a list or a
    # channel as an object cannot be looked up in a set (TypeError), and the lead this is
    # for was a 502 for the whole tab again.
    channel, grade = lead.get("channel"), lead.get("grade")
    if isinstance(channel, str) and channel in _CHANNELS:
        kept["channel"] = channel
    if "grade" in lead:                       # an absent grade is an old hot call: stays absent
        kept["grade"] = grade if isinstance(grade, str) and grade in _GRADES else ""
    if lead.get("test") is True:
        kept["test"] = True
    created = lead.get("createdAt")
    if isinstance(created, str) and _ISO_TIME.fullmatch(created):
        kept["createdAt"] = created
    kommo = lead.get("kommo")
    url = kommo.get("leadUrl") if isinstance(kommo, dict) else None
    if isinstance(url, str) and _KOMMO_LEAD_URL.fullmatch(url):
        kept["kommo"] = {"leadUrl": url}      # Coleen can still open the lead in Kommo
    # Their shapes hold no letters, but the digits of a date or an id could, by chance, be
    # the lead's 9: each is looked at once more on its own.
    out.update({k: v for k, v in kept.items() if not scrub.leaked(v)})
    out["customer"] = {"name": HIDDEN, "email": "", "phone": "", "phoneLast3": last3(phone),
                       "hasEmail": bool(email.strip()), "hasPhone": bool(digits(phone))}
    out["maskFailed"] = True
    return out


def _nothing_but_its_id(lead):
    """A lead even hard_masked could not cut down: its callId, and that it is hidden."""
    out = {"callId": lead["callId"]} if isinstance(lead.get("callId"), str) else {}
    out["customer"] = {"name": HIDDEN, "email": "", "phone": "", "phoneLast3": "",
                       "hasEmail": False, "hasPhone": False}
    out["maskFailed"] = True
    return out


def _mask_note_item(item, scrub):
    if not isinstance(item, dict):
        raise MaskError("a note entry or link is not an object")
    out = {}
    for k, v in item.items():
        if k == "url":
            out[k] = _scrub_url(v, scrub)
        elif k in _MACHINE_NOTE:
            out[k] = scrub.tree(v, scrub.own)
        else:                                 # text, label, by, and anything added later
            out[scrub.free(k)] = scrub.tree(v, scrub.free)
    return out


def mask_notes(notes, scrub):
    """One lead's {entries, links, rev}, masked."""
    if not isinstance(notes, dict):
        raise MaskError("a lead's notes are not an object")
    out = {}
    for k, v in notes.items():
        if k in ("entries", "links"):
            if not isinstance(v, list):
                raise MaskError(f"notes.{k} is not a list")
            out[k] = [_mask_note_item(item, scrub) for item in v]
        elif k == "rev":
            out[k] = scrub.tree(v, scrub.own)
        else:
            out[scrub.free(k)] = scrub.tree(v, scrub.free)
    if scrub.leaked(out):
        raise MaskError("a lead's own contact details survived the notes masking")
    return out


def unreadable_notes_absent(doc):
    """`doc` with every notes[callId] that is null removed, and that callId in notesErrors.

    28 Sep 2026 (review): the hook first sent notes[callId] = null beside notesErrors for a
    notes object it could not read -- one broken file, or every lead when its Blob listing
    failed -- and masking a null raised, so ONE unreadable note blanked the whole tab for
    everyone without the password. The agreed shape is now that an unreadable notes object is
    only named in notesErrors and its callId is absent from notes; a hook deployed before that
    still sends null, so a null is read here as absent, in both views, and is named in
    notesErrors if the hook did not name it: "could not read" and "no notes yet" must not
    look the same on the tab. A copy when anything changes; `doc` itself is never edited.
    """
    notes = doc.get("notes")
    if not isinstance(notes, dict) or all(n is not None for n in notes.values()):
        return doc
    gone = [cid for cid, n in notes.items() if n is None]
    doc = dict(doc, notes={cid: n for cid, n in notes.items() if n is not None})
    errors = doc.get("notesErrors")
    if errors is None:
        errors = []
    if isinstance(errors, list):
        doc["notesErrors"] = errors + [cid for cid in gone if cid not in errors]
    return doc


def mask_hot_leads(doc):
    """The hook's GET /api/hot-leads answer as a person without the password may see it."""
    if not isinstance(doc, dict):
        raise MaskError("the answer is not an object")
    doc = unreadable_notes_absent(doc)
    leads = doc.get("leads")
    if not isinstance(leads, list):
        raise MaskError("the answer has no list of leads")
    # 28 Sep 2026 (second review): ANY EXCEPTION, not only MaskError. One lead that raised
    # something else -- a grade stored as a list (TypeError), a summary nested 1,200 deep
    # (RecursionError), an address of 500 labels that re cannot compile -- was still a 502
    # for every lead. Each lead's Scrubber is built, and the lead masked, on its own: what
    # fails is cut down (hard_masked), and if even that fails, the lead is only its callId
    # (_nothing_but_its_id). "anyone" is built from the contacts whose own Scrubber built:
    # the same patterns again, joined by "|", which nests no deeper than the deepest of them.
    contacts, scrubs = [], []
    for lead in leads:
        if not isinstance(lead, dict):
            raise MaskError("a lead is not an object")
        try:
            contact = _lead_contact(lead)
        except Exception:                                              # noqa: BLE001
            contact = None                    # a customer that is not an object: hard_masked
        try:
            scrub = Scrubber([contact]) if contact is not None else None
        except Exception:                                              # noqa: BLE001
            scrub = None
        contacts.append(contact)
        scrubs.append(scrub)
    anyone = Scrubber([c for c, s in zip(contacts, scrubs) if s is not None])
    # Each lead on its own (hard_masked). A callId any of whose leads was cut down is
    # "untrusted": its notes are not sent.
    masked, per_lead, untrusted = [], {}, set()
    for lead, contact, scrub in zip(leads, contacts, scrubs):
        cid = lead.get("callId") if isinstance(lead.get("callId"), str) else None
        one = None
        if scrub is not None:
            try:
                one = mask_lead(lead, scrub)
            except Exception:                                          # noqa: BLE001
                one = None
        if one is None:
            try:
                one = hard_masked(lead, contact)
            except Exception:                                          # noqa: BLE001
                one = _nothing_but_its_id(lead)
            if cid is not None:
                untrusted.add(cid)
        elif cid is not None:
            per_lead.setdefault(cid, []).append(contact)
        masked.append(one)
    out, hidden = {}, []
    for k, v in doc.items():
        if k == "leads":
            out[k] = masked
        elif k == "notes":
            if v is None:
                out[k] = None
                continue
            if not isinstance(v, dict):
                raise MaskError("notes is not an object")
            # A note is masked with ITS lead's details; notes for a lead not on this page
            # (the hook should never send one) with every returned lead's. A null -- the
            # old hook's "could not read" -- is already gone (unreadable_notes_absent). One
            # lead's notes that cannot be masked are left out and named in notesHidden, as
            # a lead is cut down alone: never a 502 for everyone.
            out[k] = {}
            for cid, n in v.items():
                try:
                    named = anyone.leaked(cid)
                except Exception:                                      # noqa: BLE001
                    named = True
                if named:
                    continue                  # a key that is a contact detail is no callId
                if cid in untrusted:
                    hidden.append(cid)
                    continue
                try:
                    out[k][cid] = mask_notes(n, Scrubber(per_lead[cid]) if cid in per_lead
                                             else anyone)
                except Exception:                                      # noqa: BLE001
                    hidden.append(cid)
        elif k == "notesHidden":
            continue                          # this file's to say, never the hook's
        else:
            # Beside the leads and the notes (status, count, notesErrors, anything added
            # later): a value that cannot be masked is left out, never sent as it came, and
            # is no reason to take the leads away from everyone.
            try:
                if k in ("status", "count", "notesErrors"):
                    out[k] = anyone.tree(v, anyone.own)
                else:
                    out[anyone.free(k)] = anyone.tree(v, anyone.free)
            except Exception:                                          # noqa: BLE001
                continue
    if hidden:
        out["notesHidden"] = hidden
    return out


DUPLICATE_FLAGS = ("duplicate", "duplicateRemoved")


def mask_notes_answer(doc):
    """The hook's POST /api/lead-notes answer, masked with the lead's own rules, as GET is.

    28 Sep 2026 (review): this used the generic rules alone, and the answer carries EVERY live
    note of the lead, which the tab draws in place of its copy -- so any save by a masked
    viewer showed earlier notes with the lead's address said aloud ("joseph dot shahid at
    example dot com") or the number written "0400/111/678", which GET hides. The hook now
    adds the lead's own details to its answer, read from the lead record it checks anyway:
    top-level "contact": {"email", "phone"}. They are used here for the lead's rules and are
    never passed on (api/lead_notes.py drops "contact" from the full view as well).

    A hook deployed before that sends no "contact". Then only the generic rules apply -- any
    email address, any 8+ digit phone-shaped run -- and the lead's own other spellings are
    hidden when GET /api/hot_leads next returns these notes beside the lead. A "contact" that
    is there but not an object is a shape this file does not know: MaskError, which
    api/lead_notes.py answers with the notes hidden (notes_hidden_answer).

    "duplicate" and "duplicateRemoved" (28 Sep 2026): the hook's word that the Save was a
    retried click it had already stored -- and whether what it stored has since been removed
    -- so the tab does not draw it twice. Passed on as they are when they are true or false.
    """
    if not isinstance(doc, dict):
        raise MaskError("the answer is not an object")
    contact = doc.get("contact")
    if contact is None:
        scrub = Scrubber()                   # an older hook: the generic rules only, see above
    elif isinstance(contact, dict):
        scrub = Scrubber([(_text(contact.get("email")), _text(contact.get("phone")))])
    else:
        raise MaskError("the answer's contact is not an object")
    out = {}
    for k, v in doc.items():
        if k == "contact":
            continue                          # the lead's own details: never to the browser
        if k == "notes":
            out[k] = mask_notes(v, scrub) if v is not None else None
        elif k in ("status", "callId") or (k in DUPLICATE_FLAGS and isinstance(v, bool)):
            out[k] = v
        else:
            out[scrub.free(k)] = scrub.tree(v, scrub.free)
    if scrub.leaked({k: v for k, v in out.items() if k != "callId"}):
        raise MaskError("the lead's own contact details survived the notes masking")
    return out


def notes_hidden_answer(doc):
    """The POST /api/lead-notes answer when mask_notes_answer could not make it safe.

    28 Sep 2026 (second review): that was a 502, "the note was saved, but ...", an error for
    a save that had gone through. Now the answer says it went through: the callId, the
    hook's duplicate flags, and notesHidden in place of the notes. Nothing else of the hook's
    answer is sent; the notes come back with the lead on the next GET /api/hot_leads, masked
    or left out there by the same rules."""
    out = {"status": "success"}
    if isinstance(doc.get("callId"), str):
        out["callId"] = doc["callId"]
    out["notesHidden"] = True
    out.update({k: doc[k] for k in DUPLICATE_FLAGS if isinstance(doc.get(k), bool)})
    return out


def generic(text):
    """Free-text scrubbing with no lead in hand: any email address, any phone-shaped run."""
    return Scrubber().free(_text(text))
