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


_COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789", "1234567890", "qwerty123",
    "qwertyuiop", "iloveyou", "11111111", "00000000", "abc12345", "letmein123", "welcome123",
    "admin123", "administrator", "passw0rd", "p@ssw0rd", "1q2w3e4r", "zaq12wsx",
}


def password_problem(password: str, email: str = "", name: str = "") -> Optional[str]:
    """A message describing why the password is unacceptable, or None if it is fine."""
    if len(password) < 8:
        return "Password must be at least 8 characters long"
    if len(password.encode("utf-8")) > 72:
        return "Password must be at most 72 bytes long"  # bcrypt ignores or rejects anything longer
    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS or len(set(lowered)) == 1:
        return "That password is too common. Choose a less predictable one"
    local_part = (email or "").split("@")[0].lower()
    if (len(local_part) >= 4 and local_part in lowered) or lowered == (name or "").strip().lower():
        return "Password must not contain your email or name"
    return None
