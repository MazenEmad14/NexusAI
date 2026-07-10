from passlib.context import CryptContext
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional
import os

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "super-secret-key-for-local-development")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 days

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

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
        logger.error(f"DEBUG: get_current_user 401 - Invalid token. decoded={decoded}, token={token}")
        raise HTTPException(status_code=401, detail="Invalid token")
    
    email = decoded["sub"]
    if not hasattr(request.app, "mongo_db"):
        logger.error("DEBUG: get_current_user 500 - DB not initialized")
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    user_model = await UserModel.create_instance(request.app.mongo_db)
    user_data = await user_model.get_user_by_email(email)
    
    if not user_data:
        logger.error(f"DEBUG: get_current_user 401 - User not found in DB for email {email}")
        raise HTTPException(status_code=401, detail="User not found")
        
    return user_data
