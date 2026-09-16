"""
POST /api/research on Vercel — the Research & Scoring tab, without the local app.

Same request and same response as server.py's do_POST, so index.html needs no change:
it posts {"client_brief": {...}} and reads status / research_record_id /
shortlist_count / rejected_count / rejected_log / shortlist[] / reports /
search_area / builder_coverage / client_report_html / qa_passed / qa_failures /
kommo_payload. It also returns location_resolution (see _location_resolver), which
server.py does not.

The one difference is where the candidates come from. Locally, run_property_research
is called with candidate_packages=None and crawls E-Agent and the builder portals.
Here it is ALWAYS called with packages read out of the deployed stock snapshot, which
makes it skip the hybrid-search block entirely (kommo_agent.py:73). Three separate
locks keep it that way:

  1. candidate_packages is never None and never empty (an empty list is falsy and
     would restart the crawl) — an empty pool returns without calling the pipeline;
  2. every live source's search()/verify() is replaced with a raiser
     (_bootstrap._disarm_sources);
  3. no credential is deployed, so a crawl could not authenticate even if it ran.

Read-only filesystem: config's writable paths are re-pointed at /tmp and the audit DB
is a fresh ephemeral file there (see _bootstrap). The stock snapshot is opened
read-only and never written.
"""

import json
import os
import re
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _bootstrap
import _candidates

# The rejection log is rendered as one card per entry, so an uncapped log would post
# hundreds of DOM nodes. The count stays exact; the list is trimmed and says so.
MAX_REJECTED_LOG = int(os.environ.get("SPB_MAX_REJECTED_LOG", "30"))


class BadRequest(ValueError):
    """The request itself was malformed — answered 400, not 500."""


def _shortlist_entry(p, availability=None):
    """Identical field-for-field to server.py's shortlist serialisation, plus the two
    availability fields below.

    `availability` is {content_hash: {'availability_by_the_sheet', 'availability_status'}}
    from the packages and snapshot rows behind this search, for a CandidateProperty that
    does not carry them itself (see _availability_by_hash).
    """
    _known = (availability or {}).get(getattr(p, 'content_hash', '') or '') or {}
    # EITHER SOURCE SAYING "ONLY BY THE SHEET" IS ENOUGH. Both come from the same snapshot
    # row, so they only disagree through a bug -- e.g. a CandidateProperty field added with
    # a False default that nothing copies onto it. Clare reads True as "on the builder's
    # list, a consultant confirms" and False as "showing as available"
    # (vapi-kommo-hook lib/tools/verify.js), so the costly mistake is a False too many.
    _by_the_sheet = (bool(getattr(p, 'availability_by_the_sheet', False))
                     or bool(_known.get('availability_by_the_sheet', False)))
    # A blank on the candidate is "not carried", not "the source said nothing": both come
    # from the same snapshot row, so the row's own statement is the fallback.
    _status = (getattr(p, 'availability_status', None)
               or _known.get('availability_status', ''))
    return {
        'property_id': p.property_id,
        # NOT property_id — that is "PROP-{n}" assigned per search, so it names a
        # different building in every run. The browser keys its selection on this so a
        # consultant can mark a lot, change the brief, search again, and still be holding
        # the same lot. See SNAPSHOT_FIELDS in api/_candidates.py.
        'content_hash': getattr(p, 'content_hash', '') or '',
        # The Rebate / Effective Price columns of the comparison one-pager. Amount is 0
        # when the source only described an incentive in words; the text travels with it
        # so the document can say what was offered without inventing a figure.
        'incentive_amount': getattr(p, 'incentive_amount', 0.0) or 0.0,
        'incentive_text': getattr(p, 'incentive_text', '') or '',
        # Rows on the comparison Coleen's team sends: Estate, Land Price, Build Price.
        'estate_context': getattr(p, 'estate_context', '') or '',
        'land_price': p.price_breakdown.land_price,
        'build_price': p.price_breakdown.build_price,
        'lot_address': p.lot_address,
        # The lot as the source stated it, '' when it did not. lot_address above is often
        # only a street address, and Clare's availability check (vapi-kommo-hook) reads
        # this shortlist: it can match a caller's "lot 5532" only against a lot it is given.
        # See CandidateProperty.lot_number.
        'lot_number': getattr(p, 'lot_number', '') or '',
        'suburb': p.suburb,
        'state': p.state,
        'builder_name': p.builder_name,
        'house_design': p.house_design,
        'bedrooms': p.bedrooms,
        'bathrooms': p.bathrooms,
        'car_spaces': p.car_spaces,
        'storeys': p.storeys,
        'land_size_sqm': p.land_size_sqm,
        'house_size_sqm': p.house_size_sqm,
        'title_status': p.title_status,
        'verification_status': p.verification_status.value,
        'consultant_approved': p.consultant_approved,
        'expected_title_date': p.expected_title_date,
        'realistic_total_price': p.price_breakdown.realistic_total_price,
        'advertised_package_price': p.price_breakdown.advertised_package_price,
        'estimated_additional_costs': p.price_breakdown.estimated_additional_costs,
        'turnkey_status': p.price_breakdown.turnkey_status.value,
        'missing_inclusions': p.price_breakdown.missing_inclusions,
        'estimated_rent_min': p.estimated_rent_weekly_min,
        'estimated_rent_max': p.estimated_rent_weekly_max,
        'amenities_summary': p.amenities_summary,
        'builder_confidence_rating': p.builder_confidence_rating,
        'benchmark': p.benchmark,
        # Where a consultant can go to see this listing at its source. Colin lost eight
        # minutes of the 5 Aug call hunting one lot; the label says what the link opens
        # so "price list" is never mistaken for "the lot". See provenance.py.
        'source_link': getattr(p, 'source_link', None),
        # When this listing was last actually seen at the source. Proxima's sign-in
        # expired on 3 Aug and the daily harvest has failed silently every night since,
        # so its rows kept being served as current while going three days stale. A
        # consultant about to send one to a buyer has to be able to see that.
        'date_checked': getattr(p, 'date_checked', ''),
        # THE SOURCE NEVER SAID THE WORD. True when this lot is on a price list with no
        # status column anywhere, so being listed is the only evidence it is for sale
        # (_candidates.py, "A SHEET WITH NO STATUS COLUMN"). The card shows "Confirm
        # availability" for it, and Clare must not call it "available" on its strength.
        'availability_by_the_sheet': bool(_by_the_sheet),
        # The availability exactly as the source row stated it ('' when it stated none).
        'availability_status': str(_status or ''),
        # Benchmark A: could this buyer find comparable stock cheaper right now?
        # Attached by run_research below; None until a comparables provider is licensed,
        # which the card renders as "couldn't verify" rather than as any kind of claim.
        'competitive': getattr(p, 'competitive', None),
        'distance_km_from_target': p.distance_km_from_target,
        'score': p.scoring.total_score if p.scoring else 0,
        'scoring_details': {
            'budget_fit': p.scoring.budget_fit,
            'requirement_match': p.scoring.requirement_match,
            'value_competitiveness': p.scoring.value_competitiveness,
            'location_amenity': p.scoring.location_amenity,
            'builder_confidence': p.scoring.builder_confidence,
            'suitability_score': p.scoring.suitability_score,
            'risk_score': p.scoring.risk_score,
        } if p.scoring else {},
        # Things to confirm before this reaches a client. They cost points but reject
        # nothing, and a SHORTLISTED lot is exactly where they matter — "storeys not
        # stated, confirm with the builder" is advice a consultant has to act on.
        'advisories': p.scoring.advisories if p.scoring else '',
        'recommendation': p.recommendation.value,
        'recommendation_reason': p.recommendation_reason,
        'risks': [{'name': r.risk_name, 'rating': r.rating.value,
                   'description': r.description, 'mitigation': r.proposed_mitigation}
                  for r in p.risks],
    }


# Bounded so a large shortlist cannot balloon the response: 5 options is 10 pairs.
MAX_COMPARISON_PAIRS = int(os.environ.get("SPB_MAX_COMPARISON_PAIRS", "10"))


def _pairwise_comparisons(brief_dict, shortlist):
    """{"0-1": html} for each pairing, so the browser can switch instantly.

    Never fatal: a comparison is a convenience on top of a completed search, and a
    formatting error in it must not cost the client their results.
    """
    if len(shortlist) < 2:
        return {}
    try:
        from brief_parser import ClientBriefParser
        from comparison_report import ComparisonReportGenerator
        brief = ClientBriefParser.parse_dict(brief_dict)
    except Exception as exc:                                       # noqa: BLE001
        print("comparison reports unavailable: %s" % exc, file=sys.stderr)
        return {}
    out = {}
    for i in range(len(shortlist)):
        for j in range(i + 1, len(shortlist)):
            if len(out) >= MAX_COMPARISON_PAIRS:
                return out
            try:
                out["%d-%d" % (i, j)] = ComparisonReportGenerator.generate_html(
                    brief, [shortlist[i], shortlist[j]])
            except Exception as exc:                               # noqa: BLE001
                print("comparison %d-%d failed: %s" % (i, j, exc), file=sys.stderr)
    return out


