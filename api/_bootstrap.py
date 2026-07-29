"""
Serverless bootstrap for the SPB pipeline (Vercel Python function).

Everything in this module exists because the pipeline was written for a laptop and
the function runs somewhere it cannot write, must not scrape and must not hold a
credential. It does three things, in this order, and nothing else:

  1. puts the repo root on sys.path so `import kommo_agent` works from api/;
  2. makes `import config` survive a read-only filesystem, then re-points every
     writable path config exposes at the one writable directory (/tmp);
  3. refuses to run at all if a credential-bearing file reached the deployment.

Point 3 is the important one. It is a tripwire, not a cleanup: if the bundle
contains a portal password, a saved portal session or the live vendor CSV, the
function returns 500 with the offending path instead of quietly serving traffic.
A leaked portal session is reusable by anyone who finds it.
"""

import errno
import os
import pathlib
import sys
import tempfile

# Two supported layouts for the pipeline modules, both handled here so build_web.py
# can pick either:
#   api/_lib/kommo_agent.py ...  (preferred: Vercel's static builder excludes api/**,
#                                 so the modules are not downloadable from the site)
#   ./kommo_agent.py ...         (flat, i.e. the repo layout; the .py files are then
#                                 served as static assets like any other file)
# ROOT is also where the deployed stock.json is looked for, and what the credential
# tripwire scans.
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
LIB = HERE / "_lib"
for _path in (LIB, ROOT):
    if _path.is_dir() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# The only writable place on Vercel. Reused across warm invocations of the same
# instance, so the scratch audit DB is created once per cold start, not per request.
SCRATCH = pathlib.Path(os.environ.get("SPB_SCRATCH") or tempfile.gettempdir()) / "spb"

ON_VERCEL = bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))

# Files that must never be bundled. Checked against the deployed tree at runtime.
# Book1(Builders) List.csv column 9 is a plaintext portal password; .sessions/*.json
# are live authenticated portal cookies; drive_input/vendors.csv is the live
# credentialed vendor list.
FORBIDDEN_PATHS = (
    "Book1(Builders) List.csv",
    "drive_input/vendors.csv",
    ".env",
    "credentials.json",
    "spb_research_audit.db",          # vendor contact PII + brochure text; not needed
)
FORBIDDEN_GLOBS = (
    ".sessions/*.json",
    "spb_research_audit.db.bak-*",
)

# The sanitised registry the function is allowed to read. Written at build time by
# api/_export_builders.py (call it from build_web.py). Contains only fields that
# build_web.py already publishes in builders.json — no logins, no contact PII.
PUBLIC_REGISTRY_CSV = HERE / "_data" / "builders_public.csv"


def bundle_violations():
    """Credential-bearing files present in the deployed tree. Empty list = clean."""
    bad = []
    for base in {ROOT, LIB}:
        if not base.is_dir():
            continue
        for rel in FORBIDDEN_PATHS:
            if (base / rel).exists():
                bad.append(_rel(base / rel))
        for pattern in FORBIDDEN_GLOBS:
            for hit in base.glob(pattern):
                bad.append(_rel(hit))
    return sorted(set(bad))


def _rel(path):
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _import_config_on_readonly_fs():
    """Import `config`, tolerating its module-level mkdir() calls.

    config.py creates output/, drive_input/ and assets/ at import time. On Vercel
    those live under the read-only /var/task, so mkdir raises EROFS and the import —
    and therefore every pipeline module — dies. Path.mkdir is wrapped for the
    duration of that one import and restored immediately after, so no pipeline code
    ever runs against a patched mkdir.
    """
    real_mkdir = pathlib.Path.mkdir

    def tolerant_mkdir(self, mode=0o777, parents=False, exist_ok=False):
        try:
            return real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)
        except OSError as exc:
            if exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM):
                return None                      # read-only bundle: nothing to create
            raise

    pathlib.Path.mkdir = tolerant_mkdir
    try:
        import config
        return config
    finally:
        pathlib.Path.mkdir = real_mkdir


