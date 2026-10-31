import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from pymongo.errors import PyMongoError

from helpers import auth_utils
from helpers.services import services_of
from helpers.config import get_settings
from helpers.timezones import is_valid_timezone
from helpers.validation import password_problem
from models.RateLimitModel import RateLimitModel
from models.UserModel import UserModel
from models.db_schemes.user import User

logger = logging.getLogger("uvicorn.error")

local_auth_router = APIRouter(
    prefix="/api/v1/auth/local",
    tags=["api_v1", "auth", "local"]
)

# One message for every sign-in failure, so the response never reveals whether the email exists.
INVALID_CREDENTIALS = "Invalid email or password"


class SignUpRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str


class SignInRequest(BaseModel):
    email: EmailStr
    password: str


class TimezoneRequest(BaseModel):
    timezone: str = Field(max_length=64)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _too_many(retry_at: datetime | None, action: str) -> HTTPException:
    seconds = max(1, int((retry_at - datetime.utcnow()).total_seconds())) if retry_at else 60
    minutes = (seconds + 59) // 60
    return HTTPException(
        status_code=429,
        detail=f"Too many {action} attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
        headers={"Retry-After": str(seconds)},
    )


async def _guard_signup(limits: RateLimitModel, request: Request) -> None:
    """Every sign-up attempt counts, successful or not, so the endpoint cannot be used to probe or flood."""
    settings = get_settings()
    count = await limits.hit(f"signup:{_client_ip(request)}", window_seconds=3600)
    if count > settings.SIGNUP_MAX_PER_IP_PER_HOUR:
        _, ends = await limits.status(f"signup:{_client_ip(request)}")
        raise _too_many(ends, "sign-up")


@local_auth_router.post("/signup")
async def signup(request: Request, body: SignUpRequest):
    limits = await RateLimitModel.create_instance(services_of(request).mongo_db)
    await _guard_signup(limits, request)

    email = auth_utils.normalize_email(str(body.email))
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    problem = password_problem(body.password, email=email, name=name)
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    user_model = await UserModel.create_instance(services_of(request).mongo_db)
    if await user_model.get_user_by_email(email):
        raise HTTPException(status_code=409, detail="Email already exists")

    # bcrypt is deliberately slow: run it off the event loop so other requests keep being served.
    hashed_password = await asyncio.to_thread(auth_utils.get_password_hash, body.password)

    new_user = User(
        name=name,
        email=email,
        password_hash=hashed_password,
        is_google_connected=False
    )

    # Raises EmailAlreadyRegistered (409), also when two sign-ups with the same email race.
    await user_model.create_user(new_user)

    token = auth_utils.create_access_token({"sub": email})

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "name": name,
            "email": email,
            "is_google_connected": False
        }
    }


@local_auth_router.post("/signin")
async def signin(request: Request, body: SignInRequest):
    settings = get_settings()
    window = settings.LOGIN_WINDOW_MINUTES * 60
    email = auth_utils.normalize_email(str(body.email))
    ip = _client_ip(request)
    ip_key, account_key = f"login-ip:{ip}", f"login:{email}:{ip}"

    limits = await RateLimitModel.create_instance(services_of(request).mongo_db)

    # Refuse before doing any work once a limit is reached. The account counter is per client
    # address, so a stranger cannot lock the real owner out of their own account.
    for key, limit in ((account_key, settings.LOGIN_MAX_FAILURES_PER_ACCOUNT), (ip_key, settings.LOGIN_MAX_FAILURES_PER_IP)):
        count, ends = await limits.status(key)
        if count >= limit:
            raise _too_many(ends, "sign-in")

    user_model = await UserModel.create_instance(services_of(request).mongo_db)
    user_data = await user_model.get_user_by_email(email)

    # Always run one bcrypt check, against a dummy hash if the account does not exist, so the
    # response time does not reveal whether the email is registered.
    stored_hash = (user_data or {}).get("password_hash") or auth_utils.DUMMY_PASSWORD_HASH
    password_ok = await asyncio.to_thread(auth_utils.verify_password, body.password, stored_hash)

    if not user_data or not password_ok:
        await limits.hit(account_key, window)
        await limits.hit(ip_key, window)
        raise HTTPException(status_code=401, detail=INVALID_CREDENTIALS)

    await limits.reset(account_key)

    # Read per-user Google connection status from the user's own stored token, not a shared file.
    is_google_connected = bool(user_data.get("google_token"))

    # Sync the is_google_connected flag if it drifted
    if user_data.get("is_google_connected") != is_google_connected:
        try:
            await user_model.update_google_auth_status(email, is_google_connected)
        except PyMongoError:
            logger.warning("Could not sync the Google connection flag at sign-in", exc_info=True)  # cosmetic

    token = auth_utils.create_access_token({"sub": email})

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "name": user_data.get("name"),
            "email": email,
            "is_google_connected": is_google_connected
        }
    }


@local_auth_router.put("/timezone")
async def set_timezone(request: Request, body: TimezoneRequest, current_user: dict = Depends(auth_utils.get_current_user)):
    """Save the user's IANA time zone (the browser's zone), so "today" and "tomorrow" are theirs."""
    if not is_valid_timezone(body.timezone):
        raise HTTPException(status_code=400, detail="Unknown time zone")
    user_model = await UserModel.create_instance(services_of(request).mongo_db)
    await user_model.set_timezone(current_user["email"], body.timezone)
    return {"timezone": body.timezone}
