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

# THIS SCRIPT IS RUN BY HAND, FROM POWERSHELL, AND THAT IS WHERE IT CRASHED.
# run_daily.py launches every step with `-X utf8`, so the arrows and ellipses in the
# prompts below never hit a problem there. Typed directly, Python uses the console's
# code page — cp1252 on this machine — and the first "→" raised
# UnicodeEncodeError from inside input(), before the operator could type anything.
# A sign-in tool that cannot print its own prompt is a sign-in tool nobody can use.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                                 # noqa: BLE001
        pass
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import config
from builder_registry import BuilderRegistry
from builder_names import is_withdrawn_builder
from sources.scraper_base import (SESSION_DIR, PLAYWRIGHT_AVAILABLE, browser_user_agent,
                                  LOGGED_IN_JS, PlaywrightScraper, HARVEST_LOGIN_STATES,
                                  read_harvest_logins, safe_url, signed_out_reason)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _targets():
    """(session_name, label, login_url) for E-Agent + each real direct portal."""
    from sources.portal_config import config_for_url
    reg = BuilderRegistry()
    out, seen = [], set()

    def real(u: str) -> bool:
        u = (u or "").strip().lower()
        return "." in u and not any(x in u for x in ("outlook", "drive.google", "----"))

    for b in reg.get_all_builders():
        url = (b.get("portal_url") or "").strip()
        if not real(url):
            continue
        # Withdrawn by the client: the harvest no longer signs in, so a row here could
        # only age into "expired" and send someone to fix a supplier that is gone.
        if is_withdrawn_builder(b.get("builder_name")):
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
        url = url if "://" in url else "https://" + url
        # The registry holds whatever URL the vendor sheet listed, which for three
        # portals is not the page you can actually sign in on — Proxima's is the
        # MARKETING SITE (www.proxima.com.au) while the real login is a Magento app
        # on a different host entirely, and Hermitage/Bathla point at the root
        # rather than /login. portal_config already carries the confirmed page, so
        # prefer it whenever it has been checked against the live site.
        cfg = config_for_url(url)
        if cfg and cfg.login_verified and cfg.login_url:
            url = cfg.login_url
        out.append((name, b["builder_name"], url))
    return out


LOGIN_STATUS_PATH = SESSION_DIR / "login_status.json"

def _needs_no_login(name, url) -> bool:
    """PortalConfig.no_login_required, read from the config rather than repeated here.

    Torsion's stocklist is public: measured 21 Sep, the session render and the anonymous
    render are byte-identical (innerText sha 3acba30e8b) and BOTH return 235 rows. Asking
    "is this signed in" of it is a meaningless question, and answering it red sent a
    person to re-authenticate something that has no login -- while the harvest, believing
    the same red, threw those rows away nightly for want of credentials to re-authenticate
    WITH. That fact now lives in portal_config, where the harvest reads it too; keeping a
    second copy here is how the tab and the harvest came to disagree in the first place.
    """
    from sources.portal_config import EAGENT_CONFIG, config_for_url
    cfg = EAGENT_CONFIG if name == "e_agent" else config_for_url(url)
    return bool(getattr(cfg, "no_login_required", False))

# NOT OPENED BY THIS CHECK: PROXIMA. Its Login cell comes from the harvest's own sign-in
# (scraper_base.record_harvest_login) and never from a probe here, for two reasons:
#
#   * Its saved session lapses between runs by design. The runner signs back in with the
#     stored password every night ("the saved sign-in has lapsed" / "signed back in") and
#     save_session never writes Proxima's session back, so reopening
#     .sessions/portal_proxima.json an hour later would read "expired" on every healthy
#     night -- and Magento re-challenges 2FA in a fresh context anyway.
#   * On the laptop the harvest uses .browser_profiles/portal_proxima, and writing to that
#     profile can destroy the device-trust cookie that is the only thing keeping Proxima's
#     2FA from firing nightly (export_proxima_session.py). It is never launched here.
#
# This used to read "harvests from .browser_profiles, not this session file", which stopped
# being true on the runner, and with nothing else to go on the cell said NOT CHECKED through
# an 11-day sign-in outage (12 Oct 20:51 to 23 Oct 11:31) while the harvest logged "still
# bounced ... the password may have changed" on 11 of its 12 runs (on 21 Oct the sign-in
# form timed out instead: "sign-in attempt failed (Page.click: Timeout)"). The text below
# is shown only on a night the harvest did not try Proxima at all.
_ELSEWHERE = {"portal_proxima": "only the nightly harvest signs in to Proxima (this check "
                                "never opens it), and it did not try tonight"}


