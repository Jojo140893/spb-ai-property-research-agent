"""
Recover the facts a stocklist row carried but the extractor never captured.

Every listing keeps the flattened original row it came from in `source_text`, e.g.

    Lot 82 Aberdeen 282 142.8 12.0 Sep-26 Empley 15 3x2x2 $205,000 $335,220 $540,220 Available

which states the land area, the house area, the frontage and the bed/bath/car counts —
none of which reached their columns. House size in particular was recorded on 17% of
stock while being a HARD rejection, and that single gap emptied every VIC search.

The layouts differ per source and per builder, so the rules below are per cohort
("<source channel>|<builder>"). Each is a Python regex with one named group `v`, applied
to source_text alone.

HOW THESE WERE ADMITTED, because it is the only reason to trust them:

  1. Each was derived from 22 real sample rows of its own cohort.
  2. Each was then attacked by a separate reviewer whose job was to break it against
     those same rows. 42 rules were rejected outright — one was described as producing
     "a chimera of two different packages at two different price points" — and 13 were
     replaced by the reviewer's hardened version.
  3. Each survivor was then scored against the WHOLE database, not the samples:
       * where the field is already known, the rule must reproduce it (>= 98%);
       * every value produced must fall inside the physically plausible range for the
         field, and a suburb must resolve in the 17,537-row AU locality index;
       * a rule contradicting a value we already trust is rejected however much new
         data it would fill in.

`precision` and `checked_against` below record that measurement. Where
`checked_against` is 0 the cohort had no stored values to compare with, so the rule
rests on the reviewer plus the range and locality checks — which is why the backfill
only ever writes into an EMPTY column and never overwrites a stored value.

Regenerate with the workflow in reparse-stocklist-rows; do not hand-edit.
"""

import re
from typing import Any, Dict, Optional

_TRANSFORMS = {
    "float": lambda s: float(str(s).replace(",", "").strip()),
    "int": lambda s: int(float(str(s).replace(",", "").strip())),
    "str": lambda s: str(s).strip(),
    "upper": lambda s: str(s).strip().upper(),
    "title": lambda s: str(s).strip().title(),
}

# Physically plausible in Australian stock. A value outside these is a parse error
# wearing a number, and is dropped rather than stored.
RANGES = {
    "house_sqm": (40, 900), "land_sqm": (60, 40000), "bedrooms": (1, 10),
    "bathrooms": (1, 8), "car_spaces": (0, 8), "frontage_m": (3, 80),
    "postcode": (800, 9999), "lot_number": (1, 999999),
}

