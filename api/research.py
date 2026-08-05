"""
POST /api/research on Vercel — the Research & Scoring tab, without the local app.

Same request and same response as server.py's do_POST, so index.html needs no change:
it posts {"client_brief": {...}} and reads status / research_record_id /
shortlist_count / rejected_count / rejected_log / shortlist[] / reports /
search_area / builder_coverage / client_report_html / qa_passed / qa_failures /
kommo_payload.

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
import sys
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


def _shortlist_entry(p):
    """Identical field-for-field to server.py's shortlist serialisation."""
    return {
        'property_id': p.property_id,
        'lot_address': p.lot_address,
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


def _empty_kommo_payload(brief_dict, record_id, coverage_reason):
    """What the pipeline would have produced with nothing to shortlist."""
    from datetime import datetime
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
                               % (record_id, brief_dict.get('client_name', 'Client'),
                                  coverage_reason),
        'consultant_review_task': {
            'task_type': 'Consultant Review & Approval',
            'title': "Review Property Research for %s" % brief_dict.get('client_name', 'Client'),
            'due_date_hours': 24,
            'status': 'Pending Review',
        },
    }


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

    agent = _bootstrap.get_agent()

    rows, snapshot_path = _candidates.load_snapshot()
    packages, counts = _candidates.build_packages(brief_dict, rows)

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
    pre_filtered = (counts['not_available'] + counts['no_price'] + counts['over_budget']
                    + counts['no_suburb'] + counts.get('suburb_not_a_locality', 0)
                    + counts['incomplete_facts']
                    + counts['storey_unknown_and_binding'] + counts['truncated'])

    # kind='coverage' so the UI stops drawing this as a rejected property under a
    # "Hard Rejection" pill. It is the pipeline accounting for what it did and did
    # not score — the opposite of a rejection, and the single most useful line in the
    # response for understanding a thin shortlist.
    coverage_entry = {'property_id': 'SNAPSHOT-COVERAGE',
                      'kind': 'coverage',
                      'address': 'Snapshot coverage (deployed build, no live scrape)',
                      'reason': coverage}

    if not packages:
        # Deliberately NOT calling the pipeline: candidate_packages=[] is falsy and
        # run_property_research would fall through to a live crawl of every builder.
        from datetime import datetime
        suburbs = brief_dict.get('primary_suburbs') or []
        suburb = (suburbs[0] if suburbs else 'General')
        budget = float(brief_dict.get('budget_max') or 0)
        record_id = "%s - %s/%s - $%s - %s" % (
            brief_dict.get('client_name', 'Unnamed Client'),
            str(brief_dict.get('state') or 'QLD').upper(), suburb,
            format(budget, ',.0f'), datetime.now().strftime('%Y-%m-%d'))
        return {
            'status': 'success',
            'research_record_id': record_id,
            'shortlist_count': 0,
            'rejected_count': pre_filtered,
            'rejected_log': [coverage_entry],
            'shortlist': [],
            'reports': [],
            'search_area': agent.geo.expand_search_suburbs(
                [s for s in suburbs if s], str(brief_dict.get('state') or '').upper(),
                float(brief_dict.get('search_radius_km') or 0)),
            'builder_coverage': {},
            'client_report_html': '',
            'client_report_path': '',
            'qa_passed': False,
            'qa_failures': ['QA Item 2 Failed: No candidate packages evaluated.'],
            'kommo_payload': _empty_kommo_payload(brief_dict, record_id, coverage),
        }

    result = agent.run_property_research(brief_dict, packages)

    rejected_log = list(result['rejected_log'])
    trimmed = 0
    if len(rejected_log) > MAX_REJECTED_LOG:
        trimmed = len(rejected_log) - MAX_REJECTED_LOG
        rejected_log = rejected_log[:MAX_REJECTED_LOG]
    if trimmed:
        rejected_log.append({
            'property_id': 'REJECTED-TRUNCATED',
            'address': '%d further rejection(s) not listed' % trimmed,
            'reason': 'The rejection log is capped at %d entries for the browser '
                      '(SPB_MAX_REJECTED_LOG); the count above is complete.'
                      % MAX_REJECTED_LOG,
        })
    rejected_log.insert(0, coverage_entry)

    return {
        'status': 'success',
        'research_record_id': result['research_record_id'],
        'shortlist_count': result['shortlist_count'],
        'rejected_count': result['rejected_count'] + pre_filtered,
        'rejected_log': rejected_log,
        'shortlist': [_shortlist_entry(p) for p in result['shortlist']],
        'reports': result['reports'],
        'search_area': result.get('search_area', []),
        'builder_coverage': result.get('builder_coverage', {}),
        'client_report_html': result.get('client_report_html', ''),
        # The local server hands back a real file path. Here the report was written to
        # the function's /tmp and is gone when the instance recycles, so the path is
        # withheld rather than returned as a link that cannot be opened.
        'client_report_path': '',
        'qa_passed': result.get('qa_passed'),
        'qa_failures': result.get('qa_failures', []),
        'kommo_payload': result['kommo_payload'],
    }


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
