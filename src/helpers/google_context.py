"""Request-scoped holder for the caller's Google token.

The token is deliberately NOT part of the LangGraph state: graph state is
checkpointed to the database, and a refresh token must not be stored there.
Routes call `set_google_token(...)` before invoking the graph; nodes read it with
`get_google_token()` and report a refreshed token with `update_google_token()`.
Routes then persist the refreshed token (encrypted) through UserModel.
"""
from contextvars import ContextVar
from typing import Any, Dict, Optional

_holder: ContextVar[Optional[Dict[str, Any]]] = ContextVar("google_token_holder", default=None)


def set_google_token(token: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Bind a token for the current request; returns the holder so callers can read updates."""
    holder = {"token": token}
    _holder.set(holder)
    return holder


def get_google_token() -> Optional[Dict[str, Any]]:
    holder = _holder.get()
    return holder["token"] if holder else None


def update_google_token(token: Dict[str, Any]) -> None:
    holder = _holder.get()
    if holder is not None:
        holder["token"] = token
