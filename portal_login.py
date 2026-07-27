"""
One-time interactive login — you log in, the app remembers the session.

Opens a real visible browser at each portal's login page and waits while YOU sign
in (type it, or let your password manager fill it). When you press Enter, the
authenticated session is saved to .sessions/<name>.json (gitignored).

After that, harvest_buildings.py reuses the saved session and needs no password
at all — you only repeat this if a session expires.

Usage:
    python portal_login.py                 # walk through every portal that needs a login
    python portal_login.py e_agent         # just one (session name or builder name)
    python portal_login.py --status        # show which sessions exist and how old they are
"""

import sys
import re
from datetime import datetime
from pathlib import Path

import config
from builder_registry import BuilderRegistry
from sources.scraper_base import SESSION_DIR, PLAYWRIGHT_AVAILABLE

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _targets():
    """(session_name, label, login_url) for E-Agent + each real direct portal."""
    reg = BuilderRegistry()
    out, seen = [], set()

    def real(u: str) -> bool:
        u = (u or "").strip().lower()
        return "." in u and not any(x in u for x in ("outlook", "drive.google", "----"))

    for b in reg.get_all_builders():
        url = (b.get("portal_url") or "").strip()
        if not real(url):
            continue
        if "e-agent" in url.lower():
            if "e_agent" not in seen:
                seen.add("e_agent")
                out.append(("e_agent", "E-Agent (covers all E-Agent builders)", url))
            continue
        name = "portal_" + _slug(b["builder_name"])
        if name in seen:
            continue
        seen.add(name)
        out.append((name, b["builder_name"], url if "://" in url else "https://" + url))
    return out


def status(verify: bool = False):
    """List sessions. A session FILE can exist from an anonymous visit, so file
    presence alone does not mean 'logged in' — use --verify to actually check."""
    from sources.portal_config import EAGENT_CONFIG, config_for_url
    print("Saved sessions in", SESSION_DIR)
    if not verify:
        print("(file presence only — run with --verify to confirm each is really logged in)\n")
    for name, label, url in _targets():
        f = SESSION_DIR / f"{name}.json"
        if not f.exists():
            print(f"  [NO SESSION] {label:<40} run: python portal_login.py {name}")
            continue
        age = datetime.now() - datetime.fromtimestamp(f.stat().st_mtime)
        stamp = f"saved {age.days}d {age.seconds//3600}h ago"
        if not verify:
            print(f"  [FILE]       {label:<40} {stamp}")
            continue
        cfg = EAGENT_CONFIG if name == "e_agent" else config_for_url(url)
        ok = False
        try:
            with sync_playwright() as pw:
                br = pw.chromium.launch(headless=True)
                ctx = br.new_context(storage_state=str(f))
                pg = ctx.new_page()
                pg.goto(cfg.listings_url or url, wait_until="domcontentloaded", timeout=30000)
                pg.wait_for_timeout(1500)
                sel = cfg.logged_in_selector or ""
                ok = any(pg.query_selector(s.strip()) for s in sel.split(",") if s.strip())
                br.close()
        except Exception:
            ok = False
        print(f"  [{'LOGGED IN' if ok else 'NOT LOGGED IN'}] {label:<40} {stamp}")
        if not ok:
            print(f"               → run: python portal_login.py {name}")


def capture(only: str = ""):
    if not (PLAYWRIGHT_AVAILABLE and sync_playwright):
        print("[ERROR] Playwright missing. Run: pip install playwright && python -m playwright install chromium")
        sys.exit(1)

    targets = _targets()
    if only:
        q = only.lower()
        targets = [t for t in targets if q in t[0].lower() or q in t[1].lower()]
        if not targets:
            print(f"[ERROR] No portal matches '{only}'. Options:")
            for n, l, _ in _targets():
                print(f"    {n:<28} {l}")
            sys.exit(1)

    SESSION_DIR.mkdir(exist_ok=True)
    print("=" * 72)
    print("  INTERACTIVE PORTAL LOGIN — you sign in, the app saves the session")
    print("=" * 72)
    print("A browser window will open for each portal. Sign in as you normally would")
    print("(your password manager can fill it), then come back here and press Enter.")
    print("Your password is never read, stored, or transmitted by this tool — only")
    print("the resulting session cookies are saved locally to .sessions/.\n")

    with sync_playwright() as pw:
        for name, label, url in targets:
            print("-" * 72)
            print(f"  {label}\n  {url}")
            browser = pw.chromium.launch(headless=False)
            sess = SESSION_DIR / f"{name}.json"
            ctx = browser.new_context(storage_state=str(sess) if sess.exists() else None,
                                      viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"  (page slow to load: {e} — carry on once it appears)")
            try:
                input("  → Sign in in the browser, then press Enter here to save the session… ")
            except EOFError:
                print("  [SKIP] no interactive terminal available")
                ctx.close(); browser.close(); continue
            try:
                ctx.storage_state(path=str(sess))
                print(f"  [SAVED] {sess}")
            except Exception as e:
                print(f"  [ERROR] could not save session: {e}")
            ctx.close()
            browser.close()

    print("\n" + "=" * 72)
    print("Done. Now run:  python harvest_buildings.py")
    print("=" * 72)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if "--status" in args or "--verify" in args:
        status(verify="--verify" in args)
    else:
        capture(args[0] if args else "")
