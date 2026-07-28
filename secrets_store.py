"""
Runtime credential vault.

Secrets are never stored in the repo, the vendor CSV, or source code. At run
time the scrapers ask this module for a credential and it resolves, in order:

  1. Windows Credential Manager / macOS Keychain / Linux Secret Service (keyring)
  2. Environment variables / .env               (CI, containers, headless servers)
  3. The vendor CSV                             (legacy fallback, warns loudly)

Nobody types a password at run time and no human or agent handles the plaintext:
the operator stores each secret once with `python setup_credentials.py`, which
reads it via a hidden prompt straight into the OS vault.

Key naming:  service = "SPB-PropertyAgent",  username = "<portal>:<field>"
e.g. ("SPB-PropertyAgent", "e_agent:username") / ("SPB-PropertyAgent", "e_agent:password")
"""

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger("spb.secrets")

SERVICE = "SPB-PropertyAgent"

try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:  # pragma: no cover
    keyring = None
    KEYRING_AVAILABLE = False


def _from_vault(portal: str, field: str) -> Optional[str]:
    if not KEYRING_AVAILABLE:
        return None
    try:
        return keyring.get_password(SERVICE, f"{portal}:{field}")
    except Exception as e:  # pragma: no cover
        logger.debug("vault read failed for %s:%s -> %s", portal, field, e)
        return None


def _from_env(portal: str, field: str) -> Optional[str]:
    # e_agent + password -> E_AGENT_PASSWORD ; portal_bathla + username -> PORTAL_BATHLA_USERNAME
    key = f"{portal}_{field}".upper().replace("-", "_")
    return os.getenv(key) or None


def get_credentials(portal: str, csv_fallback: Tuple[str, str] = ("", "")) -> Tuple[str, str, str]:
    """Return (username, password, source) for a portal key.

    source is 'vault' | 'env' | 'csv' | 'none' so callers can log provenance
    without ever logging the secret itself.
    """
    u, p = _from_vault(portal, "username"), _from_vault(portal, "password")
    if u and p:
        return u, p, "vault"

    u, p = _from_env(portal, "username"), _from_env(portal, "password")
    if u and p:
        return u, p, "env"

    u, p = csv_fallback
    if u and p:
        logger.warning(
            "[%s] using credentials from the vendor CSV (plaintext on disk). "
            "Move them into the OS vault: python setup_credentials.py %s", portal, portal
        )
        return u, p, "csv"

    return "", "", "none"


def store_credentials(portal: str, username: str, password: str) -> bool:
    """Write a credential pair into the OS vault. Used by setup_credentials.py."""
    if not KEYRING_AVAILABLE:
        raise RuntimeError("keyring is not installed. Run: pip install keyring")
    keyring.set_password(SERVICE, f"{portal}:username", username)
    keyring.set_password(SERVICE, f"{portal}:password", password)
    return True


def delete_credentials(portal: str) -> None:
    if not KEYRING_AVAILABLE:
        return
    for field in ("username", "password"):
        try:
            keyring.delete_password(SERVICE, f"{portal}:{field}")
        except Exception:
            pass


def has_credentials(portal: str) -> bool:
    u, p, _src = get_credentials(portal)
    return bool(u and p)


def describe(portal: str) -> str:
    """Human-readable status with NO secret material."""
    u, p, src = get_credentials(portal)
    if not (u and p):
        return "not configured"
    masked = (u[:2] + "***@" + u.split("@", 1)[1]) if "@" in u else (u[:2] + "***")
    return f"{src}: {masked}"