def _redirect_writable_paths(config):
    """Point everything config can write at /tmp, and the registry at the safe CSV.

    DATABASE_PATH matters most: ResearchDatabase.__init__ runs CREATE TABLE, so a
    bundled read-only .db would make the agent constructor throw. The function gets
    a fresh, empty scratch DB instead — the audit trail it writes is therefore
    EPHEMERAL and dies with the instance. The building stock it scores is read
    separately, read-only, by api/_candidates.py.
    """
    SCRATCH.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_DIR = SCRATCH / "output"
    config.ASSETS_DIR = SCRATCH / "assets"
    # Empty on purpose: DrivePdfSource scans this directory and BenchmarkEngine
    # globs comparables*.csv out of it. Pointing it at an empty /tmp dir means
    # neither can pick up anything that was not deliberately deployed.
    config.DRIVE_INPUT_DIR = SCRATCH / "drive_input"
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config.DRIVE_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    config.DATABASE_PATH = SCRATCH / "audit_ephemeral.db"
    # BuilderPortalSource() builds its own BuilderRegistry() off this global, so it
    # has to point at the sanitised file too — not just the copy we pass explicitly.
    if PUBLIC_REGISTRY_CSV.exists():
        config.BUILDER_CSV_PATH = PUBLIC_REGISTRY_CSV
    else:
        config.BUILDER_CSV_PATH = SCRATCH / "no_registry.csv"   # absent -> empty registry
    # Belt and braces: no credential can be read out of the environment either.
    config.E_AGENT_USERNAME = ""
    config.E_AGENT_PASSWORD = ""
    config.KOMMO_ACCESS_TOKEN = ""
    config.KOMMO_DRY_RUN = True
    return config


def registry_violations(registry):
    """Refuse to serve if the loaded registry carries logins."""
    bad = []
    for b in registry.get_all_builders():
        if (b.get("portal_login_password") or "").strip():
            bad.append("portal_login_password for %s" % b.get("builder_name"))
        if (b.get("portal_login_email") or "").strip():
            bad.append("portal_login_email for %s" % b.get("builder_name"))
    return bad[:5]


class BootstrapError(RuntimeError):
    pass


class ScrapeAttempted(RuntimeError):
    """Raised if anything on this deployment tries to reach a builder's portal."""


def _disarm_sources(agent):
    """Make scraping impossible, not merely unreachable.

    api/research.py only ever calls run_property_research with a non-empty
    candidate_packages, which skips the hybrid-search block. This is the second lock:
    if that ever regresses (an empty list is falsy, and `if not candidate_packages:`
    would then start a live crawl of every approved builder from a public URL), the
    request fails loudly here instead of hammering the vendors' portals.
    """
    def refuse(*_args, **_kwargs):
        raise ScrapeAttempted(
            "the deployed research endpoint must not scrape; it scores stock already "
            "in the database. A caller reached a live source's search().")

    for attr in ("eagent_source", "portal_source", "drive_source"):
        source = getattr(agent, attr, None)
        if source is not None:
            source.search = refuse
            source.verify = refuse
    return agent


_AGENT = None
_CONFIG = None


def get_config():
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = _redirect_writable_paths(_import_config_on_readonly_fs())
    return _CONFIG


def get_agent():
    """The real pipeline object, built once per warm instance.

    KommoPropertyResearchAgent's constructor is network-free — EAgentSource,
    BuilderPortalSource and DrivePdfSource only read credentials/paths and build a
    requests.Session lazily. Nothing here reaches the internet. `search()` is the
    method that scrapes and it is never called: api/research.py always supplies
    candidate_packages, which makes run_property_research skip the whole hybrid
    search block (kommo_agent.py:73 `if not candidate_packages:`).
    """
    global _AGENT
    if _AGENT is not None:
        return _AGENT

    if ON_VERCEL:
        bad = bundle_violations()
        if bad:
            raise BootstrapError(
                "refusing to run: credential-bearing file(s) were deployed: "
                + ", ".join(bad))

    config = get_config()
    from kommo_agent import KommoPropertyResearchAgent

    agent = KommoPropertyResearchAgent(csv_filepath=str(config.BUILDER_CSV_PATH))
    _disarm_sources(agent)
    leaks = registry_violations(agent.builder_registry)
    if leaks:
        raise BootstrapError(
            "refusing to run: builder registry contains logins (" + "; ".join(leaks)
            + "). Deploy the sanitised CSV from api/_export_builders.py instead.")
    _AGENT = agent
    return _AGENT
