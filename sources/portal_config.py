"""
Portal scraper configuration — selectors and navigation per portal.

A DOM change on any portal is fixed HERE, not in scraper code. Each config
carries a `verified` flag: True means the selectors were confirmed against the
live authenticated portal; False means they are best-effort defaults that must
be checked on first real run (the scraper logs a warning and the pipeline shows
the source as needing re-mapping rather than trusting partial data).

To map a new portal: log into it once, inspect the login form + a listing card,
and fill in a PortalConfig below (or add it to BUILDER_PORTAL_CONFIGS keyed by
the portal's domain).
"""

from dataclasses import dataclass, field
from typing import Dict, Optional
from urllib.parse import urlparse


@dataclass
class PortalConfig:
    name: str
    # Login
    login_url: str = ""
    open_login_selector: str = ""          # optional button that reveals the login form (modals)
    email_selector: str = "input[type=email]"
    password_selector: str = "input[type=password]"
    submit_selector: str = "button:has-text('Log In')"
    # Two-step logins (email -> Continue -> password). Left blank for single-step forms.
    continue_selector: str = ""
    login_verified: bool = False           # login field selectors confirmed against the live page
    logged_in_selector: str = ""           # element only present once authenticated
    # Listings
    listings_url: str = ""                  # primary page/route holding the stock list
    extra_listings_urls: tuple = ()         # additional stock pages (portals split inventory)
    listing_card_selector: str = ""         # repeated element = one package
    field_selectors: Dict[str, str] = field(default_factory=dict)  # package field -> selector within a card
    link_selector: str = "a"                # link to the full listing within a card
    verified: bool = False                  # True once confirmed against live DOM


# --- E-Agent (Wix members site; confirmed structure 2026-07-23) -------------
# Login form uses generic input[type=email]/input[type=password]; the Wix element
# ids are auto-generated and unstable, so we target by type + button text.
# The listing card selector is best-effort and MUST be confirmed on first
# authenticated run (verified=False).
EAGENT_CONFIG = PortalConfig(
    name="E-Agent",
    login_url="https://www.e-agent.com.au/access-projects",
    open_login_selector="button:has-text('Log In'), button:has-text('Already a member')",
    email_selector="input[type=email]",
    password_selector="input[type=password]",
    submit_selector="button:has-text('Log In')",
    logged_in_selector="text=Log Out, text=My Account",
    listings_url="https://www.e-agent.com.au/access-projects",
    listing_card_selector="[data-hook='listing-card'], .listing-card, li[role='listitem']",
    field_selectors={
        "title": "h2, h3, [data-hook='title']",
        "price": "[data-hook='price'], .price",
        "suburb": "[data-hook='suburb'], .suburb, .location",
        "beds": "[data-hook='beds'], .beds",
        "baths": "[data-hook='baths'], .baths",
        "cars": "[data-hook='cars'], .cars",
    },
    link_selector="a",
    verified=False,
)


