from pydantic import BaseModel
from typing import Optional, Dict

class ChatRequest(BaseModel):
    query: str
    project_id: Optional[str] = "default"
    thread_id: Optional[str] = "default_thread"

class ChatResponse(BaseModel):
    response: str
    type: str                  # "retrieval" | "action:send_email" | "action:calendar" | "action:reminder"
    metadata: Optional[Dict] = {}
    status: str                # "success" | "error" | "no_results" | "pending_approval"
    thread_id: Optional[str] = "default_thread"
    action_details: Optional[Dict] = {}

class ResumeRequest(BaseModel):
    thread_id: str
    approve: bool
