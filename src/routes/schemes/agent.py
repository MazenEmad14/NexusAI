from pydantic import BaseModel, Field
from typing import Optional, Dict

class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1)
    project_id: Optional[str] = "default"
    thread_id: Optional[str] = "default_thread"
    file_path: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    type: str                  # "retrieval" | "action:send_email" | "action:calendar" | "action:reminder"
    metadata: Optional[Dict] = {}
    status: str                # "success" | "error" | "no_results" | "pending_approval"
    thread_id: Optional[str] = "default_thread"
    action_details: Optional[Dict] = {}
    chart_data: Optional[Dict] = None

class ResumeRequest(BaseModel):
    thread_id: str
    approve: bool
    resume_token: Optional[str] = None

class TTSRequest(BaseModel):
    text: str