RULES = [
    {
        "cohort": "Direct Builder Portal (live)|Hermitage Homes",
        "field": "lot_number",
        "pattern": "^LOT\\s+(?P<v>\\d+)\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 153,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "Direct Builder Portal (live)|Hermitage Homes",
        "field": "postcode",
        "pattern": ",[A-Za-z'\\- ]*\\s(?P<v>\\d{4})\\s*$",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 59,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "Direct Builder Portal (live)|Hermitage Homes",
        "field": "street_address",
        "pattern": "^LOT\\s+\\d+\\s+(?P<v>[A-Za-z][A-Za-z'\\- ]*?\\s(?:STREET|ROAD|DRIVE|AVENUE|CRESCENT|BOULEVARD|PARADE|TERRACE|CIRCUIT|COURT|PLACE|GROVE|CLOSE|WAY|LOOP|LANE|RISE|ST|RD|DR|AVE|CRES|PDE|TCE|CCT|CT|PL))\\s*,",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 129,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|?",
        "field": "frontage_m",
        "pattern": "\\b\\d{2,4}\\s+\\d{2,3}\\.\\d\\s+(?P<v>\\d{1,2}\\.\\d)\\s+(?:Titled|Q[1-4]-\\d{4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\\d{2})",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 72,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|?",
        "field": "house_sqm",
        "pattern": "\\b\\d{2,4}\\s+(?P<v>\\d{2,3}\\.\\d)\\s+\\d{1,2}\\.\\d\\s+(?:Titled|Q[1-4]-\\d{4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\\d{2})",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 72,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|?",
        "field": "land_sqm",
        "pattern": "\\b(?P<v>\\d{2,4})\\s+\\d{2,3}\\.\\d\\s+\\d{1,2}\\.\\d\\s+(?:Titled|Q[1-4]-\\d{4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\\d{2})",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 72,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|APLACE by Glenville",
        "field": "bathrooms",
        "pattern": "\\d{2,3}\\.\\d{1,2}\\s+\\d\\s+(?P<v>\\d(?:\\.\\d)?)\\s+\\d\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 68,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|APLACE by Glenville",
        "field": "bedrooms",
        "pattern": "\\d{2,3}\\.\\d{1,2}\\s+(?P<v>\\d)\\s+\\d(?:\\.\\d)?\\s+\\d\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 68,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|APLACE by Glenville",
        "field": "car_spaces",
        "pattern": "\\d{2,3}\\.\\d{1,2}\\s+\\d\\s+\\d(?:\\.\\d)?\\s+(?P<v>\\d)\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 68,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|APLACE by Glenville",
        "field": "frontage_m",
        "pattern": "(?:Titled|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\\d{2})\\s+\\d{3,4}(?:\\.\\d+)?\\s+(?P<v>\\d{1,2}(?:\\.\\d+)?)(?:\\s+x\\s+(?P<depth>\\d{1,2}(?:\\.\\d+)?))?\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 67,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|APLACE by Glenville",
        "field": "house_sqm",
        "pattern": "(?P<v>\\d{2,3}\\.\\d{1,2})\\s+\\d\\s+\\d(?:\\.\\d)?\\s+\\d\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 68,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|APLACE by Glenville",
        "field": "land_sqm",
        "pattern": "(?:Titled|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\\d{2})\\s+(?P<v>\\d{3,4}(?:\\.\\d+)?)\\s+\\d{1,2}(?:\\.\\d+)?\\s+(?:x\\s+\\d|\\$)",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 68,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|APLACE by Glenville",
        "field": "street_address",
        "pattern": "\\b(?P<v>\\d+\\s+[A-Za-z]+(?:\\s+[A-Za-z]+)?\\s+(?:Street|Road|Avenue|Drive|Court|Crescent|Circuit|Close|Way|Boulevard|Esplanade|Parade|Place|Lane|Terrace|Loop|Promenade|Mews|Circle))\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 128,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|AVIA Homes",
        "field": "bathrooms",
        "pattern": "\\d\\s*m2\\s+\\d\\s+(?P<v>\\d)\\s+\\d\\s+\\d\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 96,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|AVIA Homes",
        "field": "bedrooms",
        "pattern": "\\d\\s*m2\\s+(?P<v>\\d)\\s+\\d\\s+\\d\\s+\\d\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 96,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|AVIA Homes",
        "field": "car_spaces",
        "pattern": "\\d\\s*m2\\s+\\d\\s+\\d\\s+(?P<v>\\d)\\s+\\d\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 96,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|AVIA Homes",
        "field": "house_sqm",
        "pattern": "(?<![\\d.,])(?P<v>\\d{2,4})\\s*m2\\s+\\d\\s+\\d\\s+\\d\\s+\\d\\s+\\$",
        "transform": "float",
        "verdict": "ACCEPT",
        "fills": 1,
        "precision": 1.0,
        "checked_against": 95
    },
    {
        "cohort": "E-Agent|AVIA Homes",
        "field": "lot_number",
        "pattern": "^(?P<v>\\d{1,5})\\s+(?:Stage\\s+)?\\d{1,3}\\s+\\d{3,4}\\s*m2\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 94,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Aldrich Homes",
        "field": "bedrooms",
        "pattern": "\\b(?:Single|Double)\\s+(?P<v>\\d)\\s+\\d\\s+(?:Single|Double)\\b",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 103,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Aldrich Homes",
        "field": "frontage_m",
        "pattern": "\\b(?:North|South|East|West)(?:\\s+(?:East|West))?\\s+\\d{3,4}\\.\\d{2}\\s+(?P<v>\\d{1,2}\\.\\d{2})\\s+\\d{1,2}\\.\\d{2}\\s+\\d{2,3}\\.\\d{2}\\b",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 103,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Aldrich Homes",
        "field": "house_sqm",
        "pattern": "(?P<v>\\d{2,3}\\.\\d{2})\\s+(?:Single|Double)\\s+\\d\\s+\\d\\s+(?:Single|Double)\\b",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 103,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Aldrich Homes",
        "field": "land_sqm",
        "pattern": "\\b(?:North|South|East|West)(?:\\s+(?:East|West))?\\s+(?P<v>\\d{3,4}\\.\\d{2})\\s+\\d{1,2}\\.\\d{2}\\s+\\d{1,2}\\.\\d{2}\\s+\\d{2,3}\\.\\d{2}\\b",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 103,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Aldrich Homes",
        "field": "lot_number",
        "pattern": "^(?P<v>\\d+)\\s+(?=[A-Za-z])",
        "transform": "str",
        "verdict": "ACCEPT",
        "fills": 41,
        "precision": 1.0,
        "checked_against": 62
    },
    {
        "cohort": "E-Agent|Alete Homes",
        "field": "bathrooms",
        "pattern": "(?P<v>\\d+)(?:\\s*\\+\\s*\\d+)?\\s*baths?\\b",
        "transform": "int",
        "verdict": "ACCEPT",
        "fills": 2,
        "precision": 1.0,
        "checked_against": 110
    },
    {
        "cohort": "E-Agent|Aria",
        "field": "house_sqm",
        "pattern": "(?P<v>\\d{2,4}(?:\\.\\d+)?)\\s+\\d{1,3}(?:\\.\\d+)?\\s+\\d{2,4}(?:\\.\\d+)?\\s+\\$[\\d,]{4,}",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 123,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Ausbuild",
        "field": "street_address",
        "pattern": "^Lot\\s+\\d+\\s*,\\s*(?P<v>[^,]*\\b(?:Street|Road|Drive|Avenue|Court|Place|Way|Crescent|Parade|Boulevard|Circuit|Close|Terrace|Lane|Esplanade|Highway))\\s*,",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 62,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Creation Homes",
        "field": "bathrooms",
        "pattern": "\\b[1-9]\\s+(?P<v>[1-9](?:\\.\\d)?)\\s+[1-9]\\s+\\d{3,4}\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 66,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Creation Homes",
        "field": "bedrooms",
        "pattern": "\\b(?P<v>[1-9])\\s+[1-9](?:\\.\\d)?\\s+[1-9]\\s+\\d{3,4}\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 66,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Creation Homes",
        "field": "car_spaces",
        "pattern": "\\b[1-9]\\s+[1-9](?:\\.\\d)?\\s+(?P<v>[1-9])\\s+\\d{3,4}\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 66,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Creation Homes",
        "field": "house_sqm",
        "pattern": "\\b(?!Lot\\b)[A-Za-z]{3,}\\s+(?P<v>\\d{3})\\s+-\\s",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 53,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Creation Homes",
        "field": "land_sqm",
        "pattern": "\\b[1-9]\\s+[1-9](?:\\.\\d)?\\s+[1-9]\\s+(?P<v>\\d{3,4})\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 66,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Creation Homes",
        "field": "street_address",
        "pattern": "^\\S+\\s+(?P<v>\\d+[A-Za-z]?\\s+[A-Za-z][A-Za-z ]*?\\s(?:Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Court|Ct|Crescent|Cres|Parade|Pde|Circuit|Cct|Boulevard|Blvd|Lane|Esplanade))\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 14,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|DBN Homes",
        "field": "bathrooms",
        "pattern": "\\b\\d\\s+(?P<v>\\d)\\s+\\d\\s+\\$[\\d,]+\\s+\\$[\\d,]+\\s+Brochure\\b",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 109,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|DBN Homes",
        "field": "bedrooms",
        "pattern": "\\b(?P<v>\\d)\\s+\\d\\s+\\d\\s+\\$[\\d,]+\\s+\\$[\\d,]+\\s+Brochure\\b",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 109,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|DBN Homes",
        "field": "car_spaces",
        "pattern": "\\b\\d\\s+\\d\\s+(?P<v>\\d)\\s+\\$[\\d,]+\\s+\\$[\\d,]+\\s+Brochure\\b",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 109,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|DBN Homes",
        "field": "frontage_m",
        "pattern": "\\b(?:Street|Road|Drive|Court|Crescent|Way|Boulevard|Avenue|Place|Parade|Terrace|Circuit|Close|Lane|Grove|Esplanade|Highway)\\s+\\d{3,4}\\s+(?P<v>\\d{1,2}(?:\\.\\d+)?)\\s+\\d{1,2}(?:\\.\\d+)?\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 103,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|DBN Homes",
        "field": "land_sqm",
        "pattern": "\\b(?:Street|Road|Drive|Court|Crescent|Way|Boulevard|Avenue|Place|Parade|Terrace|Circuit|Close|Lane|Grove|Esplanade|Highway)\\s+(?P<v>\\d{3,4})\\s+\\d{1,2}(?:\\.\\d+)?\\s+\\d{1,2}(?:\\.\\d+)?\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 103,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|DBN Homes",
        "field": "street_address",
        "pattern": "^(?P<v>\\d+\\s+[A-Za-z]+(?:\\s+[A-Za-z]+)?\\s+(?:Street|Road|Drive|Court|Crescent|Way|Boulevard|Avenue|Place|Parade|Terrace|Circuit|Close|Lane|Grove|Esplanade|Highway))\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 102,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|EVO Homes",
        "field": "bathrooms",
        "pattern": "\\b(?:Adaptable\\s+Living|Single)\\s+\\d{1,2}\\s+(?P<v>\\d{1,2})\\s+\\d{1,2}\\s+\\d{1,2}\\s+[YN]\\s*$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 55,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|EVO Homes",
        "field": "bedrooms",
        "pattern": "\\b(?:Adaptable\\s+Living|Single)\\s+(?P<v>\\d{1,2})\\s+\\d{1,2}\\s+\\d{1,2}\\s+\\d{1,2}\\s+[YN]\\s*$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 55,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|EVO Homes",
        "field": "estate_name",
        "pattern": "^[A-Za-z][A-Za-z ]*?\\s\\d{1,4}\\s+(?P<v>[A-Za-z][A-Za-z' \\-]*?)\\s+[A-Za-z][A-Za-z'\\-]+(?:\\s+(?:Park|North|South|East|West|Creek|Rise|Vale|Waters|Lakes|Downs|Hill|Hills|Heights|Beach|Bay|Valley|Grove|Ridge|Views|Meadows|Ponds|Green|Point|Junction|Springs|Gardens))?\\s+(?:Available|Sold|On\\s+Hold|Reserved)\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 51,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|EVO Homes",
        "field": "frontage_m",
        "pattern": "\\b(?:North|South|East|West)\\s+\\d{3,4}\\s+(?P<v>\\d{1,2}(?:\\.\\d+)?)\\s+\\d{1,2}(?:\\.\\d+)?\\s+(?:REG|CORNER|IRR)\\b",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 52,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|EVO Homes",
        "field": "house_sqm",
        "pattern": "\\b(?:REG|CORNER|IRR)\\s+(?P<v>\\d{2,3}(?:\\.\\d+)?)\\s+(?:Adaptable\\s+Living|Single)\\b",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 55,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|EVO Homes",
        "field": "land_sqm",
        "pattern": "\\b(?:North|South|East|West)\\s+(?P<v>\\d{3,4})\\s+\\d{1,2}(?:\\.\\d+)?\\s+\\d{1,2}(?:\\.\\d+)?\\s+(?:REG|CORNER|IRR)\\b",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 52,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|EVO Homes",
        "field": "lot_number",
        "pattern": "^[A-Za-z][A-Za-z ]*?\\s(?P<v>\\d{1,4})\\s+[A-Za-z]",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 51,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Eternal Homes",
        "field": "suburb",
        "pattern": "\\b(?:NSW|VIC|QLD|SA|WA|ACT|NT|TAS)\\s+(?P<v>[A-Za-z][A-Za-z ]*?)\\s+Lot\\s+\\d+\\b(?!.*\\s(?P=v)(?:\\s+Estate)?\\s+\\d{2,4}\\s*sqm)",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 59,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|G Developments",
        "field": "house_sqm",
        "pattern": "\\$[\\d,]+(?:\\.\\d{2})?\\s+(?P<v>\\d{2,3}(?:\\.\\d+)?)\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 43,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|G Developments",
        "field": "land_sqm",
        "pattern": "\\b\\d(?:\\+\\d)?\\s+\\d(?:\\.\\d)?(?:\\+\\d)?\\s+\\d(?:\\+\\d)?\\s+(?P<v>\\d{3,4})\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 39,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|G Developments",
        "field": "street_address",
        "pattern": "\\b(?:Available|SOLD|Sold|ON HOLD|On Hold)\\s+(?P<v>\\d{1,4}\\s+[A-Za-z][A-Za-z'\\- ]*?\\s+(?:Street|St|Road|Rd|Drive|Dr|Parade|Pde|Avenue|Ave|Court|Ct|Crescent|Cres|Circuit|Cct|Boulevard|Blvd|Terrace|Tce|Place|Pl|Lane|Ln|Way|Close|Grove|Rise|Walk|Esplanade|Esp|Highway|Hwy))\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 18,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Gallery Group",
        "field": "house_sqm",
        "pattern": "^(?!(?:[\\s\\S]*?\\d\\s*m2){2})(?!(?:[\\s\\S]*?\\b(?:Traditional|Terrace|Heron|KRAUSS|JULIAN|HAHN|ROSE)\\s+\\d{3}\\b){2})[\\s\\S]*?\\b(?:Traditional|Terrace|Heron|KRAUSS|JULIAN|HAHN|ROSE)\\s+(?P<v>\\d{3})\\b",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 11,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Gallery Group",
        "field": "lot_number",
        "pattern": "^(?!(?:[\\s\\S]*?\\d\\s*m2){2})[\\s\\S]*?(?P<v>[\\d,]+)\\s+\\d{3,4}\\s*m2\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 4,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Gallery Group",
        "field": "street_address",
        "pattern": "Type Price\\s+(?P<v>(?:\\d+(?:-\\d+)?\\s+)?[A-Za-z][A-Za-z'\\- ]*?\\s+(?:Street|St|Road|Rd|Drive|Dr|Parade|Pde|Avenue|Ave|Court|Ct|Crescent|Cres|Circuit|Cct|Boulevard|Blvd|Terrace|Tce|Place|Pl|Lane|Ln|Way|Close|Grove|Rise|Walk|Esplanade|Esp|Highway|Hwy))\\s*,",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 6,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Goldstate Homes",
        "field": "bedrooms",
        "pattern": "\\b(?P<v>\\d)\\s+(?:Single|Double)\\s+\\d{2,3}\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 45,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Goldstate Homes",
        "field": "frontage_m",
        "pattern": "\\b(?P<v>\\d{1,2}(?:\\.\\d+)?)\\s+\\d{1,2}(?:\\.\\d+)?\\s+\\d{3,4}\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 45,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Goldstate Homes",
        "field": "house_sqm",
        "pattern": "\\b\\d\\s+(?:Single|Double)\\s+(?P<v>\\d{2,3})\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 45,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Goldstate Homes",
        "field": "land_sqm",
        "pattern": "\\b\\d{1,2}(?:\\.\\d+)?\\s+\\d{1,2}(?:\\.\\d+)?\\s+(?P<v>\\d{3,4})\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 45,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Goldstate Homes",
        "field": "lot_number",
        "pattern": "^(?:Titled|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\\d{2})\\s+(?P<v>\\d{1,6})\\s+[A-Za-z]",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 45,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Goldstate Homes",
        "field": "street_address",
        "pattern": "^(?:Titled|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\\d{2})\\s+(?P<v>\\d{1,6}\\s+[A-Za-z]+(?:\\s+[A-Za-z]+)?\\s+(?:Street|St|Road|Rd|Drive|Dr|Parade|Pde|Avenue|Ave|Court|Ct|Crescent|Cres|Circuit|Cct|Boulevard|Blvd|Terrace|Tce|Place|Pl|Lane|Ln|Way|Close|Grove|Rise|Walk|Esplanade|Esp|Highway|Hwy))\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 45,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Hattan Homes",
        "field": "bathrooms",
        "pattern": "\\d\\s+(?P<v>\\d)\\s+\\d\\s+\\$[\\d,]+\\s+\\$[\\d,]+\\s+\\$[\\d,]+\\s*$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 62,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Hattan Homes",
        "field": "bedrooms",
        "pattern": "(?P<v>\\d)\\s+\\d\\s+\\d\\s+\\$[\\d,]+\\s+\\$[\\d,]+\\s+\\$[\\d,]+\\s*$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 62,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Hattan Homes",
        "field": "car_spaces",
        "pattern": "\\d\\s+\\d\\s+(?P<v>\\d)\\s+\\$[\\d,]+\\s+\\$[\\d,]+\\s+\\$[\\d,]+\\s*$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 62,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Hattan Homes",
        "field": "frontage_m",
        "pattern": "\\b(?:House|Townhouse)\\s+(?:Lot\\s+)?\\d{1,5}\\s+\\d{2,4}\\s+(?P<v>\\d{1,2}(?:\\.\\d+)?)\\s",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 63,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Hattan Homes",
        "field": "land_sqm",
        "pattern": "\\b(?:House|Townhouse)\\s+(?:Lot\\s+)?\\d{1,5}\\s+(?P<v>\\d{2,4})\\s+\\d{1,2}(?:\\.\\d+)?\\s",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 63,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Hattan Homes",
        "field": "lot_number",
        "pattern": "\\b(?:House|Townhouse)\\s+(?:Lot\\s+)?(?P<v>\\d{1,5})\\s+\\d{2,4}\\s+\\d{1,2}(?:\\.\\d+)?\\s",
        "transform": "str",
        "verdict": "ACCEPT",
        "fills": 2,
        "precision": 1.0,
        "checked_against": 61
    },
    {
        "cohort": "E-Agent|Hattan Homes",
        "field": "suburb",
        "pattern": "^Available\\s+(?P<v>.+?)\\s+(?:Titled|Q[1-4]\\s+20\\d{2})\\s+(?:House|Townhouse)\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 63,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Hermitage Homes",
        "field": "bedrooms",
        "pattern": "(?<![\\d+.])(?P<v>[1-9])\\s+(?:Single|Double)\\b",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 166,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Hermitage Homes",
        "field": "house_sqm",
        "pattern": "\\b(?:Single|Double)\\s+(?P<v>\\d+)\\b",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 166,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Hermitage Homes",
        "field": "land_sqm",
        "pattern": "[A-Za-z]\\s+(?P<v>\\d{2,4})\\s+(?:Titled|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\\d{2})\\b",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 166,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Hermitage Homes",
        "field": "lot_number",
        "pattern": "^(?P<v>\\d+)(?=\\s)",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 165,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Hermitage Homes",
        "field": "street_address",
        "pattern": "(?P<v>(?-i:[A-Z][a-z]+)(?:\\s(?-i:[A-Z][a-z]+))*\\s(?:Street|Road|Drive|Avenue|Court|Crescent|Boulevard|Parade|Place|Terrace|Circuit|Grove|Lane|Rise|Close|Way|St|Rd|Dr|Ave|Cres|Pde|Tce|Cct))\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 136,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Hudson Homes",
        "field": "lot_number",
        "pattern": "^Available\\s+(?P<v>\\d+)(?=\\s)",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 157,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Luxton Homes",
        "field": "bathrooms",
        "pattern": "\\b\\d\\s+\\d\\s+(?P<v>\\d)\\s+\\d\\s+\\d{2,3}\\.\\d{2}\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 49,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Luxton Homes",
        "field": "bedrooms",
        "pattern": "\\b(?P<v>\\d)\\s+\\d\\s+\\d\\s+\\d\\s+\\d{2,3}\\.\\d{2}\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 49,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Luxton Homes",
        "field": "house_sqm",
        "pattern": "\\b\\d\\s+\\d\\s+\\d\\s+\\d\\s+(?P<v>\\d{2,3}\\.\\d{2})\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 49,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Luxton Homes",
        "field": "land_sqm",
        "pattern": "(?:Titled|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\s+20\\d{2}|\\d{1,2}/\\d{1,2}/\\d{4})\\s+(?P<v>\\d{3}(?:\\.\\d{1,2})?)\\b",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 47,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Luxton Homes",
        "field": "lot_number",
        "pattern": "^[A-Za-z][A-Za-z ]*?\\s(?P<v>\\d{1,4})\\s+[A-Za-z]",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 49,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Luxton Homes",
        "field": "suburb",
        "pattern": "\\b\\d{2,4}\\s+(?P<v>[A-Za-z][A-Za-z' \\-]*?)\\s+(?:Titled|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\s+20\\d{2}|\\d{1,2}/\\d{1,2}/\\d{4})\\b",
        "transform": "str",
        "verdict": "ACCEPT",
        "fills": 38,
        "precision": 1.0,
        "checked_against": 9
    },
    {
        "cohort": "E-Agent|Millwell",
        "field": "bathrooms",
        "pattern": "\\bTower\\s+\\d+\\s+\\d{3,4}\\s+\\d\\s+(?P<v>\\d)\\s+\\d\\s+(?:North|South|East|West)\\b",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 50,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Millwell",
        "field": "bedrooms",
        "pattern": "\\bTower\\s+\\d+\\s+\\d{3,4}\\s+(?P<v>\\d)\\s+\\d\\s+\\d\\s+(?:North|South|East|West)\\b",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 50,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Millwell",
        "field": "house_sqm",
        "pattern": "\\b(?P<v>\\d{2,3})\\s+\\d{1,3}\\s+\\$\\s*\\d",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 50,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Millwell",
        "field": "lot_number",
        "pattern": "\\bTower\\s+\\d+\\s+(?P<v>\\d{3,4})\\s+\\d\\s+\\d\\s+\\d\\s+(?:North|South|East|West)\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 50,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Murrumbeena",
        "field": "lot_number",
        "pattern": "^\\s*\\d{1,2}\\s+(?P<v>\\d{3,4})\\s+(?:\\d\\s*Bed|Studio)\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 56,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Silkwood Homes",
        "field": "car_spaces",
        "pattern": "(?<![\\d/])\\d\\s*/\\s*\\d\\s*/\\s*(?P<v>\\d)\\s*/\\s*\\d(?![\\d/])",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 83,
        "precision": 1.0,
        "checked_against": 3
    },
    {
        "cohort": "E-Agent|Silkwood Homes",
        "field": "house_sqm",
        "pattern": "(?<![\\d.,%])(?P<v>\\d{2,3})\\s+(?:MOD\\s+)?\\d\\s*/\\s*\\d\\s*/\\s*\\d\\s*/\\s*\\d(?![\\d/])",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 86,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Silkwood Homes",
        "field": "land_sqm",
        "pattern": "(?<![\\d.,])(?P<v>\\d{3}|1\\d{3})\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 91,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Silkwood Homes",
        "field": "lot_number",
        "pattern": "\\bPR\\d{3,6}\\s+(?P<v>\\d{1,5})\\s+[A-Za-z]",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 91,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Thomas Paul Constructions",
        "field": "bathrooms",
        "pattern": "(?<![\\d+])[1-9]\\s+(?P<v>[1-9])\\s+(?:Town\\s+House|Dual\\s+Key|Duplex|House)\\b",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 108,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Thomas Paul Constructions",
        "field": "bedrooms",
        "pattern": "(?<![\\d+])(?P<v>[1-9])\\s+[1-9]\\s+(?:Town\\s+House|Dual\\s+Key|Duplex|House)\\b",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 104,
        "precision": 1.0,
        "checked_against": 4
    },
    {
        "cohort": "E-Agent|Thomas Paul Constructions",
        "field": "lot_number",
        "pattern": "^(?P<v>\\d+)(?=\\s)",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 161,
        "precision": 1.0,
        "checked_against": 3
    },
    {
        "cohort": "E-Agent|Thomas Paul Constructions",
        "field": "street_address",
        "pattern": "^\\d+\\s+(?P<v>(?:\\d+(?:-\\d+)?\\s+)?(?-i:[A-Z])[A-Za-z'\\-]*(?:\\s(?-i:[A-Z])[A-Za-z'\\-]*)*\\s(?:Road|Street|Drive|Avenue|Crescent|Court|Parade|Place|Circuit|Boulevard|Terrace|View|Way|Lane|Rise|Close|Grove|Rd|St|Dr|Ave|Cres|Ct|Pde|Cct|Tce))\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 162,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Tomorrow Homes",
        "field": "bedrooms",
        "pattern": "\\s\\d\\s+(?P<v>[1-9])\\s+\\$[\\d,]{7,}",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 103,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Tomorrow Homes",
        "field": "frontage_m",
        "pattern": "\\b(?:St|Ave|Rd|Dr|Cres|Cct|Ct|Way|Loop|Pde|Blvd|Bvd|Tce|Pl|Cl|Esp|Hwy|Mews|Cir)\\b\\s+(?:[A-Za-z]+\\s+){1,3}(?P<v>\\d{1,2}(?:\\.\\d+)?)\\s+\\d{3}\\s+[A-Za-z]{3,}\\b",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 96,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Tomorrow Homes",
        "field": "land_sqm",
        "pattern": "\\b(?:St|Ave|Rd|Dr|Cres|Cct|Ct|Way|Loop|Pde|Blvd|Bvd|Tce|Pl|Cl|Esp|Hwy|Mews|Cir)\\b\\s+(?:[A-Za-z]+\\s+){1,3}\\d{1,2}(?:\\.\\d+)?\\s+(?P<v>\\d{3})\\s+[A-Za-z]{3,}\\b",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 96,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Tomorrow Homes",
        "field": "lot_number",
        "pattern": "\\bBROCHURE\\s*-\\s*(?P<v>\\d+)\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 104,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Tomorrow Homes",
        "field": "street_address",
        "pattern": "(?P<v>\\d+\\s+(?:[A-Za-z]+\\s+)+?(?:St|Ave|Rd|Dr|Cres|Cct|Ct|Way|Loop|Pde|Blvd|Bvd|Tce|Pl|Cl|Esp|Hwy|Mews|Cir)\\b)\\s+(?:[A-Za-z]+\\s+){1,3}\\d{1,2}(?:\\.\\d+)?\\s+\\d{3}\\s+[A-Za-z]{3,}\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 96,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Tomorrow Homes",
        "field": "suburb",
        "pattern": "\\b(?:St|Ave|Rd|Dr|Cres|Cct|Ct|Way|Loop|Pde|Blvd|Bvd|Tce|Pl|Cl|Esp|Hwy|Mews|Cir)\\b\\s+(?P<v>[A-Za-z]+(?:\\s+[A-Za-z]+){0,2})\\s+\\d{1,2}(?:\\.\\d+)?\\s+\\d{3}\\s+[A-Za-z]{3,}\\b",
        "transform": "title",
        "verdict": "UNVERIFIED",
        "fills": 96,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Vanda",
        "field": "car_spaces",
        "pattern": "Car Park:\\s*(?P<v>\\d+)",
        "transform": "int",
        "verdict": "ACCEPT",
        "fills": 15,
        "precision": 1.0,
        "checked_against": 108
    },
    {
        "cohort": "E-Agent|Vanda",
        "field": "house_sqm",
        "pattern": "(?P<v>\\d{2,4}(?:\\.\\d+)?)\\s+\\d{1,3}(?:\\.\\d+)?\\s+\\d{2,4}(?:\\.\\d+)?\\s+\\$[\\d,]{4,}",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 124,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Verv Projects",
        "field": "land_sqm",
        "pattern": "(?<![\\d.,])(?P<v>\\d{3}|1\\d{3})\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 98,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "E-Agent|Verv Projects",
        "field": "lot_number",
        "pattern": "^\\S+\\s+Detached\\s+SS\\s+[A-Za-z]+\\s+(?P<v>\\d{2,5})\\s+[A-Za-z]",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 68,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "Proxima|Ascenta Living (DBN Homes)",
        "field": "street_address",
        "pattern": ",\\s*(?P<v>[A-Za-z][A-Za-z' \\-]*?)\\s*,\\s*[A-Za-z][A-Za-z' \\-]*\\s*,\\s*(?:NSW|VIC|QLD|SA|WA|ACT|NT|TAS)\\s*,\\s*\\d{4}\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 60,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "Proxima|Bathla Development",
        "field": "street_address",
        "pattern": "Unit\\s+\\d+[A-Za-z]?,\\s*(?P<v>\\d[^,]*?)\\s*,",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 122,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "Proxima|Bathla Development",
        "field": "suburb",
        "pattern": ",\\s*(?P<v>[A-Za-z][A-Za-z' -]*?)\\s*,\\s*(?:NSW|VIC|QLD|SA|WA|ACT|NT|TAS|null)\\s*,\\s*\\d{4}\\b",
        "transform": "title",
        "verdict": "ACCEPT",
        "fills": 1,
        "precision": 1.0,
        "checked_against": 123
    },
    {
        "cohort": "Proxima|Coronation",
        "field": "street_address",
        "pattern": ",\\s*(?P<v>\\d[^,]*?)\\s*,\\s*[A-Za-z' \\-]+,\\s*(?:NSW|VIC|QLD|SA|WA|ACT|NT|TAS)\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 96,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "Proxima|Creation Homes NSW Pty Ltd",
        "field": "lot_number",
        "pattern": "^Lot\\s+(?P<v>\\d+)\\b",
        "transform": "str",
        "verdict": "ACCEPT",
        "fills": 8,
        "precision": 1.0,
        "checked_against": 36
    },
    {
        "cohort": "Proxima|Creation Homes NSW Pty Ltd",
        "field": "street_address",
        "pattern": ",\\s*(?P<v>(?:\\d+[A-Za-z]?\\s+)?[A-Za-z][A-Za-z'\\- ]*?\\s+(?:Street|St|Road|Rd|Drive|Dr|Parade|Pde|Avenue|Ave|Court|Ct|Crescent|Cres|Circuit|Cct|Boulevard|Blvd|Terrace|Tce|Place|Pl|Lane|Ln|Way|Close|Grove|Rise|Walk|Esplanade|Esp|Highway|Hwy))\\s*,",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 44,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "Proxima|Creation Homes NSW Pty Ltd",
        "field": "suburb",
        "pattern": ",\\s*(?P<v>[A-Za-z][A-Za-z'\\- ]*?)\\s*,\\s*(?:NSW|New South Wales|VIC|Victoria|QLD|Queensland|SA|South Australia|WA|Western Australia|ACT|Australian Capital Territory|NT|Northern Territory|TAS|Tasmania)\\s*,\\s*\\d{4}\\s*$",
        "transform": "title",
        "verdict": "ACCEPT",
        "fills": 5,
        "precision": 1.0,
        "checked_against": 39
    },
    {
        "cohort": "Proxima|Landmark Group",
        "field": "street_address",
        "pattern": ",\\s*(?P<v>(?:\\d+[A-Za-z]?\\s+)?[A-Za-z][A-Za-z'\\- ]*?\\s+(?:Street|St|Road|Rd|Drive|Dr|Parade|Pde|Avenue|Ave|Court|Ct|Crescent|Cres|Circuit|Cct|Boulevard|Blvd|Terrace|Tce|Place|Pl|Lane|Ln|Way|Close|Grove|Rise|Walk|Esplanade|Esp|Highway|Hwy))\\s*,",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 49,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "Proxima|Landmark Group",
        "field": "suburb",
        "pattern": ",\\s*(?P<v>[A-Za-z][A-Za-z'\\- ]*?)\\s*,\\s*(?:NSW|New South Wales|VIC|Victoria|QLD|Queensland|SA|South Australia|WA|Western Australia|ACT|Australian Capital Territory|NT|Northern Territory|TAS|Tasmania)\\s*,\\s*\\d{4}\\s*$",
        "transform": "title",
        "verdict": "ACCEPT",
        "fills": 34,
        "precision": 1.0,
        "checked_against": 15
    },
    {
        "cohort": "Proxima|Level 33",
        "field": "street_address",
        "pattern": "\\bUnit\\s+[^,]+,\\s*(?P<v>[^,]+?)\\s*,\\s*[^,]+,\\s*(?:NSW|VIC|QLD|SA|WA|ACT|NT|TAS)\\s*,\\s*\\d{4}\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 318,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "Proxima|Liebke and Co Developments",
        "field": "street_address",
        "pattern": "\\bUnit\\s+\\d+\\s*,\\s*(?P<v>[^,]+?)\\s*,",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 100,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "Proxima|MERITON PROPERTY SERVICES PTY LIMITED",
        "field": "street_address",
        "pattern": "\\bUnit\\s+[^,]+,\\s*(?P<v>[^,]+?)\\s*,\\s*[^,]+,\\s*(?:NSW|VIC|QLD|SA|WA|ACT|NT|TAS)\\s*,\\s*\\d{4}\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 187,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "Proxima|Northland",
        "field": "street_address",
        "pattern": "\\bUnit\\s+\\d+\\s*,\\s*(?P<v>[^,]+?)\\s*,",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 112,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|?",
        "field": "bathrooms",
        "pattern": "\\bAvailable\\s+\\d\\s+(?P<v>\\d)\\s+\\d\\s+\\d{2,4}\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 9,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|?",
        "field": "bedrooms",
        "pattern": "\\bAvailable\\s+(?P<v>\\d)\\s+\\d\\s+\\d\\s+\\d{2,4}\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 9,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|?",
        "field": "car_spaces",
        "pattern": "\\bAvailable\\s+\\d\\s+\\d\\s+(?P<v>\\d)\\s+\\d{2,4}\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 9,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|?",
        "field": "house_sqm",
        "pattern": "(?<!Lot\\s)\\b(?P<v>\\d{2,3})\\s+-\\s+[A-Za-z]+(?:\\s+[A-Za-z]+)?\\s+Available\\s+\\d\\s+\\d\\s+\\d\\s+\\d{2,4}\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 9,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|?",
        "field": "land_sqm",
        "pattern": "\\bAvailable\\s+\\d\\s+\\d\\s+\\d\\s+(?P<v>\\d{2,4})\\s+\\$",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 9,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|?",
        "field": "lot_number",
        "pattern": "(?-i:^(?P<v>\\d{1,4})\\s+[A-Z][a-z]+)",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 9,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|?",
        "field": "street_address",
        "pattern": "\\b(?P<v>\\d+[A-Za-z]?\\s+[A-Za-z]+(?:\\s+[A-Za-z]+)?\\s+(?:Street|Road|Drive|Avenue|Court|Place|Way|Crescent|Parade|Boulevard|Circuit|Close|Terrace|Lane|Esplanade|Highway))\\b",
        "transform": "str",
        "verdict": "UNVERIFIED",
        "fills": 3,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|Hattan Homes",
        "field": "bathrooms",
        "pattern": "(?:Single|Double)\\s+Storey\\s+\\d\\s+(?P<v>\\d)\\s+\\d\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 44,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|Hattan Homes",
        "field": "bedrooms",
        "pattern": "(?:Single|Double)\\s+Storey\\s+(?P<v>\\d)\\s+\\d\\s+\\d\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 44,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|Hattan Homes",
        "field": "car_spaces",
        "pattern": "(?:Single|Double)\\s+Storey\\s+\\d\\s+\\d\\s+(?P<v>\\d)\\s+\\$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 44,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|Hattan Homes",
        "field": "frontage_m",
        "pattern": "\\bTownhouse\\s+(?:Lot\\s+)?\\d{2,7}\\s+\\d{2,4}\\s+(?P<v>\\d{1,2}(?:\\.\\d+)?)\\s+[A-Za-z]",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 39,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|Hattan Homes",
        "field": "land_sqm",
        "pattern": "\\bTownhouse\\s+(?:Lot\\s+)?\\d{2,7}\\s+(?P<v>\\d{2,4})\\s",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 40,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|Hattan Homes",
        "field": "lot_number",
        "pattern": "\\bTownhouse\\s+(?:Lot\\s+)?(?P<v>\\d{2,7})\\s+\\d",
        "transform": "str",
        "verdict": "ACCEPT",
        "fills": 9,
        "precision": 1.0,
        "checked_against": 31
    },
    {
        "cohort": "digital email|hattan.com.au",
        "field": "bathrooms",
        "pattern": "\\d\\s+(?P<v>\\d)\\s+\\d\\s+\\$[\\d,]+\\s+\\$[\\d,]+\\s+\\$[\\d,]+\\s*$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 82,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|hattan.com.au",
        "field": "bedrooms",
        "pattern": "(?P<v>\\d)\\s+\\d\\s+\\d\\s+\\$[\\d,]+\\s+\\$[\\d,]+\\s+\\$[\\d,]+\\s*$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 82,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|hattan.com.au",
        "field": "car_spaces",
        "pattern": "\\d\\s+\\d\\s+(?P<v>\\d)\\s+\\$[\\d,]+\\s+\\$[\\d,]+\\s+\\$[\\d,]+\\s*$",
        "transform": "int",
        "verdict": "UNVERIFIED",
        "fills": 82,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|hattan.com.au",
        "field": "frontage_m",
        "pattern": "\\b(?:House|Townhouse)\\s+(?:Lot\\s+)?\\d{1,5}\\s+\\d{2,4}\\s+(?P<v>\\d{1,2}(?:\\.\\d+)?)\\s",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 88,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|hattan.com.au",
        "field": "land_sqm",
        "pattern": "\\b(?:House|Townhouse)\\s+(?:Lot\\s+)?\\d{1,5}\\s+(?P<v>\\d{2,4})\\s+\\d{1,2}(?:\\.\\d+)?\\s",
        "transform": "float",
        "verdict": "UNVERIFIED",
        "fills": 88,
        "precision": None,
        "checked_against": 0
    },
    {
        "cohort": "digital email|hattan.com.au",
        "field": "lot_number",
        "pattern": "\\b(?:House|Townhouse)\\s+(?:Lot\\s+)?(?P<v>\\d{1,5})\\s+\\d{2,4}\\s+\\d{1,2}(?:\\.\\d+)?\\s",
        "transform": "str",
        "verdict": "ACCEPT",
        "fills": 12,
        "precision": 1.0,
        "checked_against": 76
    }
]


