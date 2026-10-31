"""Keep personal data out of logs.

Logs are copied to files, dashboards and chat threads, and live far longer than a request. Never log what
a user typed, the text of their email or documents, or their address. Log that something happened and
how big it was; when a line must be matched to a request, use these helpers or the request id.
"""
import hashlib
from typing import Optional


def redact_text(text: Optional[str]) -> str:
    """'<42 chars #3fa9c1>': the size and a short fingerprint, enough to tell two inputs apart, never the text."""
    if not text:
        return "<empty>"
    return f"<{len(text)} chars #{hashlib.sha256(text.encode('utf-8')).hexdigest()[:6]}>"


def mask_email(address: Optional[str]) -> str:
    """alice@example.com -> a***@example.com (the domain helps with support, the person stays private)."""
    if not address or "@" not in address:
        return "<no address>"
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"
