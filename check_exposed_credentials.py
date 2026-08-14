"""
Is a password that leaked into git history still the one that opens the portal?

`Book1(Builders) List.csv` was committed on 22 Jul with a PASSWORD column and removed
from HEAD on 31 Jul. Removing a file from HEAD does not remove it from history, and the
commits holding it are on the public remote, so the values are readable by anyone who
can clone the repository. See SECURITY_CREDENTIAL_EXPOSURE.md.

"Three passwords are exposed" was the state on 22 July. It is not necessarily the state
today, and the difference decides how urgent this is and which portals actually need a
person. This answers the question that matters -- WHICH of them still work -- by
comparing the leaked value against what is in the OS vault right now.

    python check_exposed_credentials.py

NEITHER VALUE IS EVER PRINTED. The comparison is over SHA-256 digests, so the script can
say "same" or "different" without putting a password on a terminal, in a scrollback
buffer, or in a log. It reads; it changes nothing.

Exit code is 1 if any exposed password is still live, so it can gate a release check.
"""

import csv
import hashlib
import io
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import secrets_store                                                  # noqa: E402

# The commit that introduced the file, and the path inside it.
EXPOSED_AT = "e0b3a76"
EXPOSED_PATH = "Book1(Builders) List.csv"

# Builder name (lower-case substring) -> the vault key setup_credentials.py writes.
VAULT_KEY = {
    "paramount": "portal_paramount_living",
    "hermitage": "portal_hermitage_homes",
    "bathla": "portal_bathla",
}


def _digest(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def _exposed_bytes():
    """The leaked file's bytes — from git history if it is still there, else from disk.

    ORIGINALLY this only read `git show e0b3a76:...`. That stopped working on 2026-08-14
    when the dangling object was finally pruned from the local clone (the last step of the
    history scrub), and a verifier that dies the moment the scrub succeeds is a verifier
    that is useless exactly when you want to confirm a rotation. The on-disk copy
    (gitignored, still present as a live harvest input) carries the same exposed values,
    so fall back to it. The point of the check is unchanged: does a value that WAS public
    still open the portal?
    """
    out = subprocess.run(["git", "show", f"{EXPOSED_AT}:{EXPOSED_PATH}"], capture_output=True)
    if out.returncode == 0:
        return out.stdout
    on_disk = Path(__file__).resolve().parent / EXPOSED_PATH
    if on_disk.exists():
        return on_disk.read_bytes()
    raise SystemExit(
        f"[!] the exposed values are no longer readable — {EXPOSED_AT} is pruned from "
        f"history AND {EXPOSED_PATH} is not on disk. Nothing to compare against; if the "
        f"scrub and rotation are both done, that is the desired end state.")


def _exposed_rows():
    """Every row of the leaked file that carried a password."""
    # The sheet is not clean UTF-8; replace rather than fail, since only the PASSWORD
    # and BUILDER columns are read and a mangled byte elsewhere is irrelevant.
    text = _exposed_bytes().decode("utf-8", "replace")
    for row in csv.DictReader(io.StringIO(text)):
        password = (row.get("PASSWORD") or "").strip()
        if password:
            name = (row.get("BUILDER") or next(iter(row.values()), "") or "").strip()
            yield name, password


def main() -> int:
    print("=" * 68)
    print("  Exposed builder passwords — are they still live?")
    print("=" * 68)
    print(f"  leaked in {EXPOSED_AT}: {EXPOSED_PATH}")
    print("  comparing by digest; no password is printed\n")

    live = []
    for name, password in _exposed_rows():
        key = next((v for k, v in VAULT_KEY.items() if k in name.lower()), "")
        if not key:
            print(f"  {name[:28]:<30} ?  no vault key mapped for this builder")
            continue
        try:
            _user, stored, _src = secrets_store.get_credentials(key)
        except Exception as exc:                                      # noqa: BLE001
            print(f"  {name[:28]:<30} ?  vault lookup failed: {exc}")
            continue
        if not stored:
            # Absent is not rotated. Nothing here proves the portal password changed.
            print(f"  {name[:28]:<30} ?  not in the vault — cannot tell, assume live")
            live.append(name)
        elif _digest(stored) == _digest(password):
            print(f"  {name[:28]:<30} !! STILL LIVE — rotate this one")
            live.append(name)
        else:
            print(f"  {name[:28]:<30} ok already rotated")

    print()
    if live:
        print(f"  {len(live)} still live: {', '.join(live)}")
        print("  Rotate on the builder's own portal, then store the new value:")
        for name in live:
            key = next((v for k, v in VAULT_KEY.items() if k in name.lower()), "")
            if key:
                print(f"      python setup_credentials.py {key}")
        print("\n  Then see SECURITY_CREDENTIAL_EXPOSURE.md for scrubbing the history.")
        return 1
    print("  None of the exposed passwords still opens its portal.")
    print("  The history still holds them, so the scrub in")
    print("  SECURITY_CREDENTIAL_EXPOSURE.md is still worth doing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
