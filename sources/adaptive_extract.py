"""
Adaptive listing extractor — finds property listings on ANY portal layout without
hand-mapped CSS selectors.

Why: every builder portal has a different DOM. Hand-mapping selectors per portal
is brittle and requires someone with a login to inspect each page. Instead this
infers the listing structure from the page itself:

  1. Find every element containing a price ($xxx,xxx).
  2. Walk up to the smallest ancestor that looks like a repeated "card" (i.e. its
     parent holds several siblings with the same signature) -> that's the listing
     container, whatever the site calls it.
  3. Pull fields out of each card's text with patterns (price, beds/baths/cars,
     land/house m2, lot/address, suburb, title status) plus its own <a href>.

This is heuristic, so it reports a confidence per listing and skips anything that
does not at least have a price plus one of (address, beds). It never invents
values: a field it cannot find stays None.
"""

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

PRICE_RE = re.compile(r"\$\s*(\d{1,3}(?:[\s,]+\d{3})+|\d{4,7})(?:\.\d{2})?")
# "4 bed", "4 Bedrooms", "4br", "4 x bed"
BEDS_RE = re.compile(r"(\d+)\s*(?:x\s*)?(?:bed|bd|br)\b", re.I)
BATHS_RE = re.compile(r"(\d+(?:\.5)?)\s*(?:x\s*)?(?:bath|ba)\b", re.I)
CARS_RE = re.compile(r"(\d+)\s*(?:x\s*)?(?:car|garage|gge)\b", re.I)
# 400m2 / 400 sqm / 400 m²
AREA_RE = re.compile(r"(\d{2,4}(?:\.\d+)?)\s*(?:m2|m²|sqm|sq\.?m)\b", re.I)
LOT_RE = re.compile(r"\blot\s*\d+[A-Za-z]?\b", re.I)
TITLE_RE = re.compile(
    r"\b(titled|registered|untitled"
    r"|title\s+(?:due|expected)[^,|\n]{0,30}"
    r"|q[1-4][\s-]*(?:20)?\d{2}"                       # Q1 2026, Q1-2026, Q1-26
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s-]*(?:20)?\d{2}"  # Sep-26
    r"|tbc)\b", re.I)
AU_STATES = ("QLD", "NSW", "VIC", "SA", "WA", "NT", "ACT", "TAS")

# Explicitly labelled fields (common on real portal stock lists), e.g.
# "HOUSE SIZE : 209 m2", "LAND PRICE : $ 466,000", "PACKAGE $ 929,934",
# "LAND REGISTRATION : Aug 2026".
LBL_LAND_SIZE = re.compile(r"land\s*size\s*:?\s*([\d.]+)\s*(?:m2|m²|sqm)", re.I)
LBL_HOUSE_SIZE = re.compile(r"(?:house|home|build)\s*size\s*:?\s*([\d.]+)\s*(?:m2|m²|sqm)", re.I)
LBL_LAND_PRICE = re.compile(r"land\s*price\s*:?\s*\$?\s*([\d,]+)", re.I)
LBL_BUILD_PRICE = re.compile(r"(?:house|home|build)\s*price\s*:?\s*\$?\s*([\d,]+)", re.I)
LBL_PACKAGE_PRICE = re.compile(r"(?:package|total)\s*(?:price)?\s*:?\s*\$?\s*([\d,]+)", re.I)
LBL_REGISTRATION = re.compile(
    r"(?:land\s*)?registration\s*:?\s*"
    r"([A-Za-z]{3,9}[\s-]*(?:20)?\d{2}|q[1-4][\s-]*(?:20)?\d{2})", re.I)
# Unlabelled bed/bath/car triple: three small numbers on their own lines/cells.
BARE_TRIPLE_RE = re.compile(r"^\s*(\d{1,2})\s*$")