def _empty_kommo_payload(brief_dict, record_id, coverage_reason):
    """What the pipeline would have produced with nothing to shortlist."""
    from datetime import datetime
    # `or`, not a get() default: the form sends a blank name as '', which a default
    # cannot catch, and the note then read "Client: " with nothing after it.
    client = brief_dict.get('client_name') or 'Unnamed Client'
    return {
        'research_record_id': record_id,
        'kommo_lead_status': 'Awaiting Consultant Review',
        'custom_fields_update': {
            'research_completed_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'top_property_match': 'No qualifying properties found',
            'top_property_price': 0,
            'top_property_score': 0,
            'candidates_reviewed': 0,
        },
        'kommo_internal_note': "SPB Property Research Completed\nRecord ID: %s\n"
                               "Client: %s\n\nNo stored listing could be scored.\n%s"
                               % (record_id, client, coverage_reason),
        'consultant_review_task': {
            'task_type': 'Consultant Review & Approval',
            'title': "Review Property Research for %s" % client,
            'due_date_hours': 24,
            'status': 'Pending Review',
        },
    }


# Brief fields that must be a LIST of strings downstream. A caller that sends one suburb
# as a bare value is being reasonable; two separate code paths crashed on it.
_LIST_FIELDS = ("primary_suburbs", "secondary_suburbs", "must_have_features",
                "nice_to_have_features", "excluded_suburbs", "preferred_builders")


# The form's "no maximum": index.html sends Number.MAX_SAFE_INTEGER for a blank Max box.
NO_CEILING = 9007199254740991
# A figure from 1 to 9,999 in a price box is thousands. No listing is priced under
# $10,000 (the lowest of 12,153 is $50,000), so "750" can only mean $750,000. index.html's
# budget echo applies the same rule (THOUSANDS_BELOW) -- keep the two identical, or the
# echo promises a search that is not run.
THOUSANDS_BELOW = 10000
_MONEY_FIELDS = ("budget_min", "budget_max", "preferred_spending_cap")
# "$750,000", "750k", "1.2m", "750 000", "1.2 million".
_MONEY_TEXT = re.compile(r"^(\d+(?:\.\d+)?|\.\d+)(k|thousand|m|mil|million)?$")
_MONEY_SUFFIX = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mil": 1e6, "million": 1e6}

# A state, named or abbreviated, as a consultant might type it into Target Suburbs. None
# of these is a locality anywhere in data/au_suburbs.csv, so reading one as a state can
# never take a real suburb away from a search.
STATE_NAMES = {
    "queensland": "QLD", "qld": "QLD",
    "new south wales": "NSW", "nsw": "NSW",
    "victoria": "VIC", "vic": "VIC",
    "south australia": "SA", "sa": "SA",
    "western australia": "WA", "wa": "WA",
    "tasmania": "TAS", "tas": "TAS",
    "northern territory": "NT", "nt": "NT",
    "australian capital territory": "ACT", "act": "ACT",
}


# What brief_used and the record id say for a blank state: a search of every state.
ANY_STATE = "Any state"


def _state_named(text):
    """'QLD' for "Queensland", " qld ", "QUEENSLAND"; '' for anything else."""
    return STATE_NAMES.get(" ".join(str(text or "").split()).lower(), "")


def _tidy_number(value):
    """750000.0 -> 750000, so what the response echoes reads the way it was typed."""
    value = round(float(value), 2)
    return int(value) if value.is_integer() else value


def _read_money(value):
    """(amount, how) for one price box. how is one of:

      'missing'     nothing was given (absent, None, blank text)
      'number'      a number, used as it is
      'thousands'   a number from 1 to 9,999, read as thousands
      'text'        text that reads as an amount ("$750,000", "750k", "1.2m")
      'unreadable'  something that is not an amount ("abc", true, a list, NaN)
    """
    if value is None:
        return None, "missing"
    if isinstance(value, bool):
        return None, "unreadable"
    how = "number"
    unit = False
    if isinstance(value, str):
        text = value.strip().lower().replace("$", "").replace(",", "")
        text = "".join(text.split())
        if not text:
            return None, "missing"
        found = _MONEY_TEXT.match(text)
        if not found:
            return None, "unreadable"
        amount = float(found.group(1)) * _MONEY_SUFFIX.get(found.group(2) or "", 1.0)
        # A stated unit is not read as thousands a second time.
        unit = bool(found.group(2))
        how = "text"
    elif isinstance(value, (int, float)):
        try:
            amount = float(value)
        except OverflowError:
            return None, "unreadable"
    else:
        return None, "unreadable"
    # A unit too goes through these two checks: "999...9k" (400 nines) is infinity, which
    # brief_parser reads as a $0 ceiling and json.dumps writes as `Infinity` -- not JSON a
    # browser can parse.
    if amount != amount or amount in (float("inf"), float("-inf")):
        return None, "unreadable"
    if not unit and 1 <= amount < THOUSANDS_BELOW:
        return _tidy_number(amount * 1000), "thousands"
    return (_tidy_number(amount) if amount < NO_CEILING else NO_CEILING), how


def _shown(raw):
    """A price box's value as sent, safe to echo in JSON: NaN and infinities (json.loads
    accepts both) would make json.dumps write `NaN` / `Infinity`, and the browser's
    JSON.parse then rejects the WHOLE response -- the search results with it."""
    if raw is None:
        return None
    if isinstance(raw, float) and (raw != raw or raw in (float("inf"), float("-inf"))):
        return str(raw)
    if isinstance(raw, (str, int, float)) and not isinstance(raw, bool):
        return raw
    return str(raw)


def _normalised_money(out, adjustments):
    """Price boxes read the way a consultant meant them, each change named."""
    for field in _MONEY_FIELDS:
        present = field in out
        raw = out.get(field)
        amount, how = _read_money(raw)
        shown = _shown(raw)
        if field == "budget_max":
            if how in ("missing", "unreadable") or (amount is not None and amount <= 0):
                # NO MAXIMUM, NOT A $0 CEILING. brief_parser reads a missing budget_max as
                # 0.0 and the scorer then rejects every listing as over budget: QLD with
                # no budget_max key returned 0 while the same search with the form's
                # blank Max returned 378.
                out[field] = NO_CEILING
                adjustments.append({
                    "field": field, "code": "no_budget_max", "from": shown,
                    "to": NO_CEILING,
                    "why": ("could not be read as a price, so no price ceiling was applied"
                            if how == "unreadable" else
                            "no maximum price was given, so no price ceiling was applied"
                            if how == "missing" or not present else
                            "a maximum of $0 or less would rule out every listing, so no "
                            "price ceiling was applied")})
                continue
        elif how == "missing":
            continue
        elif how == "unreadable" or (amount is not None and amount < 0):
            if field == "budget_min":
                out[field] = 0
                why = "could not be read as a price, so no minimum was applied"
            else:
                # The parser falls back to the maximum when there is no preferred price.
                out.pop(field, None)
                why = "could not be read as a price, so the maximum was used instead"
            adjustments.append({"field": field, "code": "budget_unreadable",
                                "from": shown, "to": out.get(field), "why": why})
            continue
        if how == "thousands":
            out[field] = amount
            adjustments.append({
                "field": field, "code": "budget_in_thousands", "from": shown, "to": amount,
                "why": "a figure under %s is read as thousands"
                       % format(THOUSANDS_BELOW, ",")})
        elif how == "text":
            out[field] = amount
            adjustments.append({"field": field, "code": "budget_from_text", "from": shown,
                                "to": amount, "why": "read from the text typed"})
        elif amount != raw:
            # A number that needed no reading, only tidying (750000.0, or past the
            # form's own no-maximum): not worth a note.
            out[field] = amount
    low = _read_money(out.get("budget_min"))[0] or 0
    high = _read_money(out.get("budget_max"))[0]
    if low > 0 and high is not None and 0 < high < NO_CEILING and low > high:
        # SWAPPED, NOT REFUSED. "750000 to 300000" is a range typed the wrong way round;
        # searched as typed it asks for listings above $750,000 and below $300,000 at
        # once, which is nothing at all.
        out["budget_min"], out["budget_max"] = high, low
        adjustments.append({
            "field": "budget_min/budget_max", "code": "min_max_swapped",
            "from": [low, high], "to": [high, low],
            "why": "the minimum was above the maximum, so the two were swapped"})
        if _read_money(out.get("preferred_spending_cap"))[0] == high:
            # The form sends the Max box as the preferred price when Preferred is blank
            # (index.html runResearchAgent), so it arrives equal to the maximum AS TYPED.
            # Left behind, it would sit at the new minimum and grade every lot above
            # $300,000 down on budget: the same lots as the range typed the right way
            # round, but scored and ranked differently.
            out["preferred_spending_cap"] = low
            adjustments.append({
                "field": "preferred_spending_cap", "code": "preferred_follows_swap",
                "from": high, "to": low,
                "why": "it was the maximum as typed, so it moved with the swap"})