def _probe_session(name, url) -> dict:
    """Open the portal with its saved session, READ-ONLY, and say whether it is signed in.

    {"signed_in": True | False | None, "reason": why not / why it could not look,
     "url": where the page ended (scheme://host/path, never a query)}

    ONE DEFINITION OF "SIGNED IN" PER PORTAL, AND IT IS THE HARVEST'S. This used to load
    the page its own way (no user agent, so "HeadlessChrome"; no viewport; domcontentloaded
    and 1.5 s, no networkidle) and judge it its own way, and both drifted:

      * E-Agent. Its own rule looked for a stocklist link on /access-projects. Since
        24-26 Sep that page is a hub of three buttons with no stocklist link, signed in or
        not, so the check said "expired" on 18 of 18 nights -- including 23 Oct, when the
        harvest reused that very session and read 142 stocklist files from 12 members-only
        pages. The harvest was fixed on 26 Sep (e52befe: follow ACCESS LISTINGS, then the
        category pages); this copy never was. It now asks EAgentSource._authenticated.
      * Hermitage. The harvest's gate passed at 23:39 and read 288 listings; this check
        opened the same file at 00:40 and recorded "expired" with no reason, so a lapsed
        session, a different page load and a swallowed exception all looked the same.

    So the page is opened by PlaywrightScraper itself -- the harvest's user agent, viewport,
    goto with its retries and networkidle wait -- and judged by scraper.is_logged_in or, for
    E-Agent, by the harvest's own functional test. read_only: a check must never write the
    session file it is checking. A saved browser PROFILE is never launched (see _ELSEWHERE).

    None is NOT False: "we could not look" (no Playwright, a timeout, a crash) and "the
    session is dead" send a person to two different places. A crash used to return False
    and read "expired".
    """
    from sources.portal_config import EAGENT_CONFIG, config_for_url
    if not PLAYWRIGHT_AVAILABLE:
        return {"signed_in": None, "reason": "Playwright is not installed here", "url": ""}
    scraper = PlaywrightScraper(session_name=name, headless=True, read_only=True)
    if scraper.profile_dir.exists():
        return {"signed_in": None, "url": "",
                "reason": "the harvest signs in from a saved browser profile, which this "
                          "check never opens"}
    cfg = EAGENT_CONFIG if name == "e_agent" else config_for_url(url)
    where = ""
    try:
        with scraper.session():
            if name == "e_agent":
                from sources.e_agent import EAgentSource
                ok = bool(EAgentSource()._authenticated(scraper))
                where = safe_url(scraper.page.url)
                # The PATH only, as signed_out_reason gives it: the reason is published in
                # channels.json (the row's full url is not), and it is set inside the
                # note's own brackets ("signed out (...)"), so it carries none of its own.
                return {"signed_in": ok, "url": where,
                        "reason": "" if ok else
                        "no stocklist file on the members' pages it opened, the last at %s"
                        % (urlsplit(where).path or "?")}
            scraper.goto(cfg.listings_url or url)
            ok = bool(scraper.is_logged_in(cfg.logged_in_selector or ""))
            where = safe_url(scraper.page.url)
            return {"signed_in": ok, "url": where,
                    "reason": "" if ok else signed_out_reason(scraper.page)}
    except Exception as exc:                                          # noqa: BLE001
        # The class only: a Playwright message carries the URL it was loading, query and all.
        return {"signed_in": None, "url": where,
                "reason": "the check could not run: %s" % type(exc).__name__}


