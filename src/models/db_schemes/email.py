from pydantic import BaseModel, Field
from typing import Optional
from bson.objectid import ObjectId
from datetime import datetime

class Email(BaseModel):
    id: Optional[ObjectId] = Field(None, alias="_id")
    sender: str = Field(default="")
    receiver: str = Field(default="")
    subject: str = Field(default="")
    body: str = Field(default="")
    summary: str = Field(default="")
    direction: str = Field(default="received")     # "received" | "sent"
    gmail_message_id: str = Field(default="")
    project_id: str = Field(default="default")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def get_indexes(cls):
        return [
            {
                "key": [("project_id", 1)],
                "name": "email_project_id_index",
                "unique": False
            },
            {
                "key": [("created_at", -1)],
                "name": "email_created_at_index",
                "unique": False
            }
        ]
