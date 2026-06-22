from pydantic import BaseModel, Field
from typing import Optional
from bson.objectid import ObjectId
from datetime import datetime

class Reminder(BaseModel):
    id: Optional[ObjectId] = Field(None, alias="_id")
    task: str = Field(..., min_length=1)
    date: str = Field(default="")                  # e.g. "2026-06-23"
    time: str = Field(default="")                  # e.g. "14:00"
    notes: str = Field(default="")
    is_completed: bool = Field(default=False)
    project_id: str = Field(default="default")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def get_indexes(cls):
        return [
            {
                "key": [("project_id", 1)],
                "name": "reminder_project_id_index",
                "unique": False
            },
            {
                "key": [("is_completed", 1), ("date", 1)],
                "name": "reminder_status_date_index",
                "unique": False
            }
        ]
