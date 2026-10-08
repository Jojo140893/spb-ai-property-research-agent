"""
Display labels: a readable address for a listing, and a readable title for a document.

Both solve the same shape of problem — what the scraper captured is not what a client
should be shown — and both obey the same rule: derive from what the source actually
said, never invent.

A readable address for a listing whose address column swallowed the whole spreadsheet row.

47% of live stock (2,670 of 5,703) arrives looking like

    DUPLEX PR8735 106 Redbank Plains Sienna Eden Estate 2026-09-01 00:00:00 505 $595,000 $732,285 732285
    2-Part Detached SS West 2103 Seventh Bend Weir Views Sep-26 332 $348,000 LX 18N 4 / 2 / 2 $375,700

because the source is a price list where the address is one column of many and the
extractor concatenated the row. That is a client-facing problem: it is the headline of
every shortlist card and the title of every report.

Only tokens that CANNOT belong to an Australian street address are removed:

  * an ISO timestamp                     2026-09-01 00:00:00
  * a money amount                       $595,000   $ 661,000
  * an integer of five digits or more    732285, 1327285   (no street number is 50,000+)
  * a decimal number                     227.2   (a floor area, never an address)
  * a title-availability date            Sep-26, Q4 2026
  * a bed/bath/car triplet               4 / 2 / 2

Matching against the row's other columns was tried first and rejected: bedrooms=2 made
"2 Bed 2 Bath" render as "Bed Bath", and land_size=2 turned "in stage 2" into "in stage".
Small integers legitimately appear in address text, so they are never touched. This is a
display label only — `lot_address` keeps whatever the source gave us.
"""

import re
from typing import Any, Dict, Optional

_STRIPPERS = (
    # 2026-09-01 00:00:00 / 2026-09-01T00:00 / 2026-09-01
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b"),
    # $595,000 and "$ 661,000" — the space after the sign appears in several lists
    re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?"),
    # Sep-26 / Sept 2026 / Dec/26 — a title month. Requires the digits, so a street
    # called "May" or "March" is untouched.
    re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
               r"[\s\-/]*\d{2,4}\b", re.I),
    # Q4 2026 / Q4-26
    re.compile(r"\bQ[1-4][\s\-/]*\d{2,4}\b", re.I),
    # 4 / 2 / 2 — bed/bath/car, appended by several price lists. All THREE parts are
    # required: a two-part "3/45" is a unit address ("Unit 3/45 May Street"), and
    # stripping it deleted a real street number.
    re.compile(r"(?<![\w$])\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{1,2}(?![\w/])"),
    # A bare price that lost its dollar sign. Five digits or more: the highest street
    # number in the country is four, so this can only be money.
    re.compile(r"(?<![\w$.,/-])\d{5,}(?![\w./-])"),
    # 227.2 — a floor area or a frontage, never part of an address
    re.compile(r"(?<![\w$/-])\d{1,4}\.\d{1,2}(?![\w/-])"),
)

_MULTISPACE = re.compile(r"\s{2,}")
_EDGE_JUNK = re.compile(r"^[\s,;:|/\-]+|[\s,;:|/\-]+$")
# ", ," or " - - " left behind between removed tokens
_ORPHAN_SEPARATORS = re.compile(r"\s*([,;:|])\s*(?=[,;:|])")


def clean_display_address(raw: Any, row: Optional[Dict[str, Any]] = None) -> str:
    """The address with the non-address tokens removed.

    `row` is accepted so callers do not have to change if this ever needs the rest of the
    record; it is deliberately unused, because matching against sibling columns removed
    digits that were part of the text.

    Never returns an empty string for a non-empty input: a cluttered address beats a
    blank one on a client-facing card.
    """
    original = str(raw or "").strip()
    if not original:
        return ""

    text = original
    for pattern in _STRIPPERS:
        text = pattern.sub(" ", text)

    text = _ORPHAN_SEPARATORS.sub("", text)
    text = _MULTISPACE.sub(" ", text)
    text = _EDGE_JUNK.sub("", text).strip()

    # Reduced to nothing, or to punctuation and digits with no words left — keep the mess.
    if not text or not re.search(r"[A-Za-z]", text):
        return original
    return text


# --------------------------------------------------------------------------- documents

