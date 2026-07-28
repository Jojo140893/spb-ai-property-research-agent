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
    python portal_login.py <portal> --profile  # sign in inside a PERSISTENT browser profile
                                              # (best for reCAPTCHA / 2FA portals)
"""

import sys
import re
import time
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


# JS heuristic: are we in an authenticated state? Looks for logout/account markers.
_LOGGED_IN_JS = r"""
() => {
  const t = (document.body ? document.body.innerText : '').toLowerCase();
  const links = [...document.querySelectorAll('a,button')].map(e => (e.innerText||'').toLowerCase());
  const markers = ['log out','logout','sign out','signout','my account','my profile','dashboard'];
  const hasMarker = links.some(l => markers.some(m => l.includes(m))) || markers.some(m => t.includes(m));
  const loginish = /sign in|log in|login|password/.test(t) && !hasMarker;
  return hasMarker && !loginish;
}
"""


def _open_login_form(page, name: str):
    """Click through to the actual email/password form so the operator lands on the
    fields directly instead of a signup modal. Navigation only — never fills them."""
    steps = [
        "button:has-text('Already a member')", "button:has-text('Log In')",
        "a:has-text('Log In')", "button:has-text('Sign In')",
        "button:has-text('Log in with Email')", "button:has-text('Sign in with email')",
    ]
    for sel in steps:
        try:
            if page.query_selector("input[type=password]"):
                return True  # password field already visible — nothing to click
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                page.wait_for_timeout(1200)
        except Exception:
            continue
    try:
        return page.query_selector("input[type=password]") is not None
    except Exception:
        return False


PROFILE_DIR = SESSION_DIR.parent / ".browser_profiles"


def capture_profile(only: str = "", wait_minutes: int = 45):
    """Sign in inside a PERSISTENT browser profile.

    More robust than storage_state for sites behind reCAPTCHA/2FA: the profile
    keeps cookies, local storage and device trust on disk, so the sign-in (and any
    "remember this device" choice) survives future runs and the site is far less
    likely to re-challenge. The scrapers can reuse the same profile directory.
    """
    if not (PLAYWRIGHT_AVAILABLE and sync_playwright):
        print("[ERROR] Playwright missing. Run: pip install playwright && python -m playwright install chromium")
        sys.exit(1)
    targets = _targets()
    if only:
        q = only.lower()
        targets = [t for t in targets if q in t[0].lower() or q in t[1].lower()]
    if not targets:
        print(f"[ERROR] no portal matches '{only}'")
        sys.exit(1)

    PROFILE_DIR.mkdir(exist_ok=True)
    SESSION_DIR.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        for name, label, url in targets:
            prof = PROFILE_DIR / name
            prof.mkdir(parents=True, exist_ok=True)
            print("=" * 72)
            print(f"  {label}\n  {url}")
            print(f"  Persistent profile: {prof}")
            print(f"  Sign in in the window. Tick 'remember me' if offered. Waiting up to {wait_minutes} min.")
            ctx = pw.chromium.launch_persistent_context(
                str(prof), headless=False, no_viewport=True, args=["--start-maximized"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)
                _open_login_form(page, name)
            except Exception as e:
                print(f"  (slow load: {e})")
            saved = False
            deadline = time.time() + wait_minutes * 60
            while time.time() < deadline:
                try:
                    if page.is_closed():
                        break
                    if page.evaluate(_LOGGED_IN_JS):
                        # keep BOTH: the profile (durable) and a storage_state export
                        ctx.storage_state(path=str(SESSION_DIR / f"{name}.json"))
                        print(f"  [SAVED] signed in — profile kept and session exported to {name}.json")
                        saved = True
                        break
                except Exception:
                    pass
                time.sleep(3)
            if not saved:
                # even without positive detection the profile may now hold the login
                try:
                    ctx.storage_state(path=str(SESSION_DIR / f"{name}.json"))
                    print(f"  [PROFILE KEPT] no signed-in state detected, but the profile and "
                          f"any cookies were preserved — a harvest may still work.")
                except Exception:
                    print("  [NOT SAVED] nothing captured.")
            try:
                ctx.close()
            except Exception:
                pass
    print("\nNext: python harvest_buildings.py")


def capture_auto(only: str = "", wait_minutes: int = 45):
    """Open a visible browser per portal and save the session AS SOON AS you are
    signed in — no terminal keypress needed (works when launched by an agent).

    Your password is never read by this tool; only the resulting session cookies
    are saved locally to .sessions/.
    """
    if not (PLAYWRIGHT_AVAILABLE and sync_playwright):
        print("[ERROR] Playwright missing. Run: pip install playwright && python -m playwright install chromium")
        sys.exit(1)

    targets = _targets()
    if only:
        q = only.lower()
        targets = [t for t in targets if q in t[0].lower() or q in t[1].lower()]
        if not targets:
            print(f"[ERROR] No portal matches '{only}'.")
            sys.exit(1)

    SESSION_DIR.mkdir(exist_ok=True)
    print("=" * 72)
    print("  PORTAL LOGIN (auto-detect) — a browser window is opening for you")
    print("=" * 72)

    with sync_playwright() as pw:
        for name, label, url in targets:
            sess = SESSION_DIR / f"{name}.json"
            print(f"\n>>> {label}\n    {url}")
            print(f"    Sign in in the browser window. Waiting up to {wait_minutes} min; "
                  f"the session saves itself the moment you're in.")
            browser = pw.chromium.launch(headless=False, args=["--start-maximized"])
            ctx = browser.new_context(storage_state=str(sess) if sess.exists() else None,
                                      no_viewport=True)
            page = ctx.new_page()
            saved = False
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)
                if _open_login_form(page, name):
                    print("    Login form is open — type your password and sign in.")
                else:
                    print("    (couldn't auto-open the form; click Log In in the window)")
            except Exception as e:
                print(f"    (slow load: {e})")
            deadline = time.time() + wait_minutes * 60
            while time.time() < deadline:
                try:
                    if page.is_closed():
                        print("    (window closed)")
                        break
                    if page.evaluate(_LOGGED_IN_JS):
                        ctx.storage_state(path=str(sess))
                        print(f"    [SAVED] signed-in session -> {sess.name}")
                        saved = True
                        break
                except Exception:
                    pass
                time.sleep(3)
            if not saved:
                print(f"    [NOT SAVED] no signed-in state detected for {label}.")
                print(f"                Re-run later:  python portal_login.py {name}")
            try:
                ctx.close(); browser.close()
            except Exception:
                pass

    print("\n" + "=" * 72)
    print("Next:  python harvest_buildings.py")
    print("=" * 72)


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
        raise SystemExit(0)
    pos = [a for a in args if not a.startswith("--")]
    only = pos[0] if pos else ""
    if "--profile" in args:
        capture_profile(only)
    elif "--auto" in args or not sys.stdin.isatty():
        # No interactive terminal (or explicitly asked): auto-detect sign-in.
        if not sys.stdin.isatty() and "--auto" not in args:
            print("[i] No interactive terminal detected - using auto-detect mode.\n")
        capture_auto(only)
    else:
        capture(only)
