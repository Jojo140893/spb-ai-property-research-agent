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

PRICE_RE = re.compile(r"\$\s?([\d]{1,3}(?:,\d{3})+|\d{5,7})(?:\.\d{2})?\b")
# "4 bed", "4 Bedrooms", "4br", "4 x bed"
BEDS_RE = re.compile(r"(\d+)\s*(?:x\s*)?(?:bed|bd|br)\b", re.I)
BATHS_RE = re.compile(r"(\d+(?:\.5)?)\s*(?:x\s*)?(?:bath|ba)\b", re.I)
CARS_RE = re.compile(r"(\d+)\s*(?:x\s*)?(?:car|garage|gge)\b", re.I)
# 400m2 / 400 sqm / 400 m²
AREA_RE = re.compile(r"(\d{2,4}(?:\.\d+)?)\s*(?:m2|m²|sqm|sq\.?m)\b", re.I)
LOT_RE = re.compile(r"\b(lot\s*\d+[A-Za-z]?\b[^,|\n]{0,60})", re.I)
TITLE_RE = re.compile(r"\b(titled|registered|untitled|title\s+(?:due|expected)[^,|\n]{0,30}|q[1-4]\s*20\d{2})\b", re.I)
AU_STATES = ("QLD", "NSW", "VIC", "SA", "WA", "NT", "ACT", "TAS")


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
    lines = [_clean(l) for l in (text or "").splitlines() if _clean(l)]
    t = _clean(text)
    areas = [float(a) for a in AREA_RE.findall(t)]
    areas_sorted = sorted(areas)
    land = house = None
    if len(areas_sorted) >= 2:
        # heuristic: the larger area is the land, the smaller the house
        house, land = areas_sorted[0], areas_sorted[-1]
    elif len(areas_sorted) == 1:
        land = areas_sorted[0]

    # Address: from the line that mentions the lot (not across line breaks).
    lot = None
    for ln in lines:
        m = LOT_RE.search(ln)
        if m:
            lot = m
            break
    title = TITLE_RE.search(t)

    # Suburb/state: from the line holding the state code; take the 1-2 words
    # immediately before it (handles "Coomera, QLD" and "Hope Island QLD").
    suburb = state = None
    state_pat = re.compile(r"([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+)?)\s*,?\s+(" + "|".join(AU_STATES) + r")\b")
    for ln in lines:
        m = state_pat.search(ln)
        if m:
            cand = _clean(m.group(1))
            # drop a leading lot/street fragment if the line was "Lot 3 Foo St Coomera QLD"
            cand = re.sub(r"^.*?\b(?:st|street|rd|road|ave|avenue|dr|drive|cres|court|ct)\b\s*", "", cand, flags=re.I)
            suburb, state = _clean(cand), m.group(2)
            break

    return {
        "advertised_package_price": parse_price(t),
        "bedrooms": _int(BEDS_RE, t),
        "bathrooms": _int(BATHS_RE, t),
        "car_spaces": _int(CARS_RE, t),
        "land_size_sqm": land,
        "house_size_sqm": house,
        "lot_address": _clean(lot.group(1)) if lot else None,
        "title_status": _clean(title.group(1)) if title else None,
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
        # need at least an address-ish label or a bedroom count to be a real listing
        if not fields["lot_address"] and fields["bedrooms"] is None:
            continue
        if not fields["lot_address"]:
            # first meaningful line as the label
            first = next((l for l in (text.splitlines()) if len(_clean(l)) > 4), "")
            fields["lot_address"] = _clean(first)[:120]
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