# Anchor text on a builder's download button, which is the same on every document they
# publish. Sixteen brochures shared the title "Icon to represent a home design brochure
# Download Brochure", so the vendor tab listed sixteen indistinguishable links and a
# consultant could not tell which one to send.
_GENERIC_TITLES = {
    "download brochure", "download floorplan", "download", "brochure", "floorplan",
    "floorplans", "specification", "specifications", "download specification",
    "view brochure", "view floorplan", "download pdf", "pdf", "more info",
    "view full terms and conditions", "terms and conditions", "download flyer", "flyer",
}
# The alt text of the little image inside the button, captured along with the anchor
# text. It appears both before the label ("Icon to represent a home design brochure
# Download Brochure") and after it ("Floorplans icon Floorplan"), so rather than trying
# to locate it, treat any title mentioning an icon as describing a picture, not a
# document. A real document title does not contain the word.
_MENTIONS_ICON = re.compile(r"\bicons?\b", re.I)
# The call to action tacked onto the end of an otherwise useful title.
_TRAILING_CTA = re.compile(
    r"[\s\-–—:|]*\b(?:download|view)?\s*(?:brochure|floorplans?|specifications?|flyer|pdf)\s*$",
    re.I)
_FILE_JUNK = re.compile(r"^\d{6,8}[-_]?|[-_](?:v?\d+|final|web|lr|hr|copy|updated)$", re.I)


