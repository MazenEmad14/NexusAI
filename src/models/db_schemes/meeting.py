from pydantic import BaseModel, Field
from typing import Optional, List
from bson.objectid import ObjectId
from datetime import datetime

class Meeting(BaseModel):
    id: Optional[ObjectId] = Field(None, alias="_id")
    title: str = Field(..., min_length=1)
    date: str = Field(default="")                  # e.g. "2026-06-23"
    time: str = Field(default="")                  # e.g. "17:00"
    attendees: List[str] = Field(default_factory=list)
    google_event_id: str = Field(default="")
    status: str = Field(default="scheduled")       # "scheduled" | "cancelled" | "completed"
    project_id: str = Field(default="default")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def get_indexes(cls):
        return [
            {
                "key": [("project_id", 1)],
                "name": "meeting_project_id_index",
                "unique": False
            },
            {
                "key": [("date", 1)],
                "name": "meeting_date_index",
                "unique": False
            }
        ]
