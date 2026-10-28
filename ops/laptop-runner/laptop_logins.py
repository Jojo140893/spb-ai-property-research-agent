"""Put the logins the laptop nightly needs into THIS laptop's Windows Credential Manager.

The person who owns the laptop runs it, once, from the runner folder (SETUP.md step 4):

    <Root>\\venv311\\Scripts\\python.exe <Root>\\laptop_logins.py            every login
    <Root>\\venv311\\Scripts\\python.exe <Root>\\laptop_logins.py vercel     just the one(s) named
    <Root>\\venv311\\Scripts\\python.exe <Root>\\laptop_logins.py --status   what is stored, no prompts
    <Root>\\venv311\\Scripts\\python.exe <Root>\\laptop_logins.py --forget   remove them all (runner retired)

Every prompt is hidden, and Enter skips it. Nothing is printed, logged or written anywhere but
the vault (service SPB-PropertyAgent). An AI agent must never run the prompts for you: it
would have to see what you type.

    supabase   REQUIRED unless this laptop's own `supabase login` is the SPB project's
               account. A token of an account that can see the SPB project
               (ref dvivicgzphyxtxpkgvjn): supabase.com/dashboard -> Account -> Access Tokens.
               Checked (read-only) before it is stored.
    vercel     REQUIRED on any laptop whose own `vercel login` cannot deploy the dashboard.
               The team digitalx-solutions-projects is on the Hobby plan, which has ONE member,
               so a second person cannot be invited: the team's owner makes a token at
               vercel.com/account/tokens, scope digitalx-solutions-projects, with an expiry.
               Checked with read-only `vercel whoami` / `vercel project ls` before it is stored.
    e_agent    REQUIRED: without it the nightly stops before harvesting (as GitHub's does).
    proxima    optional. Proxima uses the bucket's shared session; a stored login is used only
               when that session has expired. Each refused sign-in counts against the account
               (30 Sep: 3 refusals in a day), so leave it out unless asked.
    hook       optional: the WhatsApp scan's key, only on a laptop that runs whatsapp_scan.ps1.
    inbox      the digital@ inbox's Gmail APP password (myaccount.google.com/apppasswords on
               that account), not its account password. Only on the laptop that should read
               the inbox. Without it the sweep falls back to the login in the vendor CSV,
               which Gmail has refused since 25 Oct, and reads nothing. Safe to store since
               PR #42: sources/email_inbox.py checks Gmail's certificate and name first.
"""
import getpass
import json
import os
import re
import shutil
import subprocess
import sys

import keyring

VAULT = "SPB-PropertyAgent"          # secrets_store.SERVICE
SPB_REF = "dvivicgzphyxtxpkgvjn"
VERCEL_SCOPE = "digitalx-solutions-projects"
VERCEL_PROJECT = "vercel_site"       # the dashboard's Vercel project

# On since PR #42 (TLS-verified IMAP in sources/email_inbox.py). False hides the step again.
INBOX_ENABLED = True

# Tokens go on a command line (the Vercel CLI takes --token, and vercel is a .cmd shim that
# cmd.exe parses), so anything but a plain token is refused rather than passed through.
_TOKEN = re.compile(r"[A-Za-z0-9_.\-]{20,300}")


def _get(key):
    try:
        return keyring.get_password(VAULT, key) or ""
    except Exception:                                                  # noqa: BLE001
        return ""


def _run(cmd, env=None, timeout=90):
    try:
        return subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 1, "", f"{exc.__class__.__name__}")


# --- Supabase ---------------------------------------------------------------------------------

def supabase_exe():
    return shutil.which("supabase") or os.path.expanduser(r"~\bin\supabase.exe")


def supabase_sees_spb(token=None):
    """(ok, why). Read-only: `supabase projects list`, with the token or the CLI's own login."""
    env = {k: v for k, v in os.environ.items() if k != "SUPABASE_ACCESS_TOKEN"}
    if token:
        env["SUPABASE_ACCESS_TOKEN"] = token
    out = _run([supabase_exe(), "projects", "list", "--output", "json"], env=env)
    try:
        listed = json.loads(out.stdout or "[]")
        listed = listed.get("projects", []) if isinstance(listed, dict) else listed
        if SPB_REF in {p.get("ref") for p in listed}:
            return True, "can see the SPB project"
        return False, f"cannot see the SPB project ({SPB_REF})"
    except (ValueError, AttributeError):
        return False, "the Supabase CLI did not answer (installed? signed in?)"


# --- Vercel -----------------------------------------------------------------------------------

def vercel_exe():
    return shutil.which("vercel") or os.path.expandvars(r"%APPDATA%\npm\vercel.cmd")


def vercel_can_deploy(token=None):
    """(ok, why). Read-only: who the login is, and whether it sees the dashboard's project."""
    tok = ["--token", token] if token else []
    who = _run([vercel_exe(), "whoami", *tok])
    if who.returncode != 0:
        return False, ("the token is invalid or expired" if token
                       else "this laptop's Vercel CLI is not signed in")
    ls = _run([vercel_exe(), "project", "ls", "--scope", VERCEL_SCOPE, *tok])
    seen = (ls.stdout or "") + (ls.stderr or "")
    if ls.returncode != 0:
        return False, (f"signed in, but cannot see the team {VERCEL_SCOPE} "
                       "(a personal-scope token? make it with the scope set to the team)")
    if VERCEL_PROJECT not in seen:
        return False, f"sees the team {VERCEL_SCOPE} but not its project {VERCEL_PROJECT}"
    return True, f"can see {VERCEL_SCOPE}/{VERCEL_PROJECT}"