def _normalised_location(out, adjustments):
    """A state typed where a suburb goes, a full state name, a radius with no origin."""
    state = str(out.get("state") or "").strip()
    if state.lower() == ANY_STATE.lower():
        # This endpoint's own label for a blank state (brief_used), so a logged search can
        # be posted back exactly as logged. Not a slip, so not noted.
        out["state"] = state = ""
    named = _state_named(state)
    if named and named != state.upper():
        # "Queensland" in the State field matched no row's "QLD" and emptied the search.
        out["state"] = named
        adjustments.append({"field": "state", "code": "state_full_name", "from": state,
                            "to": named, "why": "read as the state's code"})
    current = str(out.get("state") or "").strip().upper()

    suburbs = out.get("primary_suburbs")
    if isinstance(suburbs, list) and suburbs:
        kept = []
        for said in suburbs:
            code = _state_named(said)
            if not code:
                kept.append(said)
                continue
            if not current:
                # "Any state" left selected and "Queensland" typed as the suburb: the
                # search was for a suburb called Queensland, in every state -- 0 results.
                current = code
                out["state"] = code
                adjustments.append({
                    "field": "state", "code": "state_typed_as_suburb", "said": said,
                    "from": "", "to": code,
                    "why": '"%s" in Target suburbs is a state, so %s was searched'
                           % (said, code)})
            elif code == current:
                adjustments.append({
                    "field": "primary_suburbs", "code": "state_typed_as_suburb",
                    "said": said, "from": [said], "to": [],
                    "why": '"%s" is the selected state, not a suburb, so the whole of %s '
                           'was searched' % (said, code)})
            else:
                # Two different answers to "which state"; the consultant's own State
                # choice stands, and the typed name is searched exactly as it was typed.
                kept.append(said)
                adjustments.append({
                    "field": "primary_suburbs", "code": "state_name_conflicts",
                    "said": said,
                    "note": '"%s" names %s, but the selected state is %s; it was searched '
                            'as typed' % (said, code, current)})
        out["primary_suburbs"] = kept

    from brief_parser import coerce_number
    # Read the way brief_parser reads it -- a radius is kilometres, never thousands.
    radius = coerce_number(out.get("search_radius_km"), 0) or 0
    if radius > 0 and not out.get("primary_suburbs"):
        # The scorer already ignores it (scoring_engine: "A radius with NO origin cannot
        # be applied"), so this changes no result -- it makes "within 20 km of nowhere"
        # visible instead of silently meaning the whole state.
        adjustments.append({"field": "search_radius_km", "code": "radius_without_suburb",
                            "from": out.get("search_radius_km"), "to": 0,
                            "why": "radius ignored: no suburb to measure from"})
        out["search_radius_km"] = 0


def _normalised_brief(brief):
    """(brief, brief_adjustments): the caller's brief in the shape every consumer below
    already assumes, read the way the consultant meant it, and every change named.

    Done ONCE here rather than defensively at each reader, because the two crashes this
    fixes were in different files reading the same field two different ways:

        primary_suburbs = 5   ->  brief_parser.py:86  iterated it        (not iterable)
                              ->  research.py:220     took suburbs[0]    (not subscriptable)

    The second is the worse of the two: it is in the ZERO-RESULTS branch, which returns
    early and therefore never passes through the parser that would have cleaned the value
    up. That is the path a caller hits when their search is already going badly.

    FORM SLIPS ARE READ, NOT SEARCHED LITERALLY. Part C, 16 Sep: Colin searched "QLD under
    $750k" and got nothing useful, and each of these on its own turns that search into 0
    results with nothing on screen saying why -- replayed against the deployed snapshot:

        budget_max 750 / "750k"              a $750 (or $0) ceiling            -> 0
        no budget_max at all                 a $0 ceiling                      -> 0
        min 750000, max 300000               above $750k AND below $300k       -> 0
        "Queensland" / "QLD" as a suburb     a suburb called Queensland        -> 0

    Each is corrected here and listed in brief_adjustments, which the Research tab prints
    above the results: {field, code, from, to, why} or {field, code, note}. Nothing is
    corrected silently, and a brief with no slip comes back with no adjustments.
    """
    out = dict(brief)
    adjustments = []
    for field in _LIST_FIELDS:
        value = out.get(field)
        if value is None or isinstance(value, list):
            continue
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            # One suburb sent bare, or an integration that unwrapped a single-element list.
            text = str(value).strip()
            out[field] = [text] if text else []
        elif isinstance(value, (tuple, set)):
            out[field] = [str(v).strip() for v in value if str(v).strip()]
        else:
            out[field] = []
    # Every element must be a string: a list holding a dict crashes the same readers.
    for field in _LIST_FIELDS:
        if isinstance(out.get(field), list):
            out[field] = [str(v).strip() for v in out[field]
                          if isinstance(v, (str, int, float)) and str(v).strip()]
    _normalised_money(out, adjustments)
    _normalised_location(out, adjustments)
    return out, adjustments


def _state_resolution(adjustments):
    """location_resolution entries (method 'state') for suburbs that were read as a state.

    Only for the names taken out of the suburb list: one kept because it conflicts with
    the selected state goes on to the resolver, which reports it like any other phrase.
    """
    out = []
    for a in adjustments:
        if a.get("code") != "state_typed_as_suburb":
            continue
        code = a["to"] if a.get("field") == "state" else ""
        out.append({"said": a.get("said", ""), "used": [], "method": "state",
                    "note": "a state, not a suburb, so it was not searched as a suburb"
                            + ("; the search is of %s" % code if code else "")})
    return out


def _several_states_notes(brief_dict, index):
    """A note for each target suburb that is a locality in more than one state, when no
    state was chosen: "Springfield" is in six, and with Any state all six can match."""
    if str(brief_dict.get("state") or "").strip() or index is None:
        return []
    notes = []
    for said in brief_dict.get("primary_suburbs") or []:
        try:
            states = (index.states_for_suburb(said)
                      or index.states_for_suburb(said, spoken=True))
        except Exception:                                           # noqa: BLE001
            continue
        if len(states) > 1:
            notes.append({
                "field": "primary_suburbs", "code": "suburb_in_several_states",
                "said": said, "states": list(states),
                "note": "%s is a locality in %s; no state was selected, so listings in "
                        "any of them can match" % (said, _joined(states))})
    return notes


def _joined(names):
    names = list(names)
    return names[0] if len(names) == 1 else "%s and %s" % (", ".join(names[:-1]), names[-1])


def _whole(value):
    return _tidy_number(value) if isinstance(value, float) else value


def _brief_used(brief_dict):
    """What was actually searched, after every adjustment and the location resolver.

    Keyed like the brief itself (primary_suburbs, search_radius_km, ...), so a logged
    brief_used can be posted back as a client_brief and replay the search. A blank state
    reads 'Any state', which _normalised_brief reads back as blank. The numbers are the
    ones the pipeline reads: brief_parser truncates minimums to whole numbers, so
    bathrooms 2.5 shows as 2.
    """
    from brief_parser import coerce_number

    def number(key, default=0):
        value = coerce_number(brief_dict.get(key), None)
        return default if value is None else _whole(value)

    def whole(key, default=0):
        value = coerce_number(brief_dict.get(key), None)
        return default if value is None else int(value)

    product = brief_dict.get("product_type")
    if isinstance(product, (list, tuple, set)):
        product = [str(p).strip() for p in product if str(p).strip()]
    else:
        product = [str(product).strip()] if str(product or "").strip() else []
    budget_max = number("budget_max", NO_CEILING)
    return {
        "state": str(brief_dict.get("state") or "").strip().upper() or ANY_STATE,
        "budget_min": number("budget_min"),
        "budget_max": budget_max,
        # brief_parser's default: the maximum, when no preferred price was given.
        "preferred_spending_cap": number("preferred_spending_cap", budget_max),
        "primary_suburbs": list(brief_dict.get("primary_suburbs") or []),
        "search_radius_km": number("search_radius_km"),
        "bedrooms_min": whole("bedrooms_min"),
        "bathrooms_min": whole("bathrooms_min"),
        "car_spaces_min": whole("car_spaces_min"),
        "storeys_max": whole("storeys_max", None),
        "land_size_min_sqm": number("land_size_min_sqm"),
        "house_size_min_sqm": number("house_size_min_sqm"),
        "product_type": product,
        "requires_incentive": bool(brief_dict.get("requires_incentive")),
    }


# Every in-state row a search did not shortlist, by what stopped it. Out-of-scope rows
# (superseded, other states, no state) are not "filtered" -- they were never in the search.
# storey_unknown_and_binding is NOT here: it tags a row that is still scored, so counting
# it too counted that row twice.
FILTERED_COUNTS = (
    "not_available", "availability_unstated", "price_unverified", "no_price",
    "over_budget", "under_minimum", "no_suburb", "suburb_not_a_locality",
    "incomplete_facts", "other_product_type", "no_incentive",
    "truncated", "same_listing_collapsed", "duplicates_merged",
)
OUT_OF_SCOPE_COUNTS = ("superseded", "other_state", "state_unknown")

# The pipeline's reason for a candidate it scored but held back as unconfirmed
# (kommo_agent.py, "SOP Step 5"). Matched on the text, which has not changed since the
# pipeline was written, rather than on a 'kind' field that only some versions add.
PENDING_REASON = "Pending Confirmation"


def _is_pending(entry):
    return str(entry.get("reason") or "").startswith(PENDING_REASON)


def _capped_rejected_log(entries, cap):
    """(entries kept, pending left out, hard rejections left out).

    Up to `cap` pending entries AHEAD of up to `cap` hard rejections. The log used to be
    cut at `cap` in pipeline order, hard rejections first, so any search with more than
    30 of those never listed a single stale match: Werribee VIC <= $700k, 4 bed, hid its
    one pending lot behind 933 scorer rejections. A stale match is the most useful line
    in the log -- it is a real listing a consultant can confirm with one phone call.
    """
    pending = [e for e in entries if _is_pending(e)]
    hard = [e for e in entries if not _is_pending(e)]
    return (pending[:cap] + hard[:cap], max(0, len(pending) - cap),
            max(0, len(hard) - cap))


