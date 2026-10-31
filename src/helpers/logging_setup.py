"""Request ids, structured (JSON) logs and redaction.

* Every request gets an id (from a valid X-Request-ID header, else a new one). It is returned in the
  X-Request-ID response header, put on every log line written while the request runs, and included in error
  responses, so "it failed around 14:03" becomes "request 3f9a1c2b7d4e5a60".
* LOG_FORMAT=json writes one JSON object per line (for log shippers); the default is readable text.
* Secrets in URLs (OAuth codes, one-time tickets) are redacted from the access log.
"""
import json
import logging
import re
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")
_SECRET_PARAMS = re.compile(r"(?i)\b(ticket|code|state|token|access_token|refresh_token|key|api_key|password)=[^&\s\"']+")

_TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s [req=%(request_id)s user=%(user_id)s] %(message)s"


def redact_secrets(text: str) -> str:
    return _SECRET_PARAMS.sub(lambda m: f"{m.group(1)}=[redacted]", text)


class ContextFilter(logging.Filter):
    """Adds the current request id and user id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        record.user_id = user_id_var.get() or "-"
        return True


class AccessLogRedactor(logging.Filter):
    """uvicorn's access line contains the full URL; strip secrets from its query string."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact_secrets(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(log_format: str = "text") -> None:
    """Idempotent: safe to call at every startup (and in tests)."""
    formatter = JsonFormatter() if log_format == "json" else logging.Formatter(_TEXT_FORMAT)

    # uvicorn owns handlers on its "uvicorn" logger; our modules log through "uvicorn.error", a child of it.
    for name in ("uvicorn", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            _install(handler, formatter, access=(name == "uvicorn.access"))

    # Modules that use logging.getLogger(__name__) propagate to the root logger, which has no handler
    # unless one is added; give it one with the same format so those lines are not lost or unformatted.
    root = logging.getLogger()
    if not any(getattr(h, "_app_handler", False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler._app_handler = True  # type: ignore[attr-defined]
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    for handler in root.handlers:
        if getattr(handler, "_app_handler", False):
            _install(handler, formatter)


def _install(handler: logging.Handler, formatter: logging.Formatter, access: bool = False) -> None:
    handler.setFormatter(formatter)
    for existing in list(handler.filters):
        if isinstance(existing, (ContextFilter, AccessLogRedactor)):
            handler.removeFilter(existing)
    handler.addFilter(ContextFilter())
    if access:
        handler.addFilter(AccessLogRedactor())


class RequestContextMiddleware:
    """Pure ASGI middleware (no buffering, so streaming responses are untouched)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex[:16]
        id_token, user_token = request_id_var.set(request_id), user_id_var.set("")

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                headers = [h for h in message.get("headers", []) if h[0].lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(id_token)
            user_id_var.reset(user_token)
