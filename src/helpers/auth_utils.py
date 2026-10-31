import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional
from helpers.config import get_settings
from helpers.services import services_of

SECRET_KEY = get_settings().JWT_SECRET_KEY
ALGORITHM = "HS256"


def normalize_email(email: str) -> str:
    """Emails are matched case-insensitively: store and look up the lower-cased form."""
    return (email or "").strip().lower()


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """False (never an exception) for a wrong password, an over-long one or a malformed hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# Verified against when the account does not exist, so a sign-in takes the same time either way
# and cannot be used to discover which emails are registered.
DUMMY_PASSWORD_HASH = get_password_hash("not-a-real-password")

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=get_settings().ACCESS_TOKEN_MINUTES)
    to_encode.update({"exp": int(expire.timestamp())})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str):
    try:
        decoded_data = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return decoded_data
    except jwt.PyJWTError:
        return None

import hmac
import logging

from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from helpers.logging_setup import user_id_var
from helpers.session_cookies import ACCESS_COOKIE, CSRF_COOKIE, CSRF_HEADER
from models.UserModel import UserModel

# auto_error=False: a missing Authorization header is not an error by itself, the session cookie may be there.
security = HTTPBearer(auto_error=False)

logger = logging.getLogger('uvicorn.error')

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def enforce_csrf(request: Request) -> None:
    """Protect cookie-authenticated requests that change something.

    A browser sends cookies on a request that another website triggered, so for those the request must
    also prove it came from our own page: it must echo the csrf cookie in a header (another site can
    neither read the cookie nor set the header), and its Origin, if sent, must be one of ours.
    """
    origin = request.headers.get("origin")
    if origin and origin not in get_settings().CORS_ORIGINS and origin != str(request.base_url).rstrip("/"):
        logger.warning("Rejected a cookie-authenticated request from an unknown origin")
        raise HTTPException(status_code=403, detail="Origin not allowed")

    cookie, header = request.cookies.get(CSRF_COOKIE, ""), request.headers.get(CSRF_HEADER, "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        logger.warning("Rejected a cookie-authenticated request without a valid CSRF token")
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


async def get_current_user(request: Request, credentials: HTTPAuthorizationCredentials = Security(security)):
    """The signed-in user. A Bearer token (API clients) wins; otherwise the httpOnly session cookie (browsers)."""
    token = credentials.credentials if credentials else None
    from_cookie = False
    if not token:
        token = request.cookies.get(ACCESS_COOKIE)
        from_cookie = bool(token)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    decoded = decode_access_token(token)
    if not decoded or "sub" not in decoded:
        logger.warning("get_current_user 401 - invalid, malformed or expired token")
        raise HTTPException(status_code=401, detail="Invalid token")

    if from_cookie and request.method not in SAFE_METHODS:
        enforce_csrf(request)

    email = normalize_email(decoded["sub"])
    user_model = await UserModel.create_instance(services_of(request).mongo_db)
    user_data = await user_model.get_user_by_email(email)

    if not user_data:
        logger.warning("get_current_user 401 - user for a valid token no longer exists")
        raise HTTPException(status_code=401, detail="User not found")

    user_id_var.set(str(user_data["_id"]))  # on every log line written for the rest of this request

    return user_data
