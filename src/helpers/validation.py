"""Validation for values that the LLM extracted and a user is about to approve."""
import re
from typing import Iterable, Optional

_EMAIL_RE = re.compile(r"^[^@\s<>,;\r\n]+@[^@\s<>,;\r\n]+\.[^@\s<>,;\r\n]+$")
MAX_RECIPIENTS = 10


def has_header_injection(*values: str) -> bool:
    """True if any value contains a line break (would let a caller inject mail headers)."""
    return any(("\r" in v or "\n" in v) for v in values if isinstance(v, str))


def parse_recipients(value) -> Optional[list]:
    """Return a clean list of valid addresses, or None if any address is invalid."""
    if isinstance(value, str):
        items: Iterable = [part for part in re.split(r"[,;]", value) if part.strip()]
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return None
    cleaned = [item.strip() for item in items if isinstance(item, str) and item.strip()]
    if not cleaned or len(cleaned) > MAX_RECIPIENTS:
        return None
    if not all(_EMAIL_RE.match(address) for address in cleaned):
        return None
    return cleaned
