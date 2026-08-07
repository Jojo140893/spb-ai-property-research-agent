"""
Store portal credentials in the OS vault (Windows Credential Manager / Keychain).

Run this ONCE per portal. Passwords are typed into a hidden prompt and written
straight into the OS vault — they are never echoed, logged, written to the repo,
or kept in the vendor CSV. After this, harvest_buildings.py authenticates by
itself with no human involved.

Usage:
    python setup_credentials.py                 # walk through every portal
    python setup_credentials.py e_agent         # just one
    python setup_credentials.py --status        # show what's configured (no secrets)
    python setup_credentials.py --import-csv    # move creds OUT of the vendor CSV into the vault
    python setup_credentials.py --forget e_agent
"""

import re
import sys
from getpass import getpass

import secrets_store
from builder_registry import BuilderRegistry


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# API keys the app can use that are NOT builder portals, so _portals() below -- which
# reads the builder registry -- can never discover them. Without this entry the one
# instruction printed for the single item blocking the benchmark,
#
#     python setup_credentials.py domain_api
#
# answered "no portal matches 'domain_api'" and listed seven builder logins. The
# blocking step had no working command behind it.
_API_KEYS = (
    ("domain_api", "Domain API (market comparables for the benchmark)"),
    ("proptrack_api", "PropTrack API (realestate.com.au data, via REA Group)"),
)


def _portals():
    """(key, label, csv_user, csv_pass) for E-Agent + each real direct portal, plus the
    non-portal API keys above."""
    reg = BuilderRegistry()
    out, seen = [], set()
    for key, label in _API_KEYS:
        out.append((key, label, "", ""))
        seen.add(key)

    def real(u: str) -> bool:
        u = (u or "").strip().lower()
        return "." in u and not any(x in u for x in ("outlook", "drive.google", "----"))

    for b in reg.get_all_builders():
        url = (b.get("portal_url") or "").strip()
        if not real(url):
            continue
        key = "e_agent" if "e-agent" in url.lower() else "portal_" + _slug(b["builder_name"])
        label = "E-Agent (covers all E-Agent builders)" if key == "e_agent" else b["builder_name"]
        if key in seen:
            continue
        seen.add(key)
        out.append((key, label, b.get("portal_login_email", ""), b.get("portal_login_password", "")))
    return out


def status():
    print("Credential vault:", "OS vault available" if secrets_store.KEYRING_AVAILABLE else "keyring NOT installed")
    print(f"(service name: {secrets_store.SERVICE})\n")
    for key, label, cu, cp in _portals():
        # include the CSV fallback so the report shows where each credential
        # actually lives today, not just whether the vault has it
        u, p, src = secrets_store.get_credentials(key, (cu, cp))
        if not (u and p):
            print(f"  [--     ] {label:<40} no credentials anywhere")
            continue
        masked = (u[:2] + "***@" + u.split("@", 1)[1]) if "@" in u else (u[:2] + "***")
        if src == "vault":
            print(f"  [VAULT  ] {label:<40} {masked}")
        elif src == "env":
            print(f"  [ENV    ] {label:<40} {masked}")
        else:
            print(f"  [CSV !! ] {label:<40} {masked}  <-- plaintext on disk; run --import-csv")


def prompt_store(key: str, label: str, suggested_user: str = ""):
    print(f"\n--- {label}")
    prompt_u = f"  username/email [{suggested_user}]: " if suggested_user else "  username/email: "
    user = input(prompt_u).strip() or suggested_user
    if not user:
        print("  (skipped - no username given)")
        return False
    pw = getpass("  password (hidden, not echoed): ")
    if not pw:
        print("  (skipped - no password given)")
        return False
    secrets_store.store_credentials(key, user, pw)
    print(f"  [STORED] {label} -> OS vault (key '{key}')")
    return True


def interactive(only: str = ""):
    if not secrets_store.KEYRING_AVAILABLE:
        print("[ERROR] keyring is not installed. Run: pip install keyring")
        sys.exit(1)
    targets = _portals()
    if only:
        targets = [t for t in targets if only.lower() in t[0].lower() or only.lower() in t[1].lower()]
        if not targets:
            print(f"[ERROR] no portal matches '{only}'. Options:")
            for k, l, *_ in _portals():
                print(f"    {k:<26} {l}")
            sys.exit(1)

    print("=" * 70)
    print("  STORE PORTAL CREDENTIALS IN THE OS VAULT")
    print("=" * 70)
    print("Passwords are hidden as you type and go straight into the OS vault.")
    print("They are never echoed, logged, or written to the repo.\n")
    if not sys.stdin.isatty():
        print("[ERROR] This needs an interactive terminal (it prompts for a hidden password).")
        print("        Open a terminal in this folder and run:  python setup_credentials.py")
        sys.exit(1)

    n = sum(1 for key, label, cu, _cp in targets if prompt_store(key, label, cu))
    print(f"\n{n} credential set(s) stored. Next: python harvest_buildings.py")


def import_from_csv():
    """Move credentials that already exist in the vendor CSV into the OS vault."""
    if not secrets_store.KEYRING_AVAILABLE:
        print("[ERROR] keyring is not installed. Run: pip install keyring")
        sys.exit(1)
    moved = 0
    for key, label, cu, cp in _portals():
        if cu and cp:
            secrets_store.store_credentials(key, cu, cp)
            print(f"  [IMPORTED] {label} -> vault (key '{key}')")
            moved += 1
    print(f"\n{moved} credential set(s) imported from the vendor CSV into the OS vault.")
    if moved:
        print("\nIMPORTANT: now clear the EMAIL/PASSWORD columns in drive_input/vendors.csv —")
        print("the vault copy is authoritative and the CSV copy is plaintext on disk.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--status" in args:
        status()
    elif "--import-csv" in args:
        import_from_csv()
    elif "--forget" in args:
        i = args.index("--forget")
        target = args[i + 1] if len(args) > i + 1 else ""
        if not target:
            print("usage: python setup_credentials.py --forget <portal_key>")
            sys.exit(1)
        secrets_store.delete_credentials(target)
        print(f"removed '{target}' from the vault")
    else:
        interactive(args[0] if args and not args[0].startswith("--") else "")
