from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from pathlib import Path
import json
import logging
import time

from helpers.auth_utils import get_current_user
from helpers.services import services_of
from helpers.config import get_settings
from helpers.google_auth import ALL_SCOPES, SIGN_IN_SCOPES, credentials_from_token, service_status
from helpers.exceptions import EmailAlreadyRegistered
from helpers.privacy import mask_email
from models.RateLimitModel import RateLimitModel
from models.UserModel import UserModel
from routes.local_auth import start_session

logger = logging.getLogger("uvicorn.error")

auth_router = APIRouter(
    prefix="/api/v1/auth",
    tags=["api_v1", "auth"]
)

SCOPES = ALL_SCOPES  # Gmail, Calendar and Drive (see helpers/google_auth.py)

CREDENTIALS_PATH = Path(__file__).resolve().parent.parent / "credentials.json"
LOGIN_TTL_SECONDS = 300
GOOGLE_SIGNIN_MAX_PER_MINUTE = 20

# In-flight OAuth logins keyed by the `state` value. Each entry owns its own Flow
# (and therefore its own PKCE code_verifier), so concurrent logins never mix.
_pending_logins: dict[str, dict] = {}


def _cleanup_pending_logins():
    now = time.time()
    for key in [k for k, v in _pending_logins.items() if v["expires"] < now]:
        del _pending_logins[key]


def _new_flow(scopes=None) -> Flow:
    flow = Flow.from_client_secrets_file(str(CREDENTIALS_PATH), scopes=scopes or SCOPES)
    flow.redirect_uri = get_settings().GOOGLE_REDIRECT_URI
    return flow


@auth_router.get("/google/login")
async def google_login(origin: str = None, current_user: dict = Depends(get_current_user)):
    """
    Start the Google OAuth flow for the logged-in user.
    Returns the consent URL; the callback links the resulting token to this user.
    """
    return JSONResponse(content={"auth_url": _start_flow("connect", SCOPES, origin, current_user["email"])})