def _availability_by_hash(shortlist, packages, rows):
    """{content_hash: {availability_by_the_sheet, availability_status}} for the shortlist.

    The package says whether a row is available only by the sheet; the snapshot row says
    what its source stated. Read from both by content_hash, because the CandidateProperty
    the pipeline hands back carries neither.
    """
    wanted = {str(getattr(p, 'content_hash', '') or '') for p in shortlist} - {''}
    if not wanted:
        return {}
    out = {h: {'availability_by_the_sheet': False, 'availability_status': ''}
           for h in wanted}
    for pkg in packages:
        h = str(pkg.get('content_hash') or '')
        if h in out:
            out[h]['availability_by_the_sheet'] = bool(pkg.get('availability_by_the_sheet'))
    for row in rows:
        h = str(row.get('content_hash') or '').strip()
        if h in out and not row.get('superseded_by'):
            out[h]['availability_status'] = str(
                row.get('availability_status') or '').strip()
    return out


def _log_search(response, counts):
    """One stdout JSON line per search, so a "returned nothing" can be replayed from the
    function logs: post brief_used back, exactly as logged, as the client_brief. No
    client name, and nothing a search does not already show the consultant.
    Never fatal: a log line must not cost anyone their results."""
    try:
        print(json.dumps({
            "event": "spb_research",
            "brief_used": response.get("brief_used"),
            "brief_adjustments": [a.get("code") for a in response.get("brief_adjustments")
                                  or []],
            "shortlist_count": response.get("shortlist_count"),
            "rejected_count": response.get("rejected_count"),
            "in_state_rows": response.get("in_state_rows"),
            "counts": counts,
        }, default=str, separators=(",", ":")), flush=True)
    except Exception as exc:                                          # noqa: BLE001
        print("research log line unavailable: %s" % type(exc).__name__, file=sys.stderr)


# ------------------------------------------------------------------------------ explain
#
# WHY ONLY N MATCHED, AND WHAT ELSE A CONSULTANT COULD SEND. Part C, 16 Sep: Colin searched
# "QLD under $750k" and saw two cards and a badge. Nothing said that 23 in-state listings
# sit in that price range, that three Shape Homes lots in Walloon match but were last read
# 27 days ago, or that $825,000 would find 12. Everything below answers that, for the
# Research tab only.
#
# OPT-IN, AND ONLY BY A LITERAL `"explain": true`. Clare's hook (vapi-kommo-hook
# lib/deps.js) posts {client_brief} alone and reads json.shortlist; the Hot Leads re-run
# posts the same. Neither may ever see a needs-confirming row -- the hook would count it as
# stock -- so without the flag the response is byte-for-byte what it was, and a truthy
# "true" or 1 is not the flag.
#
# ADDITIVE ONLY. shortlist, shortlist_count, rejected_count, rejected_log, kommo_payload,
# comparisons and client_report_html are computed before any of this runs and are never
# written by it: _with_explain adds new keys and refuses to replace an existing one.

EXPLAIN_KEYS = ("search_help", "needs_confirming", "needs_confirming_count",
                "needs_confirming_hidden")
SEARCH_HELP_VERSION = 1
# Relaxations are priced only for a thin search: a shortlist this long already has options.
EXPLAIN_BELOW = int(os.environ.get("SPB_EXPLAIN_BELOW", "5"))
# Every relaxation is a real re-run of the search. Past this many milliseconds of explain
# work, the rest are reported not_computed rather than making the consultant wait.
EXPLAIN_BUDGET_MS = int(os.environ.get("SPB_EXPLAIN_BUDGET_MS", "4000"))
# The amber group lists at most this many; needs_confirming_count stays exact.
MAX_NEEDS_CONFIRMING = int(os.environ.get("SPB_MAX_NEEDS_CONFIRMING", "50"))
ADMIT_UNSTATED = ("availability_unstated",)
NO_BUILDER = "Builder not identified in source"
# location_resolution methods worth a note: the ones where what was searched is not simply
# what was typed.
LOCATION_NOTE_METHODS = ("groq", "fuzzy", "unresolved", "state")
# Gates whose rows are, by definition, priced outside the brief's range.
_OUT_OF_RANGE_GATES = ("no_price", "over_budget", "under_minimum")
_POOL_COUNTS = ("same_listing_collapsed", "duplicates_merged", "truncated")
# The minimums a relaxation may drop, with what a consultant calls them.
_MINIMUMS = (("bedrooms_min", "bedrooms"), ("bathrooms_min", "bathrooms"),
             ("car_spaces_min", "car spaces"), ("house_size_min_sqm", "house size"),
             ("land_size_min_sqm", "land size"))


def _elapsed_ms(started):
    return int(round((time.monotonic() - started) * 1000))


def _out_of_time(started):
    """True once the explain work started at `started` has used EXPLAIN_BUDGET_MS."""
    return _elapsed_ms(started) >= EXPLAIN_BUDGET_MS


def _money(amount):
    return "$" + format(_tidy_number(amount), ",")


def _funnel(response, counts, result):
    """Where every row of the snapshot went, gate by gate, with the in-range tally beside
    each gate: "632 availability unstated" is mostly over-budget stock, and "6 of them in
    your price range" is the number a consultant can act on."""
    in_range = dict(counts.get("counts_in_range") or {})
    gates = []
    for code in _candidates.GATE_ORDER:
        if code in OUT_OF_SCOPE_COUNTS:
            continue
        gates.append({"code": code, "stage": "before_scoring",
                      "count": counts.get(code, 0),
                      "in_price_range": (None if code in _OUT_OF_RANGE_GATES
                                         else in_range.get(code, 0))})
    for code in _POOL_COUNTS:
        if counts.get(code):
            gates.append({"code": code, "stage": "pool", "count": counts[code],
                          "in_price_range": None})
    scorer = {}
    rejected_by_scorer = 0
    pending = 0
    for entry in (result or {}).get("rejected_log") or []:
        if entry.get("kind") == "rejection":
            rejected_by_scorer += 1
            for code in entry.get("codes") or ():
                scorer[code] = scorer.get(code, 0) + 1
    for code, n in scorer.items():
        gates.append({"code": code, "stage": "scoring", "count": n, "in_price_range": None})
    if result:
        pending = len(result.get("pending_confirmation") or ())
        gates.append({"code": "pending_confirmation", "stage": "scoring", "count": pending,
                      "in_price_range": None})
    return {
        "snapshot_rows": counts.get("snapshot_rows", 0),
        "out_of_scope": {code: counts.get(code, 0) for code in OUT_OF_SCOPE_COUNTS},
        "in_state_rows": response.get("in_state_rows"),
        "gates": gates,
        # counts_in_range as build_packages tallied it: 'available' is "passed every gate
        # before scoring", stale and by-the-sheet rows included.
        "in_price_range": in_range,
        "in_price_range_total": sum(v for v in in_range.values() if isinstance(v, int)),
        "just_over": counts.get("just_over", 0),
        "scored": counts.get("scored", 0),
        "rejected_by_scorer": rejected_by_scorer,
        "pending_confirmation": pending,
        "shortlisted": response.get("shortlist_count", 0),
        "capped": bool(counts.get("truncated")),
    }


def _live_rows_by_hash(rows):
    out = {}
    for row in rows:
        h = str(row.get("content_hash") or "").strip()
        if h and not row.get("superseded_by"):
            out[h] = row
    return out


def _listing_key(lot_address, house_design, price):
    """DedupeEngine's key: the same lot, design and price is one listing."""
    try:
        price = float(price or 0)
    except (TypeError, ValueError):
        price = 0.0
    return "%s||%s||%s" % (str(lot_address or "").strip().lower(),
                           str(house_design or "").strip().lower(), price)


# How _candidates labels a snapshot package's channel: "<source_channel> - database snapshot".
_SNAPSHOT_CHANNEL_SUFFIX = " - database snapshot"


def _candidate_key(cand):
    return _listing_key(cand.lot_address, cand.house_design,
                        cand.price_breakdown.advertised_package_price)


