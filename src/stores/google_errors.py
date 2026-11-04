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
    elif status == 403 and "insufficient" in str(exc).lower():
        # The token was granted without the permission this call needs: connecting again asks for it.
        error = GoogleAuthRequiredException(
            f"Google has not given this app access to {service} yet. Please reconnect your Google account and allow it.")
    else:
        error = ExternalServiceError(f"{service} request failed ({status or type(exc).__name__}): {exc}", service=service)
    error.__cause__ = exc
    return error
