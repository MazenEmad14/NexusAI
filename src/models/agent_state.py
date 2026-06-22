from typing import TypedDict, List, Dict, Optional

class AgentState(TypedDict):
    """
    LangGraph State Object — shared across all nodes in the Secretary AI workflow.
    """

    # ---- User Input ----
    query: str                          # Raw user query text
    chat_history: List[Dict]            # Contextual memory accumulation

    # ---- Classification ----
    request_type: str                   # "retrieval" | "action"
    action_type: str                    # "send_email" | "calendar" | "reminder" | ""

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
