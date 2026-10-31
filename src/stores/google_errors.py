"""Turn errors from the Google client libraries into the application's own exceptions."""
from google.auth.exceptions import RefreshError

from helpers.exceptions import DomainError, ExternalServiceError, GoogleAuthRequiredException


def translate_google_error(exc: Exception, service: str) -> DomainError:
    """A rejected or revoked token means the user must reconnect; anything else is Google being unavailable."""
    if isinstance(exc, DomainError):
        return exc
    status = getattr(getattr(exc, "resp", None), "status", None)  # googleapiclient HttpError
    if isinstance(exc, RefreshError) or status == 401:
        error = GoogleAuthRequiredException()
    else:
        error = ExternalServiceError(f"{service} request failed ({status or type(exc).__name__}): {exc}", service=service)
    error.__cause__ = exc
    return error
