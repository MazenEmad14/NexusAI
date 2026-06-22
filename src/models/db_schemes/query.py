from pydantic import BaseModel, Field
from typing import Optional
from bson.objectid import ObjectId
from datetime import datetime

class Query(BaseModel):
    id: Optional[ObjectId] = Field(None, alias="_id")
    query_text: str = Field(..., min_length=1)
    request_type: str = Field(default="")          # "retrieval" | "action"
    action_type: str = Field(default="")           # "send_email" | "calendar" | "reminder"
    status: str = Field(default="pending")         # "pending" | "processing" | "success" | "error"
    response: str = Field(default="")
    project_id: str = Field(default="default")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def get_indexes(cls):
        return [
            {
                "key": [("created_at", -1)],
                "name": "query_created_at_index",
                "unique": False
            },
            {
                "key": [("project_id", 1)],
                "name": "query_project_id_index",
                "unique": False
            }
        ]
