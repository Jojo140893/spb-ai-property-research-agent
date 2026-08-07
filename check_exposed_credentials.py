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


def _exposed_rows():
    """Every row of the leaked file that carried a password."""
    out = subprocess.run(["git", "show", f"{EXPOSED_AT}:{EXPOSED_PATH}"],
                         capture_output=True)
    if out.returncode != 0:
        raise SystemExit(f"[!] cannot read {EXPOSED_PATH} at {EXPOSED_AT} — "
                         f"is this a clone of the right repository?")
    # The sheet is not clean UTF-8; replace rather than fail, since only the PASSWORD
    # and BUILDER columns are read and a mangled byte elsewhere is irrelevant.
    text = out.stdout.decode("utf-8", "replace")
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
