"""Optional encryption-at-rest for stored Google OAuth tokens.

Set TOKEN_ENCRYPTION_KEY (a Fernet key) to enable it. Without a key tokens are
stored as before, and reads accept both encrypted and legacy plaintext values.
"""
import json
import logging
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

from helpers.config import get_settings

logger = logging.getLogger("uvicorn.error")

_ENC_FIELD = "enc"


def _fernet() -> Optional[Fernet]:
    key = get_settings().TOKEN_ENCRYPTION_KEY
    return Fernet(key.encode()) if key else None


def encrypt_token(token: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if token is None:
        return None
    f = _fernet()
    if f is None:
        return token
    return {_ENC_FIELD: f.encrypt(json.dumps(token).encode()).decode()}


def decrypt_token(stored: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not stored:
        return None
    if _ENC_FIELD not in stored:
        return stored  # legacy plaintext value
    f = _fernet()
    if f is None:
        logger.error("Encrypted Google token found but TOKEN_ENCRYPTION_KEY is not set")
        return None
    try:
        return json.loads(f.decrypt(stored[_ENC_FIELD].encode()))
    except InvalidToken:
        logger.error("Could not decrypt stored Google token (wrong key?)")
        return None