def _needs_confirming_entry(cand, pkg, row):
    """One amber card: the candidate as scored, with the ROW'S OWN dates.

    date_checked and age_days come from the package, which took them from the snapshot row
    -- never from the day of the search. For a 'digital email' row date_checked is the last
    night the inbox sweep re-read the message, not the list's date (critique #27), so the
    entry also carries source_channel, first_seen (the emailed list's first read) and
    list_age_days, and the card can say "email last read" instead of "last checked".
    """
    row = row or {}
    stated = str(row.get("availability_status") or "").strip()
    first_seen = str(pkg.get("email_first_seen") or "").strip()
    channel = str(row.get("source_channel") or "").strip()
    if not channel:
        # No live row behind the candidate (a row with no content_hash): the package
        # carries the channel as "<channel> - database snapshot". The card's "email last
        # read" wording turns on this, so it is not left blank while the package knows it.
        carried = str(pkg.get("source_channel") or "").strip()
        if carried.endswith(_SNAPSHOT_CHANNEL_SUFFIX):
            carried = carried[:-len(_SNAPSHOT_CHANNEL_SUFFIX)].strip()
        channel = "" if carried == "stored stock" else carried
    return {
        "content_hash": str(cand.content_hash or ""),
        "lot_address": cand.lot_address,
        "lot_number": getattr(cand, "lot_number", "") or "",
        "suburb": cand.suburb,
        "state": cand.state,
        "builder_name": (cand.builder_name or "").strip() or NO_BUILDER,
        "estate_context": getattr(cand, "estate_context", "") or "",
        "house_design": cand.house_design,
        "advertised_package_price": cand.price_breakdown.advertised_package_price,
        "bedrooms": cand.bedrooms,
        "bathrooms": cand.bathrooms,
        "car_spaces": cand.car_spaces,
        "storeys": cand.storeys,
        "land_size_sqm": cand.land_size_sqm,
        "house_size_sqm": cand.house_size_sqm,
        "score": cand.scoring.total_score if cand.scoring else 0,
        "advisories": cand.scoring.advisories if cand.scoring else "",
        "source_link": getattr(cand, "source_link", None),
        # What a consultant has to confirm: 'stale', 'availability_unstated',
        # 'document_partially_read' (see _candidates._gate).
        "confirm": list(pkg.get("confirm") or ()),
        # The availability exactly as the source row stated it; None when it stated none.
        "availability_as_stated": stated or None,
        "availability_by_the_sheet": bool(pkg.get("availability_by_the_sheet")),
        "date_checked": str(pkg.get("date_checked") or ""),
        "age_days": pkg.get("age_days"),
        "fresh_days": _candidates.FRESH_DAYS,
        "source_channel": channel,
        "first_seen": first_seen or None,
        "list_age_days": pkg.get("list_age_days"),
    }


def _score_packages(agent, brief_dict, packages):
    """(processed, rejected) for packages, deduped the way the search dedupes them. Writes
    nothing: score_candidates keeps no audit and builds no report."""
    from sources.dedupe import DedupeEngine
    packages = DedupeEngine.deduplicate(packages)
    if not packages:
        return [], []
    return agent.score_candidates(brief_dict, packages)


def _refused_matches(agent, brief_dict, rows, prepared):
    """({reason: n}, capped) for unstated rows admission refused that would have MATCHED.

    build_packages counts refusals before scoring, so a Werribee search would count every
    refused row in Victoria -- including the suburbs the scorer rejects afterwards. So each
    unstated row is searched once more as though its source had said "Available" (same
    snapshot, same prepared maps; nothing else about it changes), the stand-ins are scored
    against the same brief, and only the ones that survive are counted, under the reason
    admission_refusal gives for the real row. capped is True when the candidate cap cut
    that pass, so the counts may be short.
    """
    stating = prepared.states_availability
    real_row = {}
    patched = []
    for row in rows:
        avail = str(row.get("availability_status") or "").strip().lower()
        doc = str(row.get("source_doc") or "")
        h = str(row.get("content_hash") or "").strip()
        if (h and not row.get("superseded_by")
                and avail not in _candidates.IS_AVAILABLE
                and avail not in _candidates.NOT_AVAILABLE
                and (not doc or doc in stating)):
            real_row[h] = row
            patched.append(dict(row, availability_status="Available"))
        else:
            patched.append(row)
    if not real_row:
        return {}, False
    packages, counts = _candidates.build_packages(brief_dict, patched, prepared=prepared)
    stand_ins = [p for p in packages if str(p.get("content_hash") or "") in real_row]
    processed, _rejected = _score_packages(agent, brief_dict, stand_ins)
    hidden = {}
    for cand in processed:
        reason, _notes = _candidates.admission_refusal(real_row[cand.content_hash], prepared)
        if reason:
            hidden[reason] = hidden.get(reason, 0) + 1
    return hidden, bool(counts.get("truncated"))


def _needs_confirming(agent, brief_dict, rows, prepared, packages, result, shortlist):
    """(entries, count, hidden, capped): matches a consultant may confirm and send.

    Two sources, never the main pool:
      (a) the pipeline's Pending Confirmation candidates -- stale or demoted rows that passed
          every requirement -- kept when their package's confirm_guard is None;
      (b) a SEPARATE admit pass over exceptions-only lists (build_packages with
          admit=availability_unstated and only_admitted=True: its own collapse and its own
          600 cap, so it cannot push a confirmed row out of the search), scored against the
          same brief, survivors only.
    Merged, with anything already on the shortlist (by content_hash or as the same lot,
    design and price) dropped, sorted by score, listed up to MAX_NEEDS_CONFIRMING.

    hidden is {reason: n} over matches that were NOT offered: (a) candidates a guard hides
    (newer_capture_disagrees, over_confirm_limit, ...) and unstated rows refused admission
    (document_reread_without_row, build_only, ...), both counted after scoring.

    capped is {'admit_pass': bool, 'refusal_pass': bool}: True where the candidate cap cut
    that pass, so what it contributed may be short. (a) is short whenever the search's own
    pool was cut, which the caller knows from counts['truncated'].
    """
    live = _live_rows_by_hash(rows)
    taken_hashes = {str(getattr(p, "content_hash", "") or "") for p in shortlist} - {""}
    taken_keys = {_candidate_key(p) for p in shortlist}
    entries = []
    hidden = {}

    def offer(cand, pkg):
        h = str(cand.content_hash or "")
        key = _candidate_key(cand)
        if (h and h in taken_hashes) or key in taken_keys:
            return
        taken_keys.add(key)
        if h:
            taken_hashes.add(h)
        entries.append(_needs_confirming_entry(cand, pkg, live.get(h)))

    by_hash = {str(p.get("content_hash") or ""): p for p in packages}
    for cand in (result or {}).get("pending_confirmation") or ():
        pkg = by_hash.get(str(cand.content_hash or ""))
        if pkg is None or not pkg.get("confirm"):
            hidden["untraceable"] = hidden.get("untraceable", 0) + 1
            continue
        guard = pkg.get("confirm_guard")
        if guard:
            hidden[guard] = hidden.get(guard, 0) + 1
            continue
        offer(cand, pkg)

    admitted, admit_counts = _candidates.build_packages(
        brief_dict, rows, prepared=prepared, admit=ADMIT_UNSTATED, only_admitted=True)
    admitted_by_hash = {str(p.get("content_hash") or ""): p for p in admitted}
    processed, _rejected = _score_packages(agent, brief_dict, admitted)
    for cand in processed:
        pkg = admitted_by_hash.get(str(cand.content_hash or ""))
        if pkg is None:
            continue
        guard = pkg.get("confirm_guard")
        if guard:                      # admission already applied every guard; belt only
            hidden[guard] = hidden.get(guard, 0) + 1
            continue
        offer(cand, pkg)

    capped = {"admit_pass": bool(admit_counts.get("truncated")), "refusal_pass": False}
    if admit_counts.get("unstated_not_admitted"):
        refused, capped["refusal_pass"] = _refused_matches(agent, brief_dict, rows, prepared)
        for reason, n in refused.items():
            hidden[reason] = hidden.get(reason, 0) + n

    entries.sort(key=lambda e: (-(e["score"] or 0), e["advertised_package_price"] or 0,
                                e["content_hash"]))
    return entries[:max(0, MAX_NEEDS_CONFIRMING)], len(entries), hidden, capped


_PRICE_FIELDS = ("budget_min", "budget_max", "preferred_spending_cap")


def _as_posted(offers, brief_dict, adjustments):
    """offers, with each price patch made safe to post back over the form AS SENT.

    A brief_patch is applied to the form, and the form goes through _normalised_brief
    again. That is the brief as searched for every slip but one: a swapped range. The form
    still says min $750,000 / max $300,000, so a patch of only budget_max 800000 re-posts
    $750,000-$800,000 -- not the $300,000-$800,000 that was counted -- and "no minimum
    price" re-posts $0-$300,000. After a swap, a patch that touches any price carries all
    three as searched, so the count on the button is what the click returns.
    """
    if not any(a.get("code") == "min_max_swapped" for a in adjustments or ()):
        return offers
    for offer in offers:
        patch = offer.get("brief_patch")
        if not patch or not set(patch) & set(_PRICE_FIELDS):
            continue
        whole = {}
        for field in _PRICE_FIELDS:
            if field in patch:
                whole[field] = patch[field]
            elif brief_dict.get(field) is not None:
                whole[field] = brief_dict[field]
        offer["brief_patch"] = whole
    return offers