def _money(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        # pdfplumber sometimes emits "$ 1 ,757,400" — strip spaces as well as commas,
        # otherwise that row's $1,757,400 package was read as $1,035,000.
        v = float(re.sub(r"[,\s]", "", s))
        return v if v > 0 else None
    except ValueError:
        return None


# Marks a money figure as rent or a yield rather than part of the package price.
_RENTY = re.compile(r"p\.?w\.?|per\s*week|weekly|rent|yield|p\.?a\.?|per\s*annum", re.I)


def _ordered_package_prices(text: str) -> List[float]:
    """Plausible package-component prices, in the order they appear.

    Discards any figure that is immediately followed by a percentage (that is the
    annual-rent/yield pair in the QLD stocklists) or that sits next to rent wording.
    """
    out: List[float] = []
    for m in PRICE_RE.finditer(text):
        v = _money(m.group(1))
        if not v or not (50_000 <= v <= 5_000_000):
            continue
        after = text[m.end():m.end() + 14]
        before = text[max(0, m.start() - 22):m.start()]
        if re.search(r"^\s*\d+(?:\.\d+)?\s*%", after):      # "$121,160 8.03%" -> annual rent
            continue
        if _RENTY.search(after) or _RENTY.search(before):
            continue
        out.append(v)
    return out


_GEO_SUBURBS = None


def _suburb_lookup():
    """Lazy set of known AU suburb names (from data/au_suburbs.csv) for matching
    suburb lines that carry no state code."""
    global _GEO_SUBURBS
    if _GEO_SUBURBS is None:
        try:
            from geo import SuburbGeoIndex
            idx = SuburbGeoIndex()
            _GEO_SUBURBS = {s.lower() for (s, _st) in idx._index.keys()} if idx.loaded else set()
        except Exception:
            _GEO_SUBURBS = set()
    return _GEO_SUBURBS


def _clean(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def parse_price(text: str) -> Optional[float]:
    m = PRICE_RE.search(text or "")
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    # ignore obvious non-package prices (deposits, weekly rents, $/sqm)
    return val if 50_000 <= val <= 5_000_000 else None


def _int(rx, text) -> Optional[int]:
    m = rx.search(text or "")
    if not m:
        return None
    try:
        return int(float(m.group(1)))
    except ValueError:
        return None


def parse_fields(text: str) -> Dict[str, Any]:
    """Extract listing fields from a card's visible text.

    Line-aware: address and suburb are read from the individual line they appear
    on, so they never bleed across line breaks into neighbouring fields.
    """
    raw_lines = [l for l in (text or "").replace("\t", "\n").splitlines()]
    lines = [_clean(l) for l in raw_lines if _clean(l)]
    t = _clean(text)

    # --- sizes: prefer explicit labels, else infer from bare areas ---
    m_land, m_house = LBL_LAND_SIZE.search(t), LBL_HOUSE_SIZE.search(t)
    land = float(m_land.group(1)) if m_land else None
    house = float(m_house.group(1)) if m_house else None
    if land is None and house is None:
        areas_sorted = sorted(float(a) for a in AREA_RE.findall(t))
        if len(areas_sorted) >= 2:
            house, land = areas_sorted[0], areas_sorted[-1]
        elif len(areas_sorted) == 1:
            land = areas_sorted[0]

    # Address: the whole cell/line containing "Lot N" (e.g. "Beaumoor Estate, Lot 519").
    lot_address = None
    for ln in lines:
        if LOT_RE.search(ln):
            lot_address = ln[:120]
            break

    # Title / registration status
    reg = LBL_REGISTRATION.search(t)
    title = TITLE_RE.search(t)
    title_status = _clean(reg.group(1)) if reg else (_clean(title.group(1)) if title else None)

    # Suburb/state: prefer an explicit state code; else match a line against the
    # known-AU-suburb dataset (portals often print just "BEAUDESERT").
    suburb = state = None
    state_pat = re.compile(r"([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+)?)\s*,?\s+(" + "|".join(AU_STATES) + r")\b")
    for ln in lines:
        m = state_pat.search(ln)
        if m:
            cand = re.sub(r"^.*?\b(?:st|street|rd|road|ave|avenue|dr|drive|cres|court|ct)\b\s*", "",
                          _clean(m.group(1)), flags=re.I)
            suburb, state = _clean(cand), m.group(2)
            break
    if not suburb:
        known = _suburb_lookup()
        for ln in lines[:6]:  # suburb is normally near the top of a card
            cand = _clean(re.sub(r"[^A-Za-z '\-]", "", ln))
            if 3 <= len(cand) <= 30 and cand.lower() in known:
                suburb = cand.title()
                break

    # Beds/baths/cars: labelled if possible, else the bare "4 / 2 / 2" triple.
    beds, baths, cars = _int(BEDS_RE, t), _int(BATHS_RE, t), _int(CARS_RE, t)
    if beds is None:
        bare = [int(m.group(1)) for ln in lines if (m := BARE_TRIPLE_RE.match(ln)) and int(m.group(1)) <= 12]
        if len(bare) >= 3:
            beds, baths, cars = bare[0], bare[1], bare[2]
        elif len(bare) == 2:
            beds, baths = bare[0], bare[1]

    # Prices: prefer a labelled package/total price; keep land + build separately
    # (SOP Step 6 needs the breakdown, not just the headline).
    pkg = _money(LBL_PACKAGE_PRICE.search(t).group(1)) if LBL_PACKAGE_PRICE.search(t) else None
    land_price = _money(LBL_LAND_PRICE.search(t).group(1)) if LBL_LAND_PRICE.search(t) else None
    build_price = _money(LBL_BUILD_PRICE.search(t).group(1)) if LBL_BUILD_PRICE.search(t) else None

    # A stocklist row often carries Land Price, Build Price AND Total Price with no
    # labels. Prices are read in DOCUMENT ORDER (that is the column order in every
    # observed stocklist: land, build, total) and any figure that is actually rent or
    # a yield is discarded first — the QLD dual-occupancy rows carry weekly rent,
    # annual rent AND a yield %, and sorting numerically used to store the annual
    # rent ($121,160) as the land price of an $850,000 lot.
    ordered = _ordered_package_prices(t)
    price = pkg
    if price is None and ordered:
        price = max(ordered)
    if price is None and land_price and build_price:
        price = land_price + build_price

    if not (land_price and build_price) and len(ordered) >= 3 and price:
        # Only accept an unlabelled split when it ADDS UP: land + build == total.
        for i in range(len(ordered) - 1):
            a, b = ordered[i], ordered[i + 1]
            if a + b and abs((a + b) - price) <= max(2.0, price * 0.005):
                land_price = land_price or a          # document order: land before build
                build_price = build_price or b
                break

    # Rows where several money figures are mixed together (rent, yield, deposit) are
    # the ones that mis-parse. Flag them rather than presenting them as authoritative.
    price_confidence = 1.0
    if len(ordered) >= 3 and not (land_price and build_price):
        price_confidence = 0.4
    if _RENTY.search(t) and not (LBL_LAND_PRICE.search(t) or LBL_BUILD_PRICE.search(t)):
        price_confidence = min(price_confidence, 0.6)

    return {
        "advertised_package_price": price,
        "land_price": land_price,
        "build_price": build_price,
        # <1.0 means several money figures were jumbled in this row (rent, yield,
        # deposit) and the split could not be verified — surfaced so a low-confidence
        # row is never presented to a client as authoritative.
        "price_confidence": price_confidence,
        "bedrooms": beds,
        "bathrooms": baths,
        "car_spaces": cars,
        "land_size_sqm": land,
        "house_size_sqm": house,
        "lot_address": lot_address,
        "title_status": title_status,
        "suburb": suburb,
        "state": state,
    }


# JS that finds repeated listing "cards" on the page and returns their text + link.
# Runs in the page context via Playwright page.evaluate().
FIND_CARDS_JS = r"""
() => {
  const priceRe = /\$\s?(\d{1,3}(,\d{3})+|\d{5,7})\b/;
  const sig = el => el.tagName + ':' + (el.className || '').toString().split(/\s+/).slice(0,3).join('.');
  // all elements whose OWN text (not just descendants) mentions a price
  const priced = [...document.querySelectorAll('body *')].filter(el => {
    if (!el.innerText) return false;
    if (el.children.length > 12) return false;              // too big to be one card
    const t = el.innerText;
    return priceRe.test(t) && t.length < 1200;
  });
  // for each, climb to the ancestor that repeats among siblings => the card
  const cands = new Map();
  for (const el of priced) {
    let node = el;
    for (let depth = 0; depth < 6 && node && node.parentElement; depth++) {
      const parent = node.parentElement;
      const mySig = sig(node);
      const twins = [...parent.children].filter(c => sig(c) === mySig);
      if (twins.length >= 2 && node.innerText && priceRe.test(node.innerText)) {
        const key = parent.tagName + '>' + mySig;
        if (!cands.has(key)) cands.set(key, twins);
        break;
      }
      node = parent;
    }
  }
  // choose the group with the most cards that contain a price
  let best = [];
  for (const group of cands.values()) {
    const withPrice = group.filter(g => priceRe.test(g.innerText || ''));
    if (withPrice.length > best.length) best = withPrice;
  }
  // fall back: individual priced blocks if no repeated structure was detected
  if (best.length === 0) best = priced.slice(0, 60);
  return best.slice(0, 200).map(el => {
    const a = el.querySelector('a[href]') || el.closest('a[href]');
    const img = el.querySelector('img[src]');
    return {
      text: el.innerText || '',
      href: a ? a.href : '',
      img: img ? img.src : '',
    };
  });
}
"""


def extract_listings(page, builder_hint: str = "", state_hint: str = "") -> List[Dict[str, Any]]:
    """Return listings found on the currently-loaded page. Empty list if none."""
    try:
        raw_cards = page.evaluate(FIND_CARDS_JS)
    except Exception:
        return []

    seen = set()
    out: List[Dict[str, Any]] = []
    for card in raw_cards or []:
        text = card.get("text") or ""
        fields = parse_fields(text)
        if not fields["advertised_package_price"]:
            continue
        # A listing needs a price plus SOME identity. Prefer a lot number or a bed
        # count, but many portals label cards by product/estate only
        # ("Single Contract - Fraser Rise  $660,000"), so a short descriptive card is
        # accepted too — with lower confidence. Filter/summary widgets are excluded
        # by length and by their give-away wording.
        if not fields["lot_address"]:
            first = next((l for l in text.splitlines() if len(_clean(l)) > 4), "")
            first = _clean(first)[:120]
            looks_like_widget = bool(re.search(
                r"price\s*range|filters?\b|sort by|results?\b|showing\s+\d|per page", text, re.I))
            if fields["bedrooms"] is None and (looks_like_widget or not (4 < len(first) <= 120)
                                               or len(text) > 600):
                continue
            fields["lot_address"] = first
        key = (fields["lot_address"].lower(), fields["advertised_package_price"], (fields["suburb"] or "").lower())
        if key in seen:
            continue
        seen.add(key)
        filled = sum(1 for k in ("bedrooms", "bathrooms", "car_spaces", "land_size_sqm", "suburb") if fields.get(k))
        out.append({
            **fields,
            "builder_name": builder_hint,
            "state": fields.get("state") or state_hint,
            "source_url_or_ref": card.get("href") or "",
            "extraction_confidence": round(min(1.0, 0.5 + 0.1 * filled), 2),
        })
    return out
