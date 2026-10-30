import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional
from helpers.config import get_settings

SECRET_KEY = get_settings().JWT_SECRET_KEY
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 days


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
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": int(expire.timestamp())})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str):
    try:
        decoded_data = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return decoded_data
    except jwt.PyJWTError:
        return None

from fastapi import Request, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from models.UserModel import UserModel

security = HTTPBearer()

import logging
logger = logging.getLogger('uvicorn.error')

async def get_current_user(request: Request, credentials: HTTPAuthorizationCredentials = Security(security)):
    token = credentials.credentials
    decoded = decode_access_token(token)
    if not decoded or "sub" not in decoded:
        logger.warning("get_current_user 401 - invalid or malformed token")
        raise HTTPException(status_code=401, detail="Invalid token")
    
    email = normalize_email(decoded["sub"])
    if not hasattr(request.app, "mongo_db"):
        logger.error("get_current_user 500 - DB not initialized")
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    user_model = await UserModel.create_instance(request.app.mongo_db)
    user_data = await user_model.get_user_by_email(email)
    
    if not user_data:
        logger.warning("get_current_user 401 - user for a valid token no longer exists")
        raise HTTPException(status_code=401, detail="User not found")
        
    return user_data