def _relaxation_offers(brief_dict, counts, needs_confirming_count, adjustments=()):
    """The relaxations worth pricing for this brief, IN THE ORDER THEY ARE PRICED.

    The order is the time budget's priority (critique #24): the reveal costs nothing, the
    budget steps are what a consultant tries first (Colin's own retries were 750k / 760k /
    770k), then location, then the minimums and filters. A slow search runs out of budget
    on the least likely, never on the budget steps.
    """
    from brief_parser import coerce_number
    offers = [{"id": "include_needs_confirming", "kind": "reveal",
               "label": "Show %d listing(s) that match but need confirming"
                        % (needs_confirming_count or 0),
               "brief_patch": None}]

    budget_max = coerce_number(brief_dict.get("budget_max"), 0) or 0
    if 0 < budget_max < NO_CEILING:
        preferred = coerce_number(brief_dict.get("preferred_spending_cap"), None)
        # The form sends Max as the preferred price when Preferred is blank; a preferred
        # price the consultant typed separately is theirs and stays put.
        follows = preferred is None or preferred == budget_max
        seen = set()
        for rid, raised, how in (
                ("budget_plus_50k", budget_max + 50000, "+$50,000"),
                ("budget_plus_10pct", round(budget_max * 1.1 / 1000.0) * 1000, "+10%")):
            raised = _tidy_number(raised)
            if raised <= budget_max or raised >= NO_CEILING or raised in seen:
                continue
            seen.add(raised)
            patch = {"budget_max": raised}
            if follows:
                patch["preferred_spending_cap"] = raised
            offers.append({"id": rid, "kind": "rerun",
                           "label": "Budget up to %s (%s)" % (_money(raised), how),
                           "brief_patch": patch})

    suburbs = [s for s in (brief_dict.get("primary_suburbs") or []) if str(s).strip()]
    state = str(brief_dict.get("state") or "").strip().upper()
    radius = coerce_number(brief_dict.get("search_radius_km"), 0) or 0
    named = ((", ".join(suburbs[:3]) + " and %d more" % (len(suburbs) - 3))
             if len(suburbs) > 3 else _joined(suburbs) if suburbs else "")
    if suburbs and state:
        # The state goes in the patch: it may have come from the resolver ("Tarneit" with
        # Any state), and re-posted without it the search would be of every state.
        offers.append({"id": "whole_state", "kind": "rerun",
                       "label": "All of %s, not only %s" % (state, named),
                       "brief_patch": {"state": state, "primary_suburbs": [],
                                       "search_radius_km": 0}})
    if suburbs:
        for km in (20, 50):
            if radius < km:
                offers.append({"id": "radius_%d" % km, "kind": "rerun",
                               "label": "Within %d km of %s" % (km, named),
                               "brief_patch": {"search_radius_km": km}})

    set_minimums = [(field, name) for field, name in _MINIMUMS
                    if (coerce_number(brief_dict.get(field), 0) or 0) > 0]
    for field, name in set_minimums:
        offers.append({"id": "drop_" + field, "kind": "rerun",
                       "label": "Any %s (no minimum of %s)"
                                % (name, _whole(coerce_number(brief_dict.get(field), 0))),
                       "brief_patch": {field: 0}})
    if len(set_minimums) >= 2:
        offers.append({"id": "drop_all_minimums", "kind": "rerun",
                       "label": "No minimums at all",
                       "brief_patch": {field: 0 for field, _name in set_minimums}})
    if counts.get("other_product_type"):
        offers.append({"id": "drop_product_type", "kind": "rerun",
                       "label": "Any property type", "brief_patch": {"product_type": []}})
    if counts.get("no_incentive"):
        offers.append({"id": "drop_incentive", "kind": "rerun",
                       "label": "With or without an incentive",
                       "brief_patch": {"requires_incentive": False}})
    if counts.get("under_minimum"):
        offers.append({"id": "drop_price_floor", "kind": "rerun",
                       "label": "No minimum price", "brief_patch": {"budget_min": 0}})
    return _as_posted(offers, brief_dict, adjustments)


def _shortlist_count_for(agent, brief_dict, rows, prepared):
    """(shortlist_count, capped) for a brief: the search's own path, end to end --
    build_packages, dedupe, score_candidates, the verified-or-approved final pool and
    SPB_SHORTLIST_MAX -- without the audit write, reports or Kommo payload."""
    from schema import VerificationStatus
    packages, counts = _candidates.build_packages(brief_dict, rows, prepared=prepared)
    processed, _rejected = _score_packages(agent, brief_dict, packages)
    final = [c for c in processed
             if c.verification_status == VerificationStatus.VERIFIED or c.consultant_approved]
    cap = int(os.environ.get("SPB_SHORTLIST_MAX", "0") or 0)
    return (min(len(final), cap) if cap > 0 else len(final)), bool(counts.get("truncated"))


def _relaxations(agent, brief_dict, rows, prepared, counts, base_count,
                 needs_confirming_count, started, adjustments=()):
    """Every relaxation with a TRUE count: each is the whole search re-run with its
    brief_patch applied to the brief as searched. Posting that patch back over the form
    returns exactly shortlist_count (count_status 'exact'). Past EXPLAIN_BUDGET_MS the rest
    are 'not_computed'; one that fails is 'error' and costs nothing else."""
    out = []
    for offer in _relaxation_offers(brief_dict, counts, needs_confirming_count,
                                    adjustments):
        if offer["kind"] == "reveal":
            known = needs_confirming_count is not None
            out.append(dict(offer, count=needs_confirming_count,
                            adds=needs_confirming_count,
                            count_status="exact" if known else "not_computed"))
            continue
        if _out_of_time(started):
            out.append(dict(offer, shortlist_count=None, adds=None,
                            count_status="not_computed", capped=None))
            continue
        began = time.monotonic()
        try:
            n, capped = _shortlist_count_for(
                agent, dict(brief_dict, **offer["brief_patch"]), rows, prepared)
        except Exception as exc:                                      # noqa: BLE001
            print("relaxation %s failed: %s" % (offer["id"], type(exc).__name__),
                  file=sys.stderr)
            out.append(dict(offer, shortlist_count=None, adds=None, count_status="error",
                            capped=None, error=type(exc).__name__))
            continue
        out.append(dict(offer, shortlist_count=n, adds=n - base_count,
                        count_status="exact", capped=capped, ms=_elapsed_ms(began)))
    return out


def _with_explain(response, brief_dict, rows, prepared, counts, packages, result):
    """Add search_help, needs_confirming, needs_confirming_count and needs_confirming_hidden
    to a finished response. Never raises, and never replaces a key already in it."""
    started = time.monotonic()
    errors = []
    help_ = {"version": SEARCH_HELP_VERSION,
             "brief_as_searched": response.get("brief_used"),
             "brief_adjustments": response.get("brief_adjustments") or [],
             "location_notes": [dict(e) for e in response.get("location_resolution") or []
                                 if isinstance(e, dict)
                                 and e.get("method") in LOCATION_NOTE_METHODS]}
    extra = {"search_help": help_, "needs_confirming": [], "needs_confirming_count": None,
             "needs_confirming_hidden": {}}
    try:
        agent = _bootstrap.get_agent()
    except Exception as exc:                                          # noqa: BLE001
        agent = None
        errors.append("agent: %s" % type(exc).__name__)
    try:
        help_["funnel"] = _funnel(response, counts, result)
    except Exception as exc:                                          # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        errors.append("funnel: %s" % type(exc).__name__)
    if agent is not None:
        try:
            listed, total, hidden, capped = _needs_confirming(
                agent, brief_dict, rows, prepared, packages, result,
                (result or {}).get("shortlist") or [])
            extra.update(needs_confirming=listed, needs_confirming_count=total,
                         needs_confirming_hidden=hidden)
            # EXACT OVER WHAT WAS SCORED. The Pending candidates come from the search's own
            # pool, so when the candidate cap cut that pool (VIC <= $900k, 4 bed: 933 rows)
            # stale matches beyond it were never scored and cannot be counted. Said, not
            # implied: the count is complete only when nothing was cut.
            pool_cut = bool(counts.get("truncated"))
            help_["needs_confirming"] = {
                "count": total, "listed": len(listed), "list_cap": MAX_NEEDS_CONFIRMING,
                "hidden": dict(hidden),
                "count_may_be_short": pool_cut or capped["admit_pass"],
                "hidden_may_be_short": (pool_cut or capped["admit_pass"]
                                        or capped["refusal_pass"]),
                "ms": _elapsed_ms(started)}
        except Exception as exc:                                      # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            errors.append("needs_confirming: %s" % type(exc).__name__)
        base = response.get("shortlist_count") or 0
        help_["explain_below"] = EXPLAIN_BELOW
        help_["budget_ms"] = EXPLAIN_BUDGET_MS
        if base < EXPLAIN_BELOW:
            try:
                help_["relaxations"] = _relaxations(
                    agent, brief_dict, rows, prepared, counts, base,
                    extra["needs_confirming_count"], started,
                    response.get("brief_adjustments") or ())
            except Exception as exc:                                  # noqa: BLE001
                traceback.print_exc(file=sys.stderr)
                help_["relaxations"] = []
                errors.append("relaxations: %s" % type(exc).__name__)
        else:
            help_["relaxations"] = []
            help_["relaxations_note"] = ("not priced: the search already shortlisted %d "
                                         "(SPB_EXPLAIN_BELOW is %d)" % (base, EXPLAIN_BELOW))
    else:
        help_["relaxations"] = []
    if errors:
        help_["error"] = "; ".join(errors)
    help_["timing_ms"] = _elapsed_ms(started)
    for key, value in extra.items():
        if key not in response:
            response[key] = value
    return response


