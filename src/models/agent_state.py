from typing import TypedDict, List, Dict, Optional, Any

class AgentState(TypedDict):
    """
    LangGraph State Object — shared across all nodes in the Secretary AI workflow.
    """

    # ---- User Input ----
    query: str                          # Raw user query text
    user_id: str                        # The user ID executing the request
    user_email: str                     # The user's email (used for per-user Google token lookup)
    chat_history: List[Dict]            # Contextual memory accumulation
    file_path: Optional[str]            # Optional file path for data analysis
    chart_data: Optional[Dict[str, Any]] # Optional chart data for rendering interactive charts

    # ---- Classification ----
    request_type: str                   # "retrieval" | "action"
    action_type: str                    # "send_email" | "calendar" | "reminder" | ""
    priority: str                      # "low" | "medium" | "high" | "critical"

    # ---- Retrieval Results ----
    knowledge_results: List[Dict]       # RAG / Vector DB search results
    emails: List[Dict]                  # Gmail search results

    # ---- Action Details ----
    action_details: Dict                # Extracted parameters for the action
                                        # e.g. {"recipient": "...", "subject": "...", "body": "..."}

    # ---- Output ----
    result: str                         # Final formatted response (WhatsApp-style)
    status: str                         # "success" | "error" | "no_results"

    # ---- Metadata ----
    metadata: Dict                      # Additional context: source, processing_time, etc.
    project_id: str                     # Current project context

    # ---- Per-User Google Auth ----
    google_token: Optional[Dict[str, Any]]  # Per-user OAuth2 token dict (from MongoDB)
                                             # Replaces shared token.json — enables multi-user Google isolation
