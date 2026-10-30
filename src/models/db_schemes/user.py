from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Dict, Any
from datetime import datetime

class User(BaseModel):
    user_id: str = Field(alias="_id", default="")
    name: str = Field(...)
    email: EmailStr = Field(...)
    password_hash: str = Field(...)
    is_google_connected: bool = Field(default=False)
    timezone: Optional[str] = Field(default=None)  # IANA name; None means the deployment default
    # Per-user Google OAuth2 token stored as dict (replaces shared token.json)
    google_token: Optional[Dict[str, Any]] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