_COMPILED = None


def _compiled():
    global _COMPILED
    if _COMPILED is None:
        out = {}
        for rule in RULES:
            try:
                pattern = re.compile(rule["pattern"], re.IGNORECASE)
            except re.error:
                continue
            out.setdefault(rule["cohort"], []).append((rule["field"], pattern,
                                                       rule["transform"]))
        _COMPILED = out
    return _COMPILED


def cohort_of(row: Dict[str, Any]) -> str:
    return "%s|%s" % (row.get("source_channel"), row.get("builder_name") or "?")


def in_range(field: str, value: Any) -> bool:
    lo, hi = RANGES.get(field, (None, None))
    if lo is None:
        return bool(str(value).strip())
    try:
        return lo <= float(value) <= hi
    except (TypeError, ValueError):
        return False


def recover(row: Dict[str, Any]) -> Dict[str, Any]:
    """{field: value} for everything this row's own text states and its columns lack.

    Returns only fields whose value passes the range check. Never returns a field the
    row already holds — the caller decides nothing, so a stored value cannot be
    overwritten by accident.
    """
    text = str(row.get("source_text") or "")
    if not text:
        return {}
    found = {}
    for field, pattern, transform in _compiled().get(cohort_of(row), ()):
        if row.get(field) not in (None, "", 0):
            continue
        match = pattern.search(text)
        if not match:
            continue
        try:
            value = _TRANSFORMS[transform](match.group("v"))
        except Exception:                                            # noqa: BLE001
            continue
        if value is None or not in_range(field, value):
            continue
        found[field] = value
    return found
