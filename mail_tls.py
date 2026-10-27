"""The one TLS context a mail login is sent over: the certificate AND the host name checked.

Python's stdlib mail clients (imaplib, smtplib) check neither unless they are handed a
context (25 Oct 2026, PR #42). ssl.create_default_context() checks both -- but on Windows
it trusts only the roots Python can list from the Windows certificate store, and that
store fills on demand: Windows fetches a root the first time something asks it to build a
chain to that root, and Python's listing never asks. On the laptop runner (27 Oct 2026)
the store held 31 roots and no Google Trust Services root at all. imap.gmail.com and
smtp.gmail.com verified only because Google still sends a cross-signature from
GlobalSign Root CA, which runs out on 28 Jan 2028 and which Google may stop sending
before then. From that day every nightly on the laptop would read the inbox as
CERTIFICATE_VERIFY_FAILED, and the alert about it would fail the same way.

So Mozilla's root list (certifi, installed with requests) is loaded on top of the
system's. That only adds roots; it relaxes nothing -- a certificate and the right name
are still required.
"""
import ssl

import certifi


def verified_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cafile=certifi.where())
    return ctx
