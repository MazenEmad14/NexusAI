from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class Task(BaseModel):
    task_id: str = Field(alias="_id", default="")
    project_id: str = Field(default="default")
    description: str = Field(...)
    deadline: Optional[datetime] = Field(default=None)
    status: str = Field(default="pending") # "pending", "completed"
    source_email_id: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