def _start_flow(purpose: str, scopes, origin: str = None, user_email: str = None) -> str:
    """Create the Google consent URL and remember the login by its `state` value."""
    if not CREDENTIALS_PATH.exists():
        raise HTTPException(status_code=404, detail="credentials.json not found on server")
    try:
        flow = _new_flow(scopes)
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            # No include_granted_scopes: it makes Google add scopes the account granted earlier, and the token
            # exchange then fails with "Scope has changed". Every scope we need is requested here.
            prompt='consent'
        )
    except Exception:
        logger.error("Error initializing Google OAuth flow", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to start Google authentication")

    _cleanup_pending_logins()
    _pending_logins[state] = {
        "flow": flow,
        "purpose": purpose,
        "user_email": user_email,
        "origin": origin if origin in get_settings().CORS_ORIGINS else None,
        "expires": time.time() + LOGIN_TTL_SECONDS,
    }
    return authorization_url


@auth_router.get("/google/signin")
async def google_signin(request: Request, origin: str = None):
    """Start "Continue with Google" for a visitor who is not signed in (public).

    Returns the consent URL. The callback signs the person in, creating the account the first time."""
    limits = await RateLimitModel.create_instance(services_of(request).mongo_db)
    key = f"google_signin:{request.client.host if request.client else 'unknown'}"
    if await limits.hit(key, window_seconds=60) > GOOGLE_SIGNIN_MAX_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait a minute and try again.")
    return JSONResponse(content={"auth_url": _start_flow("signin", SIGN_IN_SCOPES, origin)})


def _back_to_login(pending: dict, reason: str) -> RedirectResponse:
    """Send the browser back to the sign-in page with a reason it can explain (never a raw error page)."""
    frontend = (pending or {}).get("origin") or get_settings().FRONTEND_URL
    return RedirectResponse(url=f"{frontend}/auth?google_error={reason}")


async def _sign_in_with_google(request: Request, pending: dict, flow, token_dict: dict) -> RedirectResponse:
    """Finish "Continue with Google": find or create the account for the verified Google identity and start a session."""
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2 import id_token as google_id_token

    try:
        claims = google_id_token.verify_oauth2_token(
            flow.credentials.id_token, GoogleRequest(), flow.client_config["client_id"])
    except Exception:
        logger.error("Could not verify the Google identity token", exc_info=True)
        return _back_to_login(pending, "failed")

    email, subject = (claims.get("email") or "").strip().lower(), claims.get("sub")
    if not email or not subject or not claims.get("email_verified"):
        return _back_to_login(pending, "unverified")

    db = services_of(request).mongo_db
    users = await UserModel.create_instance(db)
    user = await users.get_user_by_email(email)
    if user is None:
        name = (claims.get("name") or email.split("@")[0]).strip()[:80] or "User"
        try:
            user = await users.create_google_user(name, email, subject)
        except EmailAlreadyRegistered:  # two sign-ins racing: the other one created it
            user = await users.get_user_by_email(email)
        logger.info(f"Created an account through Google sign-in for {mask_email(email)}")
    elif user.get("google_sub") != subject:
        # An account with this address already exists and was not made with Google. Signing in here would let whoever
        # registered the address first (with a password) share the account, so the owner signs in with the password
        # and connects Google from Settings instead.
        logger.warning(f"Google sign-in refused for {mask_email(email)}: the address has a password account")
        return _back_to_login(pending, "exists")

    if not await users.update_google_token(email, token_dict):
        logger.error(f"Failed to save Google token for {mask_email(email)}")
        return _back_to_login(pending, "failed")

    response = RedirectResponse(url=f"{pending.get('origin') or get_settings().FRONTEND_URL}/workspace")
    await start_session(response, db, str(user["_id"]), email)
    return response


@auth_router.get("/google/callback")
async def google_callback(request: Request, state: str, code: str = None, error: str = None):
    # `state` must match a login we started and that has not expired; otherwise the
    # request is forged or stale and no token is stored anywhere.
    _cleanup_pending_logins()
    pending = _pending_logins.pop(state, None)
    if pending is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state. Please try connecting again.")

    if error or not code:  # the person pressed Cancel on Google's page
        if pending["purpose"] == "signin":
            return _back_to_login(pending, "denied")
        return RedirectResponse(url=f"{pending['origin'] or get_settings().FRONTEND_URL}/workspace")

    try:
        flow = pending["flow"]
        flow.fetch_token(code=code)
        token_dict = json.loads(flow.credentials.to_json())
        token_dict["granted_scopes"] = sorted(flow.credentials.scopes or [])  # what the user actually allowed
        token_dict.pop("id_token", None)
    except Exception:
        logger.error("Error exchanging Google OAuth code", exc_info=True)
        if pending["purpose"] == "signin":
            return _back_to_login(pending, "failed")
        raise HTTPException(status_code=400, detail="Failed to complete Google authentication")

    if pending["purpose"] == "signin":
        return await _sign_in_with_google(request, pending, flow, token_dict)

    user_model = await UserModel.create_instance(services_of(request).mongo_db)
    saved = await user_model.update_google_token(pending["user_email"], token_dict)
    if not saved:
        logger.error(f"Failed to save Google token for {mask_email(pending['user_email'])}")
        raise HTTPException(status_code=500, detail="Failed to save Google token")
    logger.info(f"Saved per-user Google token for {mask_email(pending['user_email'])}")

    frontend = pending["origin"] or get_settings().FRONTEND_URL
    return RedirectResponse(url=f"{frontend}/workspace")


@auth_router.get("/google/status")
async def google_status(request: Request, current_user: dict = Depends(get_current_user)):
    """Return whether the logged-in user has a usable Google connection."""
    user_model = await UserModel.create_instance(services_of(request).mongo_db)
    token_dict = await user_model.get_google_token(current_user["email"])
    if not token_dict:
        return JSONResponse(content={"authenticated": False, "services": service_status(None)})
    try:
        creds = credentials_from_token(token_dict)
        return JSONResponse(content={
            "authenticated": bool(creds.valid or creds.refresh_token),
            "services": service_status(token_dict),  # which of Gmail, Calendar and Drive the user allowed
        })
    except Exception:
        logger.error("Failed to parse stored Google token in google_status", exc_info=True)
        return JSONResponse(content={"authenticated": False})


@auth_router.post("/google/disconnect")
async def google_disconnect(request: Request, current_user: dict = Depends(get_current_user)):
    """Forget the logged-in user's Google token."""
    user_model = await UserModel.create_instance(services_of(request).mongo_db)
    await user_model.update_google_token(current_user["email"], None)
    return JSONResponse(content={"message": "Google account disconnected"})