# --- Direct builder portals, keyed by domain --------------------------------
# All best-effort until confirmed on the live authenticated portal.
BUILDER_PORTAL_CONFIGS: Dict[str, PortalConfig] = {
    "portal.hermitagehomes.com.au": PortalConfig(
        name="Hermitage Homes",
        login_url="https://portal.hermitagehomes.com.au/login",
        # confirmed live 2026-07-27: plain username/password form (NOT type=email)
        email_selector="input[name='username']",
        password_selector="input[name='password']",
        submit_selector="button:has-text('LOG IN'), button:has-text('Log In')",
        login_verified=True,
        logged_in_selector="text=Log Out, text=Logout, text=Dashboard",
        listings_url="https://portal.hermitagehomes.com.au/allpackages",  # discovered live: 171 listings
        listing_card_selector=".property-card, .listing, tr.stock-row",
        field_selectors={"title": ".title, td.address", "price": ".price, td.price"},
        verified=False,
    ),
    "portal.bathla.com.au": PortalConfig(
        name="Bathla",
        login_url="https://portal.bathla.com.au/login",
        # confirmed live 2026-07-27: two-step - email, Continue, then password
        email_selector="input[name='email'], input[type=email]",
        continue_selector="button:has-text('Continue')",
        password_selector="input[type=password]",
        submit_selector="button:has-text('Continue'), button:has-text('Sign in'), button[type=submit]",
        login_verified=True,
        logged_in_selector="text=Logout, text=Log Out, text=Dashboard",
        listings_url="https://portal.bathla.com.au/display",  # discovered live
        extra_listings_urls=("https://portal.bathla.com.au/granny",
                             "https://portal.bathla.com.au/promotions"),
        listing_card_selector=".property-card, .stock-item, tr.stock-row",
        field_selectors={"title": ".title, td.address", "price": ".price, td.price"},
        verified=False,
    ),
    "partners.frdhomes.com.au": PortalConfig(
        name="FRD Homes",
        login_url="https://partners.frdhomes.com.au/login",
        # confirmed live 2026-07-27: two-step - #email then 'Sign in with email'
        email_selector="#email, input[name='user[email]']",
        continue_selector="button:has-text('Sign in with email')",
        password_selector="input[type=password]",
        submit_selector="button[type=submit], button:has-text('Sign in')",
        login_verified=True,
        logged_in_selector="text=Logout, text=Log Out, text=Dashboard",
        listings_url="https://partners.frdhomes.com.au/",
        listing_card_selector=".property-card, .stock-item",
        field_selectors={"title": ".title", "price": ".price"},
        verified=False,
    ),
    "paramountliving.com.au": PortalConfig(
        name="Paramount Living",
        login_url="https://paramountliving.com.au/?auth=1&redirect=https%3A%2F%2Fparamountliving.com.au%2Fagent-portal%2F",
        # confirmed live 2026-07-27: Gravity-Forms style #input_1 (username, NOT email) + #input_2
        email_selector="#input_1",
        password_selector="#input_2",
        submit_selector="button:has-text('Login'), input[value='Login'], button[type=submit]",
        login_verified=True,
        logged_in_selector="text=Log Out, text=Logout",  # NOT "Agent Portal" — public nav item
        # Login CONFIRMED working (redirects to /agent-portal/ with LOGOUT markers).
        # Stock lives at /agent-stocklist/ behind a JS filter UI whose result cards
        # (.component--card_lot_port) render inconsistently headless — may need a
        # headed run or the filter form submitted. Deferred.
        listings_url="https://paramountliving.com.au/agent-stocklist/",
        listing_card_selector=".component--card_lot_port, .card__content, .property-card, article",
        field_selectors={"title": ".title, h2, h3", "price": ".price"},
        verified=False,
    ),
    "proxima.com.au": PortalConfig(
        name="Proxima",
        login_url="https://www.proxima.com.au/",
        logged_in_selector="text=Log Out, text=Logout, text=Dashboard",
        listings_url="https://www.proxima.com.au/",
        listing_card_selector=".property-card, .listing, .stock-item, article",
        field_selectors={"title": ".title, h2, h3", "price": ".price"},
        verified=False,
    ),
}


def normalize_url(u: str) -> str:
    """CSV links are often scheme-less ('referrer.torsionhomes.au/...', 'www.proxima.com.au').
    Playwright rejects those, so always return an absolute https URL."""
    u = (u or "").strip()
    if not u:
        return ""
    return u if "://" in u else "https://" + u.lstrip("/")


def config_for_url(portal_url: str) -> Optional[PortalConfig]:
    """Match a builder's portal_url to a known PortalConfig by host, else a generic default."""
    if not portal_url:
        return None
    portal_url = normalize_url(portal_url)
    host = urlparse(portal_url).netloc.lower()
    for domain, cfg in BUILDER_PORTAL_CONFIGS.items():
        if domain in host:
            return cfg
    # Generic fallback: try common patterns, flagged unverified.
    return PortalConfig(
        name=host or "Unknown Portal",
        login_url=portal_url,
        logged_in_selector="text=Logout, text=Log Out, text=Dashboard",
        listings_url=portal_url,
        listing_card_selector=".property-card, .listing, .stock-item, tr",
        field_selectors={"title": ".title, .address, h3", "price": ".price"},
        verified=False,
    )