# --- prompts ----------------------------------------------------------------------------------

def ask_token(key, label, check):
    token = getpass.getpass(f"{label} (hidden; Enter skips): ").strip()
    if not token:
        print("  skipped")
        return
    if not _TOKEN.fullmatch(token):
        print("  NOT stored: that does not look like a token (letters, digits, - _ . only)")
        return
    ok, why = check(token)
    if not ok:
        print(f"  NOT stored: {why}")
        return
    keyring.set_password(VAULT, key, token)
    print(f"  stored: it {why}")


def ask_login(portal, name):
    username = getpass.getpass(f"{name} username (hidden, Enter skips): ").strip()
    if not username:
        print("  skipped")
        return
    password = getpass.getpass(f"{name} password (hidden): ")
    if not password:
        print("  skipped (no password)")
        return
    keyring.set_password(VAULT, f"{portal}:username", username)
    keyring.set_password(VAULT, f"{portal}:password", password)
    print("  stored")


def ask_secret(key, label):
    value = getpass.getpass(f"{label} (hidden, Enter skips): ").strip()
    if not value:
        print("  skipped")
        return
    keyring.set_password(VAULT, key, value)
    print("  stored")


STEPS = {
    "supabase": ("Supabase", lambda: ask_token(
        "supabase:access_token", "Supabase access token for the SPB project's account",
        supabase_sees_spb)),
    "vercel": ("Vercel", lambda: ask_token(
        "vercel:token", f"Vercel token scoped to {VERCEL_SCOPE}", vercel_can_deploy)),
    "e_agent": ("E-Agent (required)", lambda: ask_login("e_agent", "E-Agent")),
    "proxima": ("Proxima (optional)", lambda: ask_login("portal_proxima", "Proxima")),
    "hook": ("WhatsApp scan key (optional: only if this laptop runs the WhatsApp scan)",
             lambda: ask_secret("hook:secret", "HOOK_SECRET of vapi-kommo-hook")),
}
if INBOX_ENABLED:
    STEPS["inbox"] = ("digital@ inbox (Gmail app password)",
                      lambda: ask_login("email_inbox", "digital@ inbox"))


# --- status -----------------------------------------------------------------------------------

def status():
    """What the nightly will find. Prints yes/no and reasons, never a value. 0 = ready."""
    have = lambda *keys: all(_get(k) for k in keys)
    rows, ready = [], True

    sb_tok = _get("supabase:access_token")
    if sb_tok:
        ok, why = supabase_sees_spb(sb_tok)
        rows.append((ok, f"Supabase: stored token {why}"))
    else:
        ok, why = supabase_sees_spb()
        rows.append((ok, f"Supabase: no stored token; this laptop's own CLI login {why}"))
    ready &= ok

    v_tok = _get("vercel:token")
    if v_tok and not _TOKEN.fullmatch(v_tok):
        ok = False
        rows.append((ok, "Vercel: the stored token is not a plain token - store it again: laptop_logins.py vercel"))
    elif v_tok:
        ok, why = vercel_can_deploy(v_tok)
        rows.append((ok, f"Vercel: stored token {why}"))
    else:
        ok, why = vercel_can_deploy()
        rows.append((ok, f"Vercel: no stored token; {why}" if not ok else
                     f"Vercel: no stored token; this laptop's own CLI login {why}"))
    ready &= ok

    ok = have("e_agent:username", "e_agent:password")
    rows.append((ok, "E-Agent login " + ("stored" if ok else "MISSING - the nightly will not start")))
    ready &= ok
    rows.append((None, "Proxima login " + ("stored" if have("portal_proxima:username", "portal_proxima:password")
                                           else "not stored (optional: the shared session is used)")))
    rows.append((None, "WhatsApp scan key " + ("stored" if have("hook:secret") else "not stored (optional)")))
    rows.append((None, "digital@ inbox " + (
        ("stored" if have("email_inbox:username", "email_inbox:password") else "not stored")
        if INBOX_ENABLED else "off on laptops until PR #42 (TLS) is merged")))
    for ok, text in rows:
        print({True: "PASS  ", False: "FAIL  ", None: "INFO  "}[ok] + text)
    print("Ready: the nightly can run." if ready else
          "Not ready: the nightly needs Supabase, Vercel and E-Agent.")
    return 0 if ready else 1


MANAGED = ("supabase:access_token", "vercel:token", "e_agent:username", "e_agent:password",
           "portal_proxima:username", "portal_proxima:password", "hook:secret",
           "email_inbox:username", "email_inbox:password")


def forget():
    """Remove every login this script manages from this laptop's vault (the runner is retired)."""
    gone = 0
    for key in MANAGED:
        try:
            if keyring.get_password(VAULT, key):
                keyring.delete_password(VAULT, key)
                gone += 1
        except Exception:                                              # noqa: BLE001
            print(f"  could not remove {key}")
    print(f"removed {gone} login(s) from Credential Manager ({VAULT}). Revoke the Supabase and "
          "Vercel tokens on their websites too.")
    return 0


def main(argv):
    if "--status" in argv:
        return status()
    if "--forget" in argv:
        return forget()
    wanted = [a for a in argv if not a.startswith("-")] or list(STEPS)
    unknown = [w for w in wanted if w not in STEPS]
    if unknown:
        print(f"unknown: {', '.join(unknown)} (choose from {', '.join(STEPS)})")
        return 2
    for n, key in enumerate(wanted, 1):
        title, step = STEPS[key]
        print(f"{n}. {title}")
        step()
    print()
    return status()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
