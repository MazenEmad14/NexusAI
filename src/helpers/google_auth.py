"""Reading and renewing a user's stored Google token, and knowing which Google services it covers.

A stored token holds one refresh token that covers every permission the user granted (Gmail, Calendar,
Drive). Each service used to rebuild the credentials with *its own* scope list, so a refresh made by one
service returned an access token limited to that service's scopes, and the next call to another service
failed with "insufficient authentication scopes". The credentials are now built without a scope list, so a
refresh keeps everything the user granted, and the granted scopes are recorded separately
(`granted_scopes`, written when the user connects) so the app can tell which services are available.
"""
import json
from typing import Any, Dict, Optional

from google.oauth2.credentials import Credentials

SCOPE_BASE = "https://www.googleapis.com/auth/"
GMAIL_SCOPES = [SCOPE_BASE + name for name in ("gmail.readonly", "gmail.send", "gmail.modify")]
CALENDAR_SCOPES = [SCOPE_BASE + name for name in ("calendar", "calendar.events")]
DRIVE_READ_SCOPE = SCOPE_BASE + "drive.readonly"   # search, open and read any of the user's files
DRIVE_FILE_SCOPE = SCOPE_BASE + "drive.file"       # create files, and open or change only the files this app made
DRIVE_SCOPES = [DRIVE_READ_SCOPE, DRIVE_FILE_SCOPE]

# Everything the app asks for when a user connects Google.
ALL_SCOPES = GMAIL_SCOPES + CALENDAR_SCOPES + DRIVE_SCOPES

_BOOKKEEPING_KEYS = ("scopes", "granted_scopes")


def credentials_from_token(token: Dict[str, Any]) -> Credentials:
    """Credentials for a stored token, without narrowing them to a scope list (see the module docstring)."""
    info = {key: value for key, value in token.items() if key not in _BOOKKEEPING_KEYS}
    return Credentials.from_authorized_user_info(info)


def refreshed_token(creds: Credentials, previous: Dict[str, Any]) -> Dict[str, Any]:
    """The token to store after `creds` was used or refreshed, keeping what the app knows about its scopes."""
    token = json.loads(creds.to_json())
    token.pop("scopes", None)
    for key in _BOOKKEEPING_KEYS:
        if previous.get(key):
            token[key] = previous[key]
    return token


def granted_scopes(token: Optional[Dict[str, Any]]) -> Optional[set]:
    """The scopes recorded for a token, or None for an older token that does not record them."""
    if not token:
        return None
    scopes = token.get("granted_scopes")
    return set(scopes) if scopes else None


def drive_access(token: Optional[Dict[str, Any]]) -> Dict[str, Optional[bool]]:
    """What Drive access the token has: {"read": ..., "write": ...}; None where an older token cannot tell."""
    scopes = granted_scopes(token)
    if scopes is None:
        return {"read": None, "write": None}
    full = SCOPE_BASE + "drive"
    return {
        "read": bool(scopes & {DRIVE_READ_SCOPE, full}),
        "write": bool(scopes & {DRIVE_FILE_SCOPE, full}),
    }


def service_status(token: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    """Which Google services this token covers, for the Integrations panel."""
    if not token:
        return {"gmail": False, "calendar": False, "drive": False}
    scopes = granted_scopes(token)
    if scopes is None:  # an older token: Gmail and Calendar were the only things ever requested
        return {"gmail": True, "calendar": True, "drive": False}
    drive = drive_access(token)
    return {
        "gmail": any(scope.startswith(SCOPE_BASE + "gmail") for scope in scopes),
        "calendar": any(scope.startswith(SCOPE_BASE + "calendar") for scope in scopes),
        "drive": bool(drive["read"] or drive["write"]),
    }
