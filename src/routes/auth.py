from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from pathlib import Path
import json
import logging

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
TOKEN_PATH = Path(__file__).resolve().parent.parent / "token.json"
REDIRECT_URI = "http://localhost:8000/api/v1/auth/google/callback"
FRONTEND_REDIRECT_URL = "http://localhost:5173/workspace"

# Store the flow instance globally to preserve PKCE code_verifier
_auth_flow_instance = None

import time

_state_origin_map = {}
ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

def _cleanup_state_map():
    now = time.time()
    expired = [k for k, v in _state_origin_map.items() if v["expires"] < now]
    for k in expired:
        del _state_origin_map[k]

@auth_router.get("/google/login")
async def google_login(origin: str = None):
    global _auth_flow_instance
    if not CREDENTIALS_PATH.exists():
        raise HTTPException(status_code=404, detail="credentials.json not found on server")

    try:
        flow = Flow.from_client_secrets_file(
            str(CREDENTIALS_PATH),
            scopes=SCOPES
        )
        flow.redirect_uri = REDIRECT_URI
        
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )
        _auth_flow_instance = flow
        
        if origin and origin in ALLOWED_ORIGINS:
            _cleanup_state_map()
            _state_origin_map[state] = {
                "origin": origin,
                "expires": time.time() + 300 # 5 minutes expiry
            }
            
        return JSONResponse(content={"auth_url": authorization_url})
    except Exception as e:
        logger.error(f"Error initializing Google OAuth flow: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@auth_router.get("/google/callback")
async def google_callback(request: Request, code: str, state: str = None):
    global _auth_flow_instance
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")
        
    try:
        if _auth_flow_instance is None:
            # Fallback if server restarted during auth
            flow = Flow.from_client_secrets_file(
                str(CREDENTIALS_PATH),
                scopes=SCOPES
            )
            flow.redirect_uri = REDIRECT_URI
            flow.fetch_token(code=code)
            credentials = flow.credentials
        else:
            _auth_flow_instance.fetch_token(code=code)
            credentials = _auth_flow_instance.credentials
            _auth_flow_instance = None # reset

        
        with open(TOKEN_PATH, 'w') as token_file:
            token_file.write(credentials.to_json())
            
        if hasattr(request.app, "gmail_service"):
            try:
                request.app.gmail_service.authenticate(allow_interactive=False)
            except Exception:
                pass
        if hasattr(request.app, "calendar_service"):
            try:
                request.app.calendar_service.authenticate(allow_interactive=False)
            except Exception:
                pass
            
        redirect_url = FRONTEND_REDIRECT_URL
        if state and state in _state_origin_map:
            state_data = _state_origin_map.pop(state)
            if state_data["expires"] >= time.time():
                redirect_url = f"{state_data['origin']}/workspace"
                
        return RedirectResponse(url=redirect_url)
        
    except Exception as e:
        logger.error(f"Error in Google OAuth callback: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch token: {str(e)}")

@auth_router.get("/google/status")
async def google_status(request: Request):
    if not TOKEN_PATH.exists():
        return JSONResponse(content={"authenticated": False})
    
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GoogleRequest
        
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
            with open(TOKEN_PATH, 'w') as token_file:
                token_file.write(creds.to_json())
        
        return JSONResponse(content={"authenticated": creds.valid})
    except Exception as e:
        logger.error(f"Error checking Google token status: {e}")
        return JSONResponse(content={"authenticated": False})
