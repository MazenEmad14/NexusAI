from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, EmailStr, field_validator
import logging
from models.db_schemes.user import User
from models.UserModel import UserModel
from helpers import auth_utils

logger = logging.getLogger("uvicorn.error")

local_auth_router = APIRouter(
    prefix="/api/v1/auth/local",
    tags=["api_v1", "auth", "local"]
)

class SignUpRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    
    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        return v

class SignInRequest(BaseModel):
    email: EmailStr
    password: str

@local_auth_router.post("/signup")
async def signup(request: Request, body: SignUpRequest):
    if not hasattr(request.app, "mongo_db"):
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    user_model = await UserModel.create_instance(request.app.mongo_db)
    
    # Check if user already exists
    existing_user = await user_model.get_user_by_email(body.email)
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already exists")
        
    hashed_password = auth_utils.get_password_hash(body.password)
    
    new_user = User(
        name=body.name,
        email=body.email,
        password_hash=hashed_password,
        is_google_connected=False
    )
    
    success = await user_model.create_user(new_user)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to create user")
        
    # Generate token
    token = auth_utils.create_access_token({"sub": body.email})
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "name": body.name,
            "email": body.email,
            "is_google_connected": False
        }
    }

@local_auth_router.post("/signin")
async def signin(request: Request, body: SignInRequest):
    if not hasattr(request.app, "mongo_db"):
        raise HTTPException(status_code=500, detail="Database not initialized")
        
    user_model = await UserModel.create_instance(request.app.mongo_db)
    
    # Problem 1 fix: Check if user exists first!
    user_data = await user_model.get_user_by_email(body.email)
    if not user_data:
        raise HTTPException(status_code=404, detail="Email not registered")
        
    # Then verify password
    if not auth_utils.verify_password(body.password, user_data.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Incorrect password")
        
    # Verify Google Auth status if token exists
    from pathlib import Path
    token_path = Path(__file__).resolve().parent.parent / "token.json"
    is_google_connected = token_path.exists()
    
    # Optional: Update the DB if it mismatches
    if user_data.get("is_google_connected") != is_google_connected:
        await user_model.update_google_auth_status(body.email, is_google_connected)
        
    token = auth_utils.create_access_token({"sub": body.email})
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "name": user_data.get("name"),
            "email": body.email,
            "is_google_connected": is_google_connected
        }
    }
