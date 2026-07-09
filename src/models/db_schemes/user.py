from pydantic import BaseModel, Field, EmailStr
from typing import Optional
from datetime import datetime

class User(BaseModel):
    user_id: str = Field(alias="_id", default="")
    name: str = Field(...)
    email: EmailStr = Field(...)
    password_hash: str = Field(...)
    is_google_connected: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