# CAN THE BUTTON FIX THIS ONE? "Fetch stock now" dispatches the harvest, and the harvest
# re-authenticates any lapsed session from stored credentials -- that is how E-Agent
# recovered on 17 Aug after four nights at zero (api/refresh.py says why there is no
# separate login-only job). So a red row is only actionable BY COLEEN when two things
# hold: we hold a password for it, and signing in does not need a human in the loop.
# Paramount's login carries an invisible reCAPTCHA and Proxima wants a 2FA code from an
# inbox; no button signs those in, and a row that implied otherwise would send her to
# press it and watch nothing change. Proxima's other way to go red is the one that
# actually happened (12-23 Oct): the stored password stopped working because it had been
# changed, and the nightly retries the same one -- so it is not a button either.
_NEEDS_A_PERSON = {"portal_paramount_living": "its login has an invisible reCAPTCHA",
                   "portal_proxima": "a changed password must be stored again, or a 2FA "
                                     "code entered from the account's inbox"}


def _self_service(name) -> tuple:
    """(can the Fetch stock now button fix this, why not) for one portal."""
    if name in _NEEDS_A_PERSON:
        return False, _NEEDS_A_PERSON[name]
    try:
        reg = BuilderRegistry()
        for b in reg.get_all_builders():
            url = (b.get("portal_url") or "").strip()
            if not url or "." not in url:
                continue
            slug = "e_agent" if "e-agent" in url.lower() else "portal_" + _slug(b["builder_name"])
            if slug != name:
                continue
            if (b.get("portal_login_email") or "").strip() and \
                    (b.get("portal_login_password") or "").strip():
                return True, ""
    except Exception:                                                 # noqa: BLE001
        return False, "could not read the builder registry"
    # No password stored, so nothing on the runner can sign it in. Naming the sheet
    # rather than the script: this is a row for Coleen, not a developer.
    return False, "no password is stored for it in the vendor sheet"


def _when(iso) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d/%m %H:%M")
    except ValueError:
        return "?"


# What the harvest's own sign-in outcome says on the Settings tab, in Coleen's words.
_HARVEST_SAYS = {
    "session": "signed in by the harvest at %s (its saved session was accepted)",
    "signed_in": "signed in by the harvest at %s (with the stored password: the saved "
                 "session was not enough)",
    "bounced": "the harvest at %s could not sign in: %s",
    "missing": "the harvest at %s had nothing to sign in with: %s",
    "error": "the harvest's sign-in at %s did not finish (%s), so it says nothing either way",
}


def _harvest_note(ran) -> str:
    outcome = ran.get("outcome")
    text = _HARVEST_SAYS.get(outcome, "%s")
    if HARVEST_LOGIN_STATES.get(outcome) == "ok":
        return text % _when(ran.get("at"))
    return text % (_when(ran.get("at")), ran.get("reason") or outcome)


def _settle(entry: dict, name: str, has_file: bool, probe, ran) -> None:
    """Set entry's state, and the basis, reason, url and note behind it.

    TWO MEASUREMENTS, AND WHICH ONE DECIDES. `ran` is what tonight's harvest found when it
    signed in (scraper_base.record_harvest_login); `probe` is this check reopening the saved
    session afterwards. The column exists to answer "did the nightly get in", and the
    harvest is the only thing that saw that happen, so its verdict stands. The check is
    the fallback for a portal the harvest did not try, and it is kept alongside: where the
    two disagree the note says so rather than hiding one of them. On 23 Oct the check said
    Hermitage was signed out an hour after the harvest had read 288 listings on the same
    session; whether Hermitage ends sessions after an hour is not known, and the row should
    show both facts until it is.

    A harvest "error" (a form that timed out, a crash) is no verdict, and is said as such.
    """
    verdict = HARVEST_LOGIN_STATES.get((ran or {}).get("outcome"))
    if probe is not None:
        entry["check"] = {"signed_in": probe.get("signed_in"),
                          "reason": probe.get("reason", ""), "url": probe.get("url", "")}
    if verdict:
        entry.update(state=verdict, basis="harvest", reason=ran.get("reason", ""),
                     url=ran.get("url", ""))
        note = _harvest_note(ran)
        seen = (probe or {}).get("signed_in")
        if seen is not None and seen != (verdict == "ok"):
            note += "; the check after it found the saved session %s" % (
                "signed in" if seen else "signed out (%s)" % probe.get("reason", ""))
        entry["note"] = note
        return
    error_note = _harvest_note(ran) if ran else ""
    if probe is not None:
        entry.update(basis="check", reason=probe.get("reason", ""), url=probe.get("url", ""))
        if probe.get("signed_in") is None:
            entry["state"] = "unknown"
            notes = [probe.get("reason", "")]
        elif probe["signed_in"]:
            entry["state"] = "ok"
            notes = []
        else:
            entry["state"] = "expired"
            notes = ["the saved session is not signed in: %s" % probe.get("reason", "")]
        notes.append(error_note)
        if any(notes):
            entry["note"] = "; ".join(n for n in notes if n)
    elif name in _ELSEWHERE:
        entry["state"] = "unknown"
        entry["note"] = error_note or _ELSEWHERE[name]
    elif not has_file:
        entry["state"] = "missing"
        if error_note:
            entry["note"] = error_note
    else:
        entry["state"] = "unknown"


