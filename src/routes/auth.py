from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from pathlib import Path
import json
import logging
import time

from helpers.auth_utils import get_current_user
from helpers.config import get_settings
from models.UserModel import UserModel

logger = logging.getLogger("uvicorn.error")

auth_router = APIRouter(
    prefix="/api/v1/auth",
    tags=["api_v1", "auth"]
)

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/calendar.events'
]

CREDENTIALS_PATH = Path(__file__).resolve().parent.parent / "credentials.json"
ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
LOGIN_TTL_SECONDS = 300

# In-flight OAuth logins keyed by the `state` value. Each entry owns its own Flow
# (and therefore its own PKCE code_verifier), so concurrent logins never mix.
_pending_logins: dict[str, dict] = {}


def _cleanup_pending_logins():
    now = time.time()
    for key in [k for k, v in _pending_logins.items() if v["expires"] < now]:
        del _pending_logins[key]


def _new_flow() -> Flow:
    flow = Flow.from_client_secrets_file(str(CREDENTIALS_PATH), scopes=SCOPES)
    flow.redirect_uri = get_settings().GOOGLE_REDIRECT_URI
    return flow


@auth_router.get("/google/login")
async def google_login(origin: str = None, current_user: dict = Depends(get_current_user)):
    """
    Start the Google OAuth flow for the logged-in user.
    Returns the consent URL; the callback links the resulting token to this user.
    """
    if not CREDENTIALS_PATH.exists():
        raise HTTPException(status_code=404, detail="credentials.json not found on server")

    try:
        flow = _new_flow()
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )
    except Exception:
        logger.error("Error initializing Google OAuth flow", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to start Google authentication")

    _cleanup_pending_logins()
    _pending_logins[state] = {
        "flow": flow,
        "user_email": current_user["email"],
        "origin": origin if origin in ALLOWED_ORIGINS else None,
        "expires": time.time() + LOGIN_TTL_SECONDS,
    }
    return JSONResponse(content={"auth_url": authorization_url})


@auth_router.get("/google/callback")
async def google_callback(request: Request, code: str, state: str):
    # `state` must match a login we started and that has not expired; otherwise the
    # request is forged or stale and no token is stored anywhere.
    _cleanup_pending_logins()
    pending = _pending_logins.pop(state, None)
    if pending is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state. Please try connecting again.")

    try:
        flow = pending["flow"]
        flow.fetch_token(code=code)
        token_dict = json.loads(flow.credentials.to_json())
    except Exception:
        logger.error("Error exchanging Google OAuth code", exc_info=True)
        raise HTTPException(status_code=400, detail="Failed to complete Google authentication")

    user_model = await UserModel.create_instance(request.app.mongo_db)
    saved = await user_model.update_google_token(pending["user_email"], token_dict)
    if not saved:
        logger.error(f"Failed to save Google token for {pending['user_email']}")
        raise HTTPException(status_code=500, detail="Failed to save Google token")
    logger.info(f"Saved per-user Google token for {pending['user_email']}")

    frontend = pending["origin"] or get_settings().FRONTEND_URL
    return RedirectResponse(url=f"{frontend}/workspace")


@auth_router.get("/google/status")
async def google_status(request: Request, current_user: dict = Depends(get_current_user)):
    """Return whether the logged-in user has a usable Google connection."""
    from google.oauth2.credentials import Credentials

    user_model = await UserModel.create_instance(request.app.mongo_db)
    token_dict = await user_model.get_google_token(current_user["email"])
    if not token_dict:
        return JSONResponse(content={"authenticated": False})
    try:
        creds = Credentials.from_authorized_user_info(token_dict, SCOPES)
        return JSONResponse(content={"authenticated": bool(creds.valid or creds.refresh_token)})
    except Exception:
        logger.error("Failed to parse stored Google token in google_status", exc_info=True)
        return JSONResponse(content={"authenticated": False})


@auth_router.post("/google/disconnect")
async def google_disconnect(request: Request, current_user: dict = Depends(get_current_user)):
    """Forget the logged-in user's Google token."""
    user_model = await UserModel.create_instance(request.app.mongo_db)
    await user_model.update_google_token(current_user["email"], None)
    return JSONResponse(content={"message": "Google account disconnected"})