def clean_asset_title(title: Any, source_url: Any = "") -> str:
    """A title that tells one document apart from another.

    Prefers the scraped title when it says something, and falls back to the file's own
    name in the URL — "Denmark-185-Scullery-Brochure.pdf" becomes "Denmark 185 Scullery
    Brochure". That is the vendor's own name for the file, not a guess about it.
    """
    text = _MULTISPACE.sub(" ", str(title or "").replace("\n", " ")).strip()
    if _MENTIONS_ICON.search(text):
        text = ""
    if text and text.lower() not in _GENERIC_TITLES:
        trimmed = _TRAILING_CTA.sub("", text).strip(" -–—:|")
        # Only accept the trim if it left something meaningful behind.
        if trimmed and len(trimmed) > 3 and trimmed.lower() not in _GENERIC_TITLES:
            return trimmed
        return text

    name = str(source_url or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    name = re.sub(r"\.(pdf|docx?|xlsx?|jpe?g|png)$", "", name, flags=re.I)
    name = _FILE_JUNK.sub("", name)
    name = _MULTISPACE.sub(" ", name.replace("-", " ").replace("_", " ")).strip()
    if name and re.search(r"[A-Za-z]{3}", name):
        # Leave existing capitalisation alone where the vendor used it; only title-case
        # a name that is entirely lower case, so "SQ-Brochure" does not become "Sq".
        return name.title() if name.islower() else name
    return text or "Untitled document"


# --------------------------------------------------------------------- suburb recovery

# The eight states, abbreviated and spelled out. A value equal to one of these is not a
# suburb, whatever column it is sitting in.
_STATE_TOKENS = {
    "NSW": "NSW", "VIC": "VIC", "QLD": "QLD", "SA": "SA", "WA": "WA", "TAS": "TAS",
    "NT": "NT", "ACT": "ACT",
    "NEW SOUTH WALES": "NSW", "VICTORIA": "VIC", "QUEENSLAND": "QLD",
    "SOUTH AUSTRALIA": "SA", "WESTERN AUSTRALIA": "WA", "TASMANIA": "TAS",
    "NORTHERN TERRITORY": "NT", "AUSTRALIAN CAPITAL TERRITORY": "ACT",
}


def display_suburb(row: Dict[str, Any]) -> str:
    """The row's suburb, recovering it where a STATE NAME was stored in that column.

    56 live Proxima rows hold suburb="New South Wales". The address parser only knew the
    abbreviated form, so on "…, BINGARA DRIVE, WILTON, New South Wales, 2571" the full
    name was taken for the suburb and Wilton was swallowed by the street. Those rows
    cannot be found by suburb, cannot be distance-searched, and cannot be matched against
    the same lot in another channel — which is how the Proxima price error was confirmed.

    Corrected on READ, not rewritten in the database, because suburb_norm is part of
    building_content_hash (database.py:225): editing it in place would change the row's
    identity and the next harvest would insert a second copy instead of updating it.
    The parser fix in sources/proxima.py is what makes the stored value right; this
    makes the rows usable until that harvest can run.

    Returns the stored suburb unchanged whenever there is nothing to correct — this
    never invents a suburb, it only reads the one the row's own address already states.
    """
    stored = str(row.get("suburb") or "").strip()
    if stored.upper() not in _STATE_TOKENS:
        return stored

    parts = [p.strip() for p in str(row.get("lot_address") or "").split(",") if p.strip()]
    if len(parts) < 2:
        return stored
    if re.fullmatch(r"\d{4}", parts[-1]):          # postcode
        parts.pop()
    if parts and parts[-1].upper() in _STATE_TOKENS:
        parts.pop()
    # Whatever now sits at the end is the suburb, unless it is another state token or
    # carries a street number — in which case the address never named one.
    if parts:
        candidate = parts[-1].strip()
        if candidate.upper() not in _STATE_TOKENS and not re.match(r"^\d+\s", candidate):
            return candidate.title()
    return stored


# ------------------------------------------------------------------- agent small print

# A PRICE LIST'S SMALL PRINT FOR AGENTS, never a buyer's (8 Oct 2026; Stage B's F5, ported to
# main on its own, without the row labels). Since the 600-candidate cap was lifted (PR #27)
# far more of it reaches the shortlist -- 118 cards carried it on 7 Oct against 4 before --
# as a card's title ('Single Car Space & OB Storage Agent commission=6%plus, South Yarra
# VIC'), its estate ('Stock highlighted in orange below is eligible for the 100% commission
# offer.'), a building's name and an incentive ('$50,000 purchaser rebate/agent bonus'), and
# from there to the Research tab, Hot Leads, the Kommo note and Clare's options. Whole words,
# so 'Commissioners Gully' and 'Agentia Park' are not small print; 'Agentcommission' and
# 'E-AGENT' (glued by a PDF) are. In a title or an estate the purchaser, FIRB and guaranteed-
# income words come from column headings and notes ('Upfront FIRB Fee payable by', 'PRICE
# (EX GST) GUARANTEED INCOME P.A.'), never from an address. client_report.py and
# index.html's clientAddress drop the same parts from a client document with a wider net.
AGENT_TEXT = re.compile(
    r"\bcommissions?\b|\bagent(?:s|commission)?\b|\bpurchasers?\b|\beligib\w*|"
    r"\bhighlighted\b|\bfirb\b|\bguaranteed\s+income\b", re.I)
# In an INCENTIVE the purchaser and FIRB words are the offer itself -- 'rebate + FIRB fee
# covered (FIRB buyer) or $65,000' (31 live rows), '$50,000 purchaser rebate' -- which is what
# the field is for, so only what is the agent's goes: the commission and the agent's bonus.
AGENT_ONLY_TEXT = re.compile(r"\bcommissions?\b|\bagent(?:s|commission)?\b", re.I)

# A STREET OR A PLACE NAMED WITH ONE OF THOSE WORDS IS NOT SMALL PRINT (8 Oct 2026, repair
# round): '12 Agent Street', 'Lot 5 Commission Road', 'Purchaser Court', "Commission's Hill
# Estate", 'Eligibility Lane', 'Highlighted Way'. Whole words alone took them for notes and the
# title lost its street. A name is written as one -- the word and the street or place type
# after it both open in upper case -- so a note's 'commission road show' is still a note. No
# live row has such a name (every one of the 42 titles and 43 estates the strip changes is
# small print); this is for the stock still to come.
_STREET_TYPES = (r"street|st|road|rd|avenue|ave|drive|dr|court|ct|crescent|cres|place|pl|"
                 r"lane|ln|way|parade|pde|boulevard|blvd|close|cl|circuit|cct|terrace|tce|"
                 r"grove|gr|highway|hwy|esplanade|esp|square|sq|rise|walk|track|loop|mews|row")
_PLACE_TYPES = _STREET_TYPES + (r"|hill|hills|heights|estate|park|gardens|green|grange|ridge|"
                                r"reserve|vale|valley|view|views|waters|creek|point|bay|"
                                r"springs|gully|village|quarter|junction|wharf|lakes?|downs|"
                                r"fields|meadows")
_NAMED_PLACE = re.compile(r"(?:'s)?\s+(%s)\b" % _PLACE_TYPES, re.I)

# Where a line parts: a comma, a '|', or a '/' between two words ('rebate/agent bonus'). NOT
# a comma inside a number: '$50,000' is one amount -- Stage B split there, and '$50,000
# purchaser rebate/agent bonus' (37 live rows) came out '$50' -- and '3/45' is a unit address.
_PART_BREAK = re.compile(
    r"(\s*(?:(?<!\d),|,(?!\d{3}(?!\d))|\|)\s*|(?<=[^\W\d_])\s*/\s*(?=[^\W\d_]))")
# An INCENTIVE parts at those and at more (8 Oct 2026, repair round), because the buyer's
# money and the agent's share sit side by side in one line: a ';' or a ' + ' ('$5,000 cashback
# + agent bonus $2,000'), a '/' after an amount ('Rebate $1,250,500/agent fee') and a
# sentence's end ('$10,000 rebate.Agent bonus $5,000'). Without them the agent's words took the
# buyer's offer down with them, and the card kept its incentive amount with no words beside
# it. Not in a title: 'the build + commission inc GST' is one note, and a ' + ' there would
# keep its first half.
_INCENTIVE_BREAK = re.compile(
    r"(\s*(?:(?<!\d),|,(?!\d{3}(?!\d))|[|;])\s*|\s+\+\s+|(?<=[^\W_])\s*/\s*(?=[^\W\d_])"
    r"|(?<=[^\W\d_]{2})\.\s*(?=[A-Z$]))")
# Small print in brackets goes with its brackets, and what is around them stays as one line
# (8 Oct 2026, repair round): '$15,000 rebate (agent referral fee $2,000) plus FIRB covered'
# -> '$15,000 rebate plus FIRB covered'. A bracket with no small print is left as it was:
# '(FIRB buyer)' is the buyer's.
_BRACKETED = re.compile(r"\s*\(([^()]*)\)")
# A document's own heading in front of what it names: 'Agent Stock List 167 Ingleburn Road'
# is the development at 167 Ingleburn Road (19 live rows), and the address stays.
_AGENT_HEADING = re.compile(
    r"^(?:external\s+)?agents?\s+(?:stock|price)\s*lists?\b[\s:.\-]*", re.I)
# What a part says before its small print that stands on its own (8 Oct 2026, repair round):
#   a sentence that ends before it   'Lot 178, Wine cellar lot included. Agent' (two letters
#                                    and a stop, so '2.5' and 'P.A.' are no sentence's end)
#   a street address in front of it  '12 Smith St Agent commission 3%' -> '12 Smith St'
#   in an incentive, an amount       'Rebate $15,000 agent fee $2,000' -> 'Rebate $15,000'
#   said of something, and not tied to the agent's words: '$20,000 agent bonus' is the
#   agent's $20,000, and so is '$20,000 bonus for agents'
#   a line a lone 'purchaser' closes '3 Bed 3 Bath 1 Powder purchaser' -> '3 Bed 3 Bath 1
#                                    Powder': the next column's heading run into it (Aria,
#                                    24 live titles; its estate column reads 'purchaser' alone
#                                    on 13 more), unless the words before it are said of the
#                                    purchaser ('payable by purchaser', 'foreign purchaser')
#   a line an agent's commission     'Single Car Space & OB Storage Agent commission=6%plus'
#   rate closes                      -> 'Single Car Space & OB Storage': the commission column
#                                    run into the line ('Pricelist New Agent.pdf', 14 live
#                                    titles), unless the words before it are said of the
#                                    commission ('Price includes Agent commission = 2%')
# The whole part went before, so 'Wine cellar lot included' left the title -- and Clare, asked
# for 'Lot 178, Wine cellar lot included', could no longer confirm the lot she had confirmed --
# and the unit's own words ('Single Car Space & OB Storage') went with the agent's, so cards
# of one list read only their place and could not be told apart.
_SENTENCE_END = re.compile(r"(?<=[^\W\d_]{2})[.!?](?=\s|[A-Z]|$)")
_LOT_OR_NUMBER = r"\d+[a-z]?(?:\s*[-/]\s*\d+[a-z]?)?"
_STREET_LEAD = re.compile(
    r"^(?:(?:lot|unit|apt|apartment|townhouse|villa)\s*%s\s*,?\s*(?:%s\s+)?|%s\s+)"
    r"(?:[A-Za-z][\w'.\-]*\s+){0,3}?(?:%s)\b\.?"
    % (_LOT_OR_NUMBER, _LOT_OR_NUMBER, _LOT_OR_NUMBER, _STREET_TYPES), re.I)
_AMOUNT = re.compile(r"\$\s*\d")
_MONEY = re.compile(r"\$\s*\d[\d,.]*(?:k\b)?", re.I)
_DESCRIBED = re.compile(r"[A-Za-z]{2,}")
_TIED = re.compile(r"\b(?:to|for|by|the|a|an|of|via|paid|payable|from|per|our|your|any|all|"
                   r"each|is|are|selling|referring|external|foreign|local|overseas|resident|"
                   r"domestic|home|eligible|approved|intending|prospective|new|every|"
                   r"includes?|including|incl|inc|excludes?|excluding|excl|ex|less|without|"
                   r"no|pays?|paying|plus)\s*$", re.I)
_CLOSING_PURCHASER = re.compile(r"(?<=\s)purchasers?\s*$", re.I)
_COMMISSION_RATE = re.compile(r"agent\s*commission\s*(?:[=:]|\d|of\s+\d)", re.I)
_LEAD_TAIL = re.compile(r"(?:[\s,;:+&/.\-]|\b(?:plus|and|or|with)\b)+$", re.I)
_ALNUM = re.compile(r"[^\W_]")
# What a card is titled when its source row gave no address (api/_candidates.py says it too).
NO_ADDRESS = "(no address in source row)"
# THE WORDS NEVER LOSE THE MONEY THE CARD'S AMOUNT STANDS ON (8 Oct 2026, second repair
# round). A card shows incentive_amount beside its words -- 'Incentive $' on the Research tab,
# the comparison's Rebate and Effective Price -- and that amount is as read: this change is
# text only and may not move it. The round before took '$50,000' out of '$50,000bonus.
# Purchaserrebate' (12 live rows of 'Pricelist New Agent.pdf', South Yarra) as the agent's
# bonus while the card still said 50000, so it read as a confirmed $50,000 purchaser rebate --
# and 'Purchaserrebate' alone is only a column heading. Whose $50,000 that is the source does
# not settle: the owner decides, in a change that may move the amount and the words together.
# Until then a bare '$N bonus' is no small print (it names no agent), and a strip that would
# take away the amount the card carries is not made at all: the words stay as read.
_MONEY_SAID = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*(k\b)?", re.I)


def _continues(part: str) -> bool:
    """True for a part that opens in lower case: it goes on with the sentence before it
    ('Price is approximately for the build + commission inc GST, estimate combined.')."""
    opening = part.lstrip(" \t\"'([")
    return bool(opening) and opening[0].islower()


def _states(text: Any, amount: float) -> bool:
    """True when `text` states `amount` in dollars: '$50,000', '$50000' and '$50k' all state
    50000 (see _MONEY_SAID above)."""
    for m in _MONEY_SAID.finditer("" if text is None else str(text)):
        try:
            said = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        except ValueError:
            continue
        if abs(said - amount) < 0.5:
            return True
    return False


def _small_print(text: str, words) -> Optional["re.Match"]:
    """The first of `words` in `text` that is small print, None when none is: a word that
    names a street or a place with the type after it ('Agent Street') is not."""
    for m in words.finditer(text):
        named = _NAMED_PLACE.match(text, m.end())
        if named and m.group(0)[:1].isupper() and named.group(1)[:1].isupper():
            continue
        return m
    return None


def _lead(part: str, at: int, incentive: bool) -> str:
    """What `part` says before its small print at `at` that stands on its own (see
    _SENTENCE_END above): the sentences before the one holding it, a street address in front
    of it, all of it when a lone 'purchaser' or an agent's commission rate closes it, or in
    an incentive an amount; '' when nothing does."""
    before = part[:at]
    lead = ""
    ends = list(_SENTENCE_END.finditer(before))
    if ends:
        lead = before[:ends[-1].start()]
    street = _STREET_LEAD.match(before.strip())
    if street and len(street.group(0)) > len(lead.strip()):
        lead = street.group(0)
    closing = _CLOSING_PURCHASER.search(part)
    if not incentive and not _TIED.search(before) and (
            (closing and closing.start() == at) or _COMMISSION_RATE.match(part, at)):
        lead = before
    if incentive and not lead.strip() and _AMOUNT.search(before):
        said = _LEAD_TAIL.sub("", before)
        if not _TIED.search(said) and _DESCRIBED.search(_MONEY.sub("", said)):
            lead = said
    lead = _LEAD_TAIL.sub("", lead).strip()
    return lead if _ALNUM.search(lead) else ""


def _strip(value: Any, fallback: str, words, breaks, incentive: bool) -> Any:
    """without_agent_text and shown_incentive: one pass, its words and its breaks."""
    text = "" if value is None else str(value)

    def at(part):
        """Where `part`'s small print starts; None when it has none."""
        m = _small_print(part, words)
        return m.start() if m else None

    read = text
    text = _BRACKETED.sub(lambda b: "" if at(b.group(1)) is not None else b.group(0), text)
    pieces = breaks.split(text)
    parts, cuts = pieces[0::2], [""] + pieces[1::2]
    if all(at(p) is None for p in parts):
        return value if text == read else (text.strip() or fallback)
    for i, part in enumerate(parts):
        heading = _AGENT_HEADING.match(part.strip())
        rest = part.strip()[heading.end():].strip() if heading else ""
        if rest and at(rest) is None:
            parts[i] = rest
    keep = [True] * len(parts)
    start = 0
    while start < len(parts):
        end = start + 1
        while end < len(parts) and _continues(parts[end]):
            end += 1
        marked = [k for k in range(start, end) if at(parts[k]) is not None]
        if marked:
            opens_lower = _continues(parts[start])
            for k in range(start if opens_lower else marked[0], end):
                keep[k] = False
            # A sentence that opens in lower case is a note's tail, and goes whole; one that
            # opens as a sentence keeps what it said before its small print.
            lead = "" if opens_lower else _lead(parts[marked[0]], at(parts[marked[0]]),
                                                incentive)
            if lead:
                parts[marked[0]], keep[marked[0]] = lead, True
        start = end
    kept = ""
    for k, part in enumerate(parts):
        if keep[k] and part.strip():
            kept += (cuts[k] if kept else "") + part.strip()
    return kept or fallback


def without_agent_text(value: Any, fallback: str = "") -> Any:
    """`value` with the parts that are agent small print taken out, the rest kept as read
    with the breaks between them; `fallback` when nothing else is left. The value itself,
    untouched (None stays None), when no part is small print, so a clean title, estate or
    name is returned exactly as given -- and what comes back never carries small print, so a
    second pass changes nothing.

    A part is what a comma, a '|' or a '/' between words sets apart (_PART_BREAK). One that
    opens in lower case continues the sentence before it, and a sentence carrying small
    print goes from its first part that does -- the whole sentence when it opens in lower
    case too. So a note's tail goes with the note ('..., estimate combined. Please'), and an
    address in front of a note keeps its place ('4 SE-Chapel St, Agent commission=6%plus'
    -> '4 SE-Chapel St'), as does what the part itself said first ('Wine cellar lot included.
    Agent' -> 'Wine cellar lot included'; '12 Smith St Agent commission 3%' -> '12 Smith St').
    A street or place named with an agent word ('12 Agent Street') is no small print."""
    return _strip(value, fallback, AGENT_TEXT, _PART_BREAK, incentive=False)


def shown_incentive(value: Any, amount: Any = None) -> Any:
    """An incentive as people are shown it (8 Oct 2026): without the agent's share -- the
    commission, the agent's bonus -- and with the buyer's offer, purchaser and FIRB words
    and all, kept ('$50,000 purchaser rebate/agent bonus' -> '$50,000 purchaser rebate').
    It parts at more than a title does (_INCENTIVE_BREAK), and keeps an amount said before
    the agent's words, so the buyer's money never goes with the agent's. '' when all of it
    was the agent's; the value itself, untouched, when none of it was. Only the words:
    incentive_amount is as it was read.

    `amount` is the card's incentive_amount (second repair round, 8 Oct 2026). When the words
    as read state it and what the strip would leave does not, the strip is not made and the
    value comes back untouched: the words beside the amount never lose its money
    ('$50,000bonus.Purchaserrebate' with 50000 stays as it is; 'Agent commission 3% plus
    $10,000 rebate' with 10000 too, rather than '' beside $10,000; '$20,000 agent bonus,
    $10,000 purchaser rebate' with 20000 too, so the words still say whose $20,000 it is)."""
    shown = _strip(value, "", AGENT_ONLY_TEXT, _INCENTIVE_BREAK, incentive=True)
    try:
        carried = float(amount or 0)
    except (TypeError, ValueError):
        carried = 0.0
    if (shown is not value and carried > 0 and _states(value, carried)
            and not _states(shown, carried)):
        return value
    return shown


def card_place(card: Dict[str, Any]) -> str:
    """'Melbourne VIC': what a card's title falls back to when all of it was small print."""
    return " ".join(str(card.get(k) or "").strip() for k in ("suburb", "state")
                    if str(card.get(k) or "").strip())


def shown_title(title: Any, card: Dict[str, Any]) -> str:
    """A card's title as people are shown it (8 Oct 2026): without agent small print, and
    when all of it was small print, the card's place -- or with no place either, what a
    card with no address says. kommo_agent titles every candidate with it. Never a key: the
    parity and search-regressions checks recover a card's title as read from it
    (search_regressions.title_as_read, 8 Oct 2026), because lots shown alike can be two."""
    return without_agent_text(title, card_place(card) or NO_ADDRESS)


# ------------------------------------------------------- a title that names no lot

# A TITLE THAT NAMES ITS LOT ('Lot 12', 'Unit 2302', 'TH17') OR A STREET ('12 Hollins Street',
# '1386 Peter Thomson Circuit'), money taken out first. ONE DEFINITION (8 Oct 2026), moved here
# unchanged from search_regressions: its nightly 'labels: N shortlisted card(s) name no lot and
# no street' and parity_check's count read it from here, and so does api/research.py when it
# decides which cards also show their line as listed (as_listed below) -- so the cards that
# gain the line are exactly the cards those checks count, and a change to one is a change to
# both.
_TITLE_LOT = re.compile(r"\b(?:lot|unit|apt|apartment|townhouse|villa|th)\s*[#:.]?\s*[a-z]?\d",
                        re.I)
_TITLE_STREET = re.compile(r"\b\d+[a-z]?(?:\s*/\s*\d+[a-z]?)?\s+[A-Za-z][A-Za-z'\-]{2,}", re.I)
_TITLE_MONEY = re.compile(r"[A-Za-z]?\$\s?\d[\d,]*(?:\.\d+)?|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b")
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")


def names_no_lot_and_no_street(card: Dict[str, Any]) -> bool:
    """True when a card's title names no lot (lot_number or "Lot N" / "Unit N") and no
    street number and name -- 'GOLF $3,497,500', 'PARK RIDGE', '... Click here Click here',
    'Melbourne VIC' (repair round 2: what the row labels were for). `card` is a shortlist or
    needs-confirming card: its lot_number and its title (lot_address) as shown."""
    if " ".join(_NOT_ALNUM.sub(" ", str(card.get("lot_number") or "").lower()).split()):
        return False
    title = _TITLE_MONEY.sub(" ", str(card.get("lot_address") or ""))
    return not (_TITLE_LOT.search(title) or _TITLE_STREET.search(title))


# ------------------------------------------------------------- the line as listed

# THE ROW'S OWN LINE, UNDER A CARD WHOSE TITLE CANNOT TELL IT APART (8 Oct 2026). Since the
# 600-candidate cap was lifted, 1,000 to 1,800 shortlisted cards a state-wide search -- mostly
# apartments: Atlas, Aria, The Brightly, Gallery Group -- are titled only by their place
# ('Melbourne VIC', 'Richmond VIC'), because the unit number sits in the builder's line and
# nowhere else ('45 4504 2 BED + 2 BATH C05.1 North East ...'). Coleen's team could not tell
# those cards apart, or find E-Agent's unit for one. The row labels that were to fix it (Stage
# B) are parked: labels that took part in matching merged wrong lots. This is the line itself,
# as the snapshot stores it (the source's line, cut at 110 characters), DISPLAY ONLY: no key,
# merge, rank, count or page reads it, nor Clare, the Kommo note or a client document.
# A number the title does not already say is what tells one unit of a list from the next
# (its level, unit, area or design number). Money is not: the card shows its price, and a
# line that is only amounts and link words ('$620,000.00 $461,312.00 $0.00 $1,081,312.00 Click
# here Click here') or only the title again ('B-701 3 2 - 126 26 152 $2,100,000') adds nothing.
_NUMBER = re.compile(r"\d+")
AS_LISTED_WHY_TITLE = "the title names a lot or a street"
AS_LISTED_WHY_NO_LINE = "the row stores no line"
AS_LISTED_WHY_SMALL_PRINT = "the line is all agent small print"
AS_LISTED_WHY_NOTHING_NEW = "the line says no number the title does not (money aside)"

# A LINE THE EXTRACTORS CUT SAYS SO (9 Oct 2026, repair round 1). The snapshot stores a source
# line as its first 110 characters (sources/spreadsheet_extract.py and sources/email_inbox.py
# write line[:110], sources/adaptive_extract.py 120), and api/_candidates.py reads a line that
# long as one that may go on (_TRUNCATED_AT; a test keeps the two equal). The first round
# showed the cut line as it stood, with no mark: 435 of the 1,963 lines shown on the 8 Oct
# database were cut, 66 of them inside an amount -- '... 176.2 16.9 193.1 $3,500,00' beside a
# card priced $3,500,000, 'Available $1,537,50', '$1,3' -- and more inside a word ('Settled
# Wes', 'Titl'). A cut amount reads as a price. So a line that long loses its last word when
# the cut may have fallen inside it (the stored line does not end in white space: what the
# cut left of a word or an amount is neither), and ends in CUT_MARK, the ellipsis the
# Research tab's asListedRow cuts with -- one character, so the line shown stays within the
# 110 the card shows and is never cut a second time.
LINE_CUT_AT = 110
CUT_MARK = chr(0x2026)  # the ellipsis
_LAST_WORD = re.compile(r"\s+\S+$")
# What a cut line keeps at its end once its cut word went: _LEAD_TAIL's breaks and joining
# words, and a currency sign standing alone -- a list read from a PDF writes '$ 209.47', so
# the cut word was the amount and the '$' before it is left ('... 90.33 $ 20', St Kilda).
_CUT_TAIL = re.compile(r"(?:[\s,;:+&/.\-$]|\b(?:plus|and|or|with)\b)+$", re.I)
# LINK WORDS AT THE END (9 Oct 2026, repair round 1): a price list's link column read as text
# -- 'Portal Link' (Alete Homes, SA: 34 of the 53 SA lines shown), 'Click here Click here'
# (Gallery Group, QLD), 'https://drive.' -- is a link no one can follow from a card, and its
# digits are no unit's number. It goes from the END of the line shown, and so does what the
# cut left of it ('Portal Li' is 'Portal' once its cut word goes). A word in the middle of the
# line stays as read.
_LINK_TAIL = re.compile(
    r"(?:\s*\b(?:portal\s+link|click\s+here)\b|\s*(?<!\S)(?:https?:|www\.)\S*)+\s*$", re.I)
_CUT_LINK_TAIL = re.compile(r"(?:\s*\b(?:portal|click)\b)\s*$", re.I)


def _numbers(text: Any) -> set:
    """The numbers `text` says outside its money amounts."""
    return set(_NUMBER.findall(_TITLE_MONEY.sub(" ", "" if text is None else str(text))))


def _without_link_tail(text: str, cut: bool) -> str:
    """`text` without the link words at its end (_LINK_TAIL) -- with `cut`, the stored line
    cut at LINE_CUT_AT, also a 'Portal' or 'Click' the cut left -- and the breaks that leaves
    at its end; `text` itself when it ends in none."""
    shown = text
    while True:
        before = shown
        shown = _LINK_TAIL.sub("", shown)
        if cut:
            shown = _CUT_LINK_TAIL.sub("", shown)
        if shown == before:
            break
    return text if shown == text else _LEAD_TAIL.sub("", shown).strip()


def _fitted(text: str) -> str:
    """A cut line's `text` with room for CUT_MARK within LINE_CUT_AT characters: whole words
    off its end until it fits (a line cut at 120, sources/adaptive_extract.py, is longer than
    the card shows, and index.html would cut it again, inside a word), then the breaks and
    a lone currency sign that leaves at its end (_CUT_TAIL). A line of one word is cut at
    the room."""
    room = LINE_CUT_AT - len(CUT_MARK)
    while len(text) > room:
        last = _LAST_WORD.search(text)
        if not last:
            return text[:room]
        text = text[:last.start()]
    return _CUT_TAIL.sub("", text).strip()


def as_listed_line(line: Any, card: Dict[str, Any]):
    """(text, why): the row's own line as listed, for a card whose title names no lot and no
    street, and why not when it is '' (the AS_LISTED_WHY_* sentences). `line` is the card's
    snapshot row's stored lot_address; `card` the card as sent (its lot_number and its shown
    title). The line goes through without_agent_text -- no agent small print on any page --
    with its runs of white space made one space and the link words at its end taken off
    (_LINK_TAIL), and is otherwise as read. A line the extractors cut (LINE_CUT_AT) drops
    what the cut left of its last word and ends in CUT_MARK, within the LINE_CUT_AT
    characters the card shows (_fitted)."""
    if not names_no_lot_and_no_street(card):
        return "", AS_LISTED_WHY_TITLE
    raw = "" if line is None else str(line)
    cut = len(raw) >= LINE_CUT_AT
    if cut and not raw[-1:].isspace():
        last = _LAST_WORD.search(raw)
        if last and raw[:last.start()].strip():
            raw = raw[:last.start()]
    read = " ".join(raw.split())
    if not read:
        return "", AS_LISTED_WHY_NO_LINE
    text = " ".join(str(without_agent_text(read, "") or "").split())
    if not text:
        return "", AS_LISTED_WHY_SMALL_PRINT
    text = _without_link_tail(text, cut)
    if cut:
        text = _fitted(text)
    if not (_numbers(text) - _numbers(card.get("lot_address"))):
        return "", AS_LISTED_WHY_NOTHING_NEW
    if cut:
        text += CUT_MARK
    return text, ""


def as_listed(line: Any, card: Dict[str, Any]) -> str:
    """The card's line as listed (as_listed_line), '' when the card does not need it or it
    would add nothing."""
    return as_listed_line(line, card)[0]