def run_research(payload):
    """The whole endpoint as a plain function, so it is testable without a socket."""
    # Anything can arrive over HTTP. A payload that is not an object, or a client_brief
    # sent as a string or a list, used to reach .get() and raise — answering a malformed
    # request with a 500 ("we broke") instead of telling the caller what was wrong.
    if not isinstance(payload, dict):
        raise BadRequest("the request body must be a JSON object")
    brief_dict = payload.get('client_brief') or {}
    if not isinstance(brief_dict, dict):
        raise BadRequest("'client_brief' must be a JSON object, got %s"
                         % type(brief_dict).__name__)
    # The literal JSON true and nothing else -- see EXPLAIN_KEYS.
    explain = payload.get('explain') is True
    brief_dict, adjustments = _normalised_brief(brief_dict)

    agent = _bootstrap.get_agent()

    rows, snapshot_path = _candidates.load_snapshot()

    # A REGION NAME MEANS THE AREA, NOT THE ONE SUBURB THAT SHARES ITS NAME.
    #
    # Coleen searched "Geelong" and got nothing, then said what was wrong herself:
    # "Geelong shows region here not the suburb". Of the 60 Geelong-area listings the
    # catalogue holds, 24 are filed under Armstrong Creek and the rest under Lara,
    # Leopold and Kensington -- only 4 say "Geelong". Suburbs are matched exactly, so a
    # region name reached almost none of its own stock.
    #
    # Widened only for names on the list in _regions, and only to suburbs the catalogue
    # actually holds stock in, so a search does not gain two hundred empty localities.
    # What it covered is reported back in `region_expansion`, because answering a wider
    # question than the one typed must be visible rather than silent.
    _expanded = {}
    # What was done to every primary suburb the brief named -- see _location_resolver.
    # A state typed as a suburb was taken out by _normalised_brief before the resolver
    # could see it, so its entry (method 'state') is kept here and leads the list: the
    # resolver's report replaces _location below and would otherwise drop it.
    _state_entries = _state_resolution(adjustments)
    _location = list(_state_entries)
    try:
        import _regions
        from geo import SuburbGeoIndex
        _index = SuburbGeoIndex()
        # AN ESTATE NAME IS NOT A SPELLING OF A SUBURB. Research tab, 15 Sep 2026:
        # "Newpark", the estate at Melonba (NSW), folded to New Park NSW, 604 km away,
        # and the search ran around it. Both indexes learn the snapshot's estates before
        # anything folds a name: this one respells the brief below, agent.geo draws the
        # search area and measures every lot (kommo_agent). See know_estates in geo.py.
        _estates = [(r.get("estate_name"), r.get("suburb"), r.get("state")) for r in rows
                    if r.get("estate_name") and not r.get("superseded_by")]
        _index.know_estates(_estates)
        agent.geo.know_estates(_estates)
        # THE DATASET'S SPELLING OF A SUBURB THAT WAS SAID DIFFERENTLY, before anything
        # matches on it. Clare, 15 Sep 2026: "Red Bank Plains" for Redbank Plains. geo
        # resolves the spoken form for a radius search, but at radius 0 -- the Research
        # tab's default -- scoring_engine and the candidate cap compare these names
        # verbatim, and offline against the snapshot 4 Redbank Plains lots were refused
        # as "not one of the requested suburbs (Red Bank Plains)". A name that is already
        # a locality in the brief's state is left exactly as typed.
        #
        # AND WHAT THE CALLER MEANT, WHEN NO SPELLING OF IT IS A PLACE. Ahsan, 15 Sep 2026:
        # "use the Groq API key to eliminate any address issue you might face in the
        # future". A fold cannot hear "Tarn eat" as Tarneit, or "western suburbs of
        # Melbourne" as the suburbs there. _location_resolver asks Groq once for every
        # phrase still unresolved, keeps only names the suburb index confirms in the brief's
        # state, and says what it did in `location_resolution`. With no key, or when Groq
        # fails, the brief comes out exactly as the fold alone left it.
        _state = str(brief_dict.get("state") or "").strip().upper()
        _asked = brief_dict.get("primary_suburbs") or []
        try:
            import _location_resolver
            _resolved = _location_resolver.resolve(_asked, _state, _index)
            _spelt = _resolved["suburbs"]
            _location = _state_entries + list(_resolved["location_resolution"])
            if _resolved["state"] and not _state:
                # Only from a locality that exists in one state alone -- and the note on
                # that phrase says so.
                brief_dict = dict(brief_dict, state=_resolved["state"])
        except Exception as exc:                                      # noqa: BLE001
            # Never fatal, and never different from the research before it existed.
            print("location resolver unavailable: %s" % type(exc).__name__, file=sys.stderr)
            _location = list(_state_entries)
            _spelt = [s if _index.locate(s, _state)
                      else (_index.canonical_suburb(s, _state, spoken=True) or s)
                      for s in _asked]
        if _spelt != _asked:
            brief_dict = dict(brief_dict, primary_suburbs=_spelt)
        # "Springfield" with Any state is six places at once. Said once, before the
        # region list can widen the names.
        adjustments.extend(_several_states_notes(brief_dict, _index))
        _known = {str(r.get("suburb") or "").strip().lower() for r in rows
                  if not r.get("superseded_by")}
        _wider, _expanded = _regions.expand(
            brief_dict.get("primary_suburbs") or [],
            str(brief_dict.get("state") or "").strip().upper(), _index, _known)
        if _expanded:
            brief_dict = dict(brief_dict, primary_suburbs=_wider)
    except Exception:                                  # pragma: no cover - import guard
        _expanded = {}

    # The brief-independent maps, built once per warm instance rather than once per call:
    # the explain pass below runs build_packages again for needs-confirming and for every
    # relaxation it prices. Pure, so the search itself is unchanged by it.
    prepared = _candidates.prepared_for_snapshot(rows, snapshot_path)
    packages, counts = _candidates.build_packages(brief_dict, rows, prepared=prepared)

    # The local run dedupes captured packages before scoring (kommo_agent.py:79). Stored
    # stock holds price-only variant siblings of the same lot, so without this the same
    # address can occupy two shortlist cards. Same engine, same key.
    from sources.dedupe import DedupeEngine
    before_dedupe = len(packages)
    packages = DedupeEngine.deduplicate(packages)
    counts['duplicates_merged'] = before_dedupe - len(packages)
    counts['scored'] = len(packages)

    coverage = _candidates.coverage_sentence(
        counts, snapshot_path, str(brief_dict.get('state') or '').upper())

    # Everything in the requested state that never reached the scoring matrix. Rows
    # for other states are out of scope, not "filtered", so they are excluded here
    # (they are still named in the coverage note).
    #
    # THE WHOLE OF IT, AND NOTHING TWICE. This sum used to leave out availability_unstated
    # (632 rows in Colin's QLD <= $750k search), price_unverified, under_minimum,
    # other_product_type and no_incentive, and to add storey_unknown_and_binding -- a tag on
    # rows that are still scored, so those were counted twice. The badge read "2 Qualified
    # (1420 Filtered)" with 632 more rows hidden and not counted. Now every in-state row is
    # either shortlisted or in the Filtered count, which in_state_rows lets anyone check:
    #
    #     shortlist_count + rejected_count == in_state_rows
    pre_filtered = sum(counts.get(code, 0) for code in FILTERED_COUNTS)
    in_state_rows = counts['snapshot_rows'] - sum(counts.get(code, 0)
                                                  for code in OUT_OF_SCOPE_COUNTS)
    brief_used = _brief_used(brief_dict)

    # kind='coverage' so the UI stops drawing this as a rejected property under a
    # "Hard Rejection" pill. It is the pipeline accounting for what it did and did
    # not score — the opposite of a rejection, and the single most useful line in the
    # response for understanding a thin shortlist.
    # THE COUNTS, NOT ONLY THE SENTENCE. Two listings have now been reported as missing
    # from the app when they were in it — a Liverpool apartment with no bedroom count and
    # a Donnybrook lot with no suburb. Both were named in this paragraph, and the paragraph
    # is a wall of numbers nobody can act on. Structured, the UI can say the one thing that
    # helps: which minimum to drop, and how much stock comes back if you do.
    coverage_entry = {'property_id': 'SNAPSHOT-COVERAGE',
                      'kind': 'coverage',
                      'address': 'Snapshot coverage (deployed build, no live scrape)',
                      'reason': coverage,
                      'missing_fields': counts.get('missing_fields') or {},
                      'no_suburb': counts.get('no_suburb', 0),
                      'suburb_not_a_locality': counts.get('suburb_not_a_locality', 0),
                      'incomplete_facts': counts.get('incomplete_facts', 0)}

    if not packages:
        # Deliberately NOT calling the pipeline: candidate_packages=[] is falsy and
        # run_property_research would fall through to a live crawl of every builder.
        from datetime import datetime

        from brief_parser import coerce_number
        suburbs = brief_dict.get('primary_suburbs') or []
        suburb = (suburbs[0] if suburbs else 'General')
        # coerce_number, not float(): this branch returns before the parser runs, so a
        # budget of 1e400 or a list arrives here raw. Both crashed the request with a
        # 500 on the exact path a caller hits when their search found nothing.
        budget = coerce_number(brief_dict.get('budget_max'), 0) or 0
        # The same record id kommo_agent writes when there are packages: a blank name is
        # 'Unnamed Client' (a get() default never fires on a key sent as ''), and a blank
        # state is 'Any state', never QLD.
        record_id = "%s - %s/%s - $%s - %s" % (
            brief_dict.get('client_name') or 'Unnamed Client',
            str(brief_dict.get('state') or '').strip().upper() or ANY_STATE, suburb,
            format(budget, ',.0f'), datetime.now().strftime('%Y-%m-%d'))
        response = {
            'status': 'success',
            'research_record_id': record_id,
            'shortlist_count': 0,
            # Nothing reached scoring, so every in-state row is in the Filtered count.
            'rejected_count': pre_filtered,
            'in_state_rows': in_state_rows,
            'brief_used': brief_used,
            'brief_adjustments': adjustments,
            'rejected_log': [coverage_entry],
            'shortlist': [],
            'reports': [],
            'search_area': agent.geo.expand_search_suburbs(
                [s for s in suburbs if s], str(brief_dict.get('state') or '').upper(),
                coerce_number(brief_dict.get('search_radius_km'), 0) or 0),
            'region_expansion': _expanded,
            # Visible on the path a caller hits when the search found nothing, which is
            # exactly when "we searched Tarneit for 'Tarn eat'" matters most.
            'location_resolution': _location,
            'builder_coverage': {},
            'client_report_html': '',
            # Present on both paths, so a caller reads one response shape.
            'comparisons': {},
            'client_report_path': '',
            'qa_passed': False,
            'qa_failures': ['QA Item 2 Failed: No candidate packages evaluated.'],
            'kommo_payload': _empty_kommo_payload(brief_dict, record_id, coverage),
        }
        _log_search(response, counts)
        if explain:
            _explained(response, brief_dict, rows, prepared, counts, packages, None)
        return response

    result = agent.run_property_research(brief_dict, packages)

    # The competitive check runs over the SHORTLIST only, not the whole pool: it is a
    # pre-send safety check on what a consultant is about to show, and running it over
    # every scored candidate would burn provider quota on listings nobody will see.
    try:
        from datetime import datetime
        from benchmark_competitive import evaluate as _competitive
        from benchmark_market import evaluate as _market
        from comp_provider import get_provider as _get_provider
        _provider = _get_provider()
        for _p in result['shortlist']:
            _row_for_benchmark = {
                'suburb': _p.suburb, 'state': _p.state,
                # HOW WE KNOW THE SUBURB, or the gate below cannot refuse anything.
                #
                # benchmark_competitive and benchmark_market both call
                # suburb_quality.resolve() on this dict and gate on is_located(). Handed
                # only a locality name that has already passed a lookup, resolve() has
                # nothing to grade and returns STATED every time — so the check that
                # exists to keep a GUESSED location out of a client's comparables was
                # passing unconditionally. A listing whose suburb was inferred from a
                # house design ("Magnolia", "Dalton 18") or an apartment type label
                # ("Theodore", a Banana Shire town 550 km from the Brisbane lot) was
                # getting suburb medians and REA comparables built for it and printed
                # under "How this compares to the suburb".
                'suburb_source': getattr(_p, 'suburb_source', ''),
                'bedrooms': _p.bedrooms, 'land_sqm': _p.land_size_sqm,
                'price': _p.price_breakdown.advertised_package_price,
                # CandidateProperty carries no product_type, so price_kind would call
                # every shortlisted lot "unknown" and refuse to benchmark any of them.
                # The breakdown is on the property, and a land price plus a build price
                # that add up to the total is the row PROVING what its price covers —
                # the same evidence price_kind.derive accepts from a stored row.
                'product_type': getattr(_p, 'product_type', ''),
                'land_price': _p.price_breakdown.land_price,
                'build_price': _p.price_breakdown.build_price,
            }
            _res = _competitive(_row_for_benchmark, provider=_provider)
            # Benchmark B: market context for the report. Computed from the same comp
            # source but a DIFFERENT comp set (new-build preferred), because a new
            # turnkey package carries a premium a 1990s house does not — fine in the
            # competitive check, misleading in a document telling a buyer the buy is
            # sound. One number cannot serve both.
            _p.market_context = _market(_row_for_benchmark, provider=_provider,
                                        as_at=datetime.now().strftime('%d/%m/%Y'))
            _p.competitive = {
                'verdict': _res.verdict, 'confidence': _res.confidence,
                'tier': _res.tier, 'n_comps': _res.n_comps,
                'n_excluded_no_price': _res.n_excluded_no_price,
                'median': _res.median, 'cheapest': _res.cheapest,
                'cheapest_url': _res.cheapest_url, 'delta_abs': _res.delta_abs,
                'source': _res.source or getattr(_provider, 'name', ''),
                'as_at': datetime.now().strftime('%d/%m/%Y'),
                'note': _res.note, 'client_safe': _res.client_safe,
                'comps': _res.comps,
            }
    except Exception as exc:                                          # noqa: BLE001
        # Never fatal. A benchmark is a check on top of a completed search; a failure in
        # it must not cost the consultant their results.
        print("competitive benchmark unavailable: %s" % exc, file=sys.stderr)

    rejected_log, pending_trimmed, hard_trimmed = _capped_rejected_log(
        result['rejected_log'], MAX_REJECTED_LOG)
    trimmed = pending_trimmed + hard_trimmed
    if trimmed:
        rejected_log.append({
            'property_id': 'REJECTED-TRUNCATED',
            'address': '%d further rejection(s) not listed' % trimmed,
            'reason': 'The rejection log lists up to %d listings pending confirmation and '
                      'up to %d hard rejections for the browser (SPB_MAX_REJECTED_LOG); '
                      'not listed: %d pending, %d rejected. The count above is complete.'
                      % (MAX_REJECTED_LOG, MAX_REJECTED_LOG, pending_trimmed, hard_trimmed),
        })
    rejected_log.insert(0, coverage_entry)

    # SPB_SHORTLIST_MAX (unset by default) cuts qualifying lots from the end of the
    # shortlist without logging them anywhere. They are in-state rows that were not
    # shortlisted, so they are Filtered too, or the count would silently fall short.
    shortlist_capped = max(0, len(packages) - result['rejected_count']
                           - result['shortlist_count'])
    availability = _availability_by_hash(result['shortlist'], packages, rows)

    response = {
        'status': 'success',
        'research_record_id': result['research_record_id'],
        'shortlist_count': result['shortlist_count'],
        'rejected_count': result['rejected_count'] + pre_filtered + shortlist_capped,
        # The in-state rows this search looked at: shortlist_count + rejected_count.
        'in_state_rows': in_state_rows,
        # What was searched, after every brief adjustment and the location resolver.
        'brief_used': brief_used,
        # Every change made to the brief as sent, and why. See _normalised_brief.
        'brief_adjustments': adjustments,
        'rejected_log': rejected_log,
        'shortlist': [_shortlist_entry(p, availability) for p in result['shortlist']],
        'reports': result['reports'],
        'search_area': result.get('search_area', []),
        'region_expansion': result.get('region_expansion') or _expanded,
        # Every primary suburb as said, what was searched for it, and how that was decided
        # (exact / spoken / groq / unresolved). See _location_resolver.
        'location_resolution': _location,
        'builder_coverage': result.get('builder_coverage', {}),
        'client_report_html': result.get('client_report_html', ''),
        # Every pairing of the shortlist, pre-rendered. Colin's own workflow is "two
        # options, side by side, which do I send" — see comparison_report.py. Built here
        # because this is where the full CandidateProperty objects live; handing the pair
        # back over HTTP would mean rebuilding them from JSON, enums and all, to produce
        # a document we can generate now for a few KB each.
        'comparisons': _pairwise_comparisons(brief_dict, result['shortlist']),
        # The local server hands back a real file path. Here the report was written to
        # the function's /tmp and is gone when the instance recycles, so the path is
        # withheld rather than returned as a link that cannot be opened.
        'client_report_path': '',
        'qa_passed': result.get('qa_passed'),
        'qa_failures': result.get('qa_failures', []),
        'kommo_payload': result['kommo_payload'],
    }
    _log_search(response, counts)
    if explain:
        _explained(response, brief_dict, rows, prepared, counts, packages, result)
    return response