def check_logins(write: bool = True):
    """[{session, label, state, age_days, checked_at, basis, reason, url, note}] for every
    portal, and write it to .sessions/login_status.json for the Settings tab.

    WHY THE DASHBOARD CANNOT WORK THIS OUT FOR ITSELF. The Settings tab had one signal,
    "how much stock came back", and inferred a sign-in from it. Those are different
    facts and on 20 Sep they disagreed in the worst way: Hermitage read 43% and its
    login was FINE, while E-Agent read 65% on a session that was not signed in at all.
    Worse, a session FILE is written by an anonymous visit too, so `--status` said all
    three of that morning's sign-ins had worked when only one had.

    So the state here is measured, never inferred: 'ok', 'expired' (not let in),
    'missing' (nothing to sign in with), 'unknown' (nobody could look, or the look
    crashed -- `reason` says which). `basis` says who measured it: "harvest" (tonight's
    own sign-in, see _settle) or "check" (the saved session reopened here). `reason` and
    `url` are what the page showed, so a dead session can be told from a crash.
    """
    out = []
    ran_by_harvest = read_harvest_logins()
    for name, label, url in _targets():
        f = SESSION_DIR / f"{name}.json"
        entry = {"session": name, "label": label, "age_days": None,
                 "checked_at": datetime.now().strftime("%d/%m/%Y %H:%M")}
        if _needs_no_login(name, url):
            entry["state"] = "no_login_needed"
        else:
            probe = None
            if f.exists():
                age = datetime.now() - datetime.fromtimestamp(f.stat().st_mtime)
                entry["age_days"] = age.days
                if name not in _ELSEWHERE:
                    probe = _probe_session(name, url)
            _settle(entry, name, f.exists(), probe, ran_by_harvest.get(name))
        if entry["state"] in ("expired", "missing"):
            fixable, why = _self_service(name)
            entry["self_service"] = fixable
            if why:
                entry["blocked_by"] = why
        out.append(entry)
    # E-AGENT'S STOCKLIST SEARCH (7 Oct 2026) has no browser session to open: the harvest
    # signs in with requests and records how that went, and this reports it. With no login
    # stored the row says where one comes from -- the client's E-Agent BDM -- which is the
    # one thing that unblocks it. Never fatal, like every other row here.
    try:
        from sources.eagent_stocklist import login_row
        out.append(login_row())
    except Exception as e:                                            # noqa: BLE001
        print(f"[warn] could not report the EAgent Stocklist login: {e}")
    if write:
        try:
            SESSION_DIR.mkdir(exist_ok=True)
            LOGIN_STATUS_PATH.write_text(json.dumps(out, indent=1), encoding="utf-8")
        except OSError as e:                                          # noqa: BLE001
            print(f"[warn] could not write {LOGIN_STATUS_PATH}: {e}")
    return out


