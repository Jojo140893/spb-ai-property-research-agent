"""
Professional HTTP REST API & Web Application Server for SPB AI Property Research Agent.
Zero third-party dependency (uses standard Python libraries).
Integrates directly with schema.py, builder_registry.py, brief_parser.py,
turnkey_calculator.py, scoring_engine.py, report_generator.py, and kommo_agent.py.
"""

import http.server
import socketserver
import json
import os
import sys
import traceback
import urllib.parse
from datetime import datetime

from kommo_agent import KommoPropertyResearchAgent


from config import PROJECT_ROOT, SERVER_PORT

PORT = SERVER_PORT
DIRECTORY = str(PROJECT_ROOT)

agent = KommoPropertyResearchAgent()


class PropertyAgentRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        
        if parsed_url.path == "/api/builders":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            builders = agent.builder_registry.get_all_builders()
            self.wfile.write(json.dumps({'status': 'success', 'count': len(builders), 'builders': builders}).encode('utf-8'))
            return

        # Serve static files (index.html, etc.)
        return super().do_GET()

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)

        if parsed_url.path == "/api/research":
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode('utf-8')) if body else {}
                brief_dict = data.get('client_brief', {})
                candidate_packages = data.get('candidate_packages')

                # Run Python AI Agent Workflow
                result = agent.run_property_research(brief_dict, candidate_packages)

                # Format response
                response_data = {
                    'status': 'success',
                    'research_record_id': result['research_record_id'],
                    'shortlist_count': result['shortlist_count'],
                    'rejected_count': result['rejected_count'],
                    'rejected_log': result['rejected_log'],
                    'shortlist': [
                        {
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
                            'recommendation': p.recommendation.value,
                            'recommendation_reason': p.recommendation_reason,
                            'risks': [{'name': r.risk_name, 'rating': r.rating.value, 'description': r.description, 'mitigation': r.proposed_mitigation} for r in p.risks]
                        } for p in result['shortlist']
                    ],
                    'reports': result['reports'],
                    'kommo_payload': result['kommo_payload']
                }

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response_data).encode('utf-8'))
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode('utf-8'))
            return


def run_server():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), PropertyAgentRequestHandler) as httpd:
        print(f"[+] SPB AI Property Research Agent Server running at http://localhost:{PORT}")
        httpd.serve_forever()


if __name__ == "__main__":
    run_server()
