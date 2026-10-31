"""The cookies that carry a browser session.

    access_token   short-lived JWT              httpOnly   Path=/
    refresh_token  opaque single-use token      httpOnly   Path=/api/v1/auth/local (sent to the auth routes only)
    csrf_token     random value                readable   Path=/   (double-submit check; also returned in JSON)

JavaScript can never read the first two, so a cross-site-scripting bug cannot steal the session, which is the
point of moving it out of localStorage.
"""
import secrets

from fastapi import Response

from helpers.config import get_settings

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
REFRESH_COOKIE_PATH = "/api/v1/auth/local"


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _flags() -> dict:
    settings = get_settings()
    return {"secure": settings.COOKIE_SECURE, "samesite": settings.COOKIE_SAMESITE, "domain": settings.COOKIE_DOMAIN}


def set_auth_cookies(response: Response, access_token: str, refresh_token: str, csrf_token: str) -> None:
    settings = get_settings()
    refresh_seconds = settings.REFRESH_TOKEN_DAYS * 86400
    response.set_cookie(ACCESS_COOKIE, access_token, max_age=settings.ACCESS_TOKEN_MINUTES * 60,
                        httponly=True, path="/", **_flags())
    response.set_cookie(REFRESH_COOKIE, refresh_token, max_age=refresh_seconds,
                        httponly=True, path=REFRESH_COOKIE_PATH, **_flags())
    response.set_cookie(CSRF_COOKIE, csrf_token, max_age=refresh_seconds, httponly=False, path="/", **_flags())


def clear_auth_cookies(response: Response) -> None:
    flags = _flags()
    response.delete_cookie(ACCESS_COOKIE, path="/", **flags)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, **flags)
    response.delete_cookie(CSRF_COOKIE, path="/", **flags)