def _explained(response, *args):
    """_with_explain, and if even that raises, the search's own response with the error
    named -- the results a consultant asked for are never lost to an explanation."""
    try:
        return _with_explain(response, *args)
    except Exception as exc:                                          # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        if "search_help" not in response:
            response["search_help"] = {"version": SEARCH_HELP_VERSION,
                                       "error": "explain: %s" % type(exc).__name__}
        return response


class handler(BaseHTTPRequestHandler):
    """Vercel Python runtime entrypoint (a BaseHTTPRequestHandler named `handler`)."""

    def _send(self, code, body):
        raw = json.dumps(body, default=str).encode('utf-8')
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        # A stale scoring run must never be served from a CDN or browser cache.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(length) if length else b''
            try:
                payload = json.loads(raw.decode('utf-8')) if raw else {}
            except (UnicodeDecodeError, ValueError) as exc:
                raise BadRequest("body is not valid JSON: %s" % exc)
            self._send(200, run_research(payload))
        except BadRequest as exc:
            # The caller's request was wrong, not ours. 400 says so; a 500 would send
            # them looking for a fault on this side.
            self._send(400, {'status': 'error', 'message': str(exc)})
        except Exception as exc:                                  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            self._send(500, {'status': 'error', 'message': str(exc)})

    def do_GET(self):
        """Health/introspection. index.html only ever POSTs here."""
        try:
            rows, path = _candidates.load_snapshot()
            self._send(200, {
                'status': 'success',
                'endpoint': 'POST /api/research with {"client_brief": {...}}',
                'snapshot': os.path.basename(path),
                'snapshot_rows': len(rows),
                'scrapes': False,
                'bundle_violations': _bootstrap.bundle_violations(),
            })
        except Exception as exc:                                  # noqa: BLE001
            self._send(500, {'status': 'error', 'message': str(exc)})

    def log_message(self, fmt, *args):       # keep the function log readable
        sys.stderr.write("[api/research] " + (fmt % args) + "\n")