def status(verify: bool = False):
    """List sessions. A session FILE can exist from an anonymous visit, so file
    presence alone does not mean 'logged in' — use --verify to actually check."""
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
        if name in _ELSEWHERE:
            print(f"  [NOT OPENED] {label:<40} {stamp}  {_ELSEWHERE[name]}")
            continue
        # The same probe --check records: this used to be a third copy of "signed in",
        # with neither the password veto nor E-Agent's functional test.
        probe = _probe_session(name, url)
        ok = probe["signed_in"]
        tag = "LOGGED IN" if ok else ("NOT LOGGED IN" if ok is False else "COULD NOT LOOK")
        print(f"  [{tag}] {label:<40} {stamp}  {probe['reason']}")
        if ok is False:
            print(f"               → run: python portal_login.py {name}")


# JS heuristic: are we in an authenticated state? Looks for logout/account markers.
# Shared with the scrapers — see the note on LOGGED_IN_JS in sources/scraper_base.py.
# This file used to carry its own copy, which treated Magento's 2FA page as "signed
# in" and closed the browser before the OTP could be entered.
_LOGGED_IN_JS = LOGGED_IN_JS


def _open_login_form(page, name: str):
    """Click through to the actual email/password form so the operator lands on the
    fields directly instead of a signup modal. Navigation only — never fills them."""
    if name == "e_agent":
        # Exact matches, the same path as the nightly login. The substring steps below
        # clicked "Log in with Google" on E-Agent's log-in panel (review, 26 Sep), opening
        # a Google popup in front of whoever was signing in.
        from sources.e_agent import open_members_form
        try:
            return open_members_form(page)[2]
        except Exception:
            return False
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
            # Same UA the harvest will present. Without this the sign-in happens as
            # the real Chrome/<current> and the harvest as whatever scraper_base sent,
            # so a portal that ties "remember this device" to the UA re-challenges for
            # 2FA on every run and this one-time sign-in never stays done.
            ctx = pw.chromium.launch_persistent_context(
                str(prof), headless=False, no_viewport=True, args=["--start-maximized"],
                user_agent=browser_user_agent(pw=pw))
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)
                _open_login_form(page, name)
            except Exception as e:
                print(f"  (slow load: {e})")
            saved = False
            deadline = time.time() + wait_minutes * 60
            # The signed-in state must HOLD, not just occur once. A single positive
            # reading is what closed the browser on Coleen mid-2FA: the detector fired
            # on a transient post-password page and the window vanished before the OTP
            # could be typed. Requiring three consecutive reads ~3s apart costs about
            # six seconds and makes a momentary state impossible to mistake for done.
            CONFIRMATIONS = 3
            streak, announced = 0, False
            while time.time() < deadline:
                try:
                    if page.is_closed():
                        break
                    if page.evaluate(_LOGGED_IN_JS):
                        streak += 1
                        if streak == 1 and not announced:
                            print("  signed-in state detected — confirming (leave the window open)...")
                            announced = True
                        if streak >= CONFIRMATIONS:
                            # keep BOTH: the profile (durable) and a storage_state export
                            ctx.storage_state(path=str(SESSION_DIR / f"{name}.json"))
                            print(f"  [SAVED] signed in — profile kept and session exported to {name}.json")
                            saved = True
                            break
                    elif streak:
                        # Went back to a login/challenge page: not in after all.
                        print("  still authenticating (a 2FA or login step is on screen) — waiting...")
                        streak, announced = 0, False
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
    if "--check" in args:
        # What the nightly runs before the build, so the Settings tab can show a MEASURED
        # sign-in rather than one inferred from how much stock came back. Never fatal:
        # a portal that cannot be reached is worth a row saying so, not a failed harvest.
        for row in check_logins():
            # The reason and who measured it, so a red row in the log can be told from a
            # crash without opening login_status.json (23 Oct: two "[expired ]" lines and
            # nothing else).
            print("  [%-8s] %-42s %s%s" % (
                row["state"], row["label"],
                "" if row["age_days"] is None else f"saved {row['age_days']}d ago",
                "  -- %s: %s" % (row.get("basis") or "?", row.get("note") or row.get("reason"))
                if (row.get("note") or row.get("reason")) else ""))
        print(f"\nwritten to {LOGIN_STATUS_PATH}")
        raise SystemExit(0)
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
