from pydantic import BaseModel, Field
from typing import Optional, Dict

class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1)
    project_id: Optional[str] = Field("default", pattern=r"^[A-Za-z0-9]{1,64}$")
    thread_id: Optional[str] = Field("default_thread", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    # Only a file *name* from the user's own project is honoured; see data_analysis_node.
    file_path: Optional[str] = Field(None, max_length=255)

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
