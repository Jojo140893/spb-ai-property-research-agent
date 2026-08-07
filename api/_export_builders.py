"""
Build the credential-free builder registry the serverless function is allowed to read.

The pipeline needs the registry for two things: BuilderConfidenceModel (Step 8) and
the `builder_coverage` block the UI's coverage bar renders. Both read only
builder_name / states / contract_available / e_agent_available / portal_url /
notes — the same fields build_web.py already publishes in builders.json.

So the deployed registry is written from scratch here in the 11-column layout
BuilderRegistry._load_primary_builders expects, with the contact columns and BOTH
login columns left empty. Nothing this file writes is absent from builders.json,
which is already public on the deployment.

Run from build_web.py:

    from api._export_builders import write_public_registry
    write_public_registry(out_dir_root)      # writes <root>/api/_data/builders_public.csv

or standalone:  python -m api._export_builders [dest.csv]
"""

import csv
import re
import sys
from pathlib import Path

# Column order BuilderRegistry parses positionally. Index 8 (portal login email) and
# 9 (password) are written EMPTY, always. Renaming these headers is cosmetic; the
# parser is positional, so the order must not change.
HEADER = ["NAME", "EMAIL", "PHONE", "BUILDER", "STATES", "Contract Availble??",
          "Is it available on E Agent?", "WEB PORTAL LINK", "EMAIL", "PASSWORD", "NOTES"]

# Indexes that must be blank in the output. Asserted after writing.
BLANK_COLUMNS = (0, 1, 2, 8, 9)


def _rows(builders):
    for b in builders:
        name = (b.get("builder_name") or "").strip()
        if not name:
            continue
        yield [
            "",                                              # contact name  (PII)
            "",                                              # contact email (PII)
            "",                                              # contact phone (PII)
            name,
            "/".join(b.get("states") or []),
            b.get("contract_available") or "",
            b.get("e_agent_available") or "",
            b.get("portal_url") or "",
            "",                                              # portal login email
            "",                                              # portal password
            _scrub(b.get("notes")),
        ]



# The structured contact columns are blanked above, and NOTES then published the same
# details in prose: "Email comes from Neha@dreamscopehomes.com.au", "Sent email to -
# Anu.saxena@homegroup.com.au". An allow-list filters field NAMES and cannot see inside a
# free-text value, which is exactly how two builder-rep addresses reached the deployed
# bundle. Standing rule 4 is about the details, not the column they sit in.
_CONTACT_IN_PROSE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]{2,}"                       # an email anywhere
    r"|0[2-478](?:[ \-]?\d){8}"                       # AU landline / mobile
    r"|(?:\+?61[ \-]?)[2-478](?:[ \-]?\d){8}", re.I)


def _scrub(text):
    """Free text with any contact detail removed, and the removal made visible."""
    return _CONTACT_IN_PROSE.sub("[contact detail removed]", str(text or ""))


def write_public_registry(root, source_csv=None):
    """Write <root>/api/_data/builders_public.csv. Returns (path, builder_count)."""
    root = Path(root)
    dest = root / "api" / "_data" / "builders_public.csv"
    return _write(dest, source_csv)


def _write(dest, source_csv=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from builder_registry import BuilderRegistry

    builders = BuilderRegistry(source_csv).get_all_builders()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        rows = list(_rows(builders))
        w.writerows(rows)

    # Read the file back and prove the sensitive columns are empty. A silent change
    # to the source layout must fail the build, not ship a password.
    with open(dest, encoding="utf-8") as f:
        for i, row in enumerate(csv.reader(f)):
            if i == 0:
                continue
            for col in BLANK_COLUMNS:
                if len(row) > col and row[col].strip():
                    raise SystemExit(
                        "[ABORT] %s row %d: column %d must be empty, got %r"
                        % (dest, i, col, row[col]))
    return dest, len(rows)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).resolve().parent / "_data" / "builders_public.csv"
    path, n = _write(target)
    print("[+] %s  %d builders, logins and contact PII excluded" % (path, n))
