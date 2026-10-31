"""Which node runs next."""

import logging
from agent.state import AgentState

logger = logging.getLogger('uvicorn.error')


def route_request_type(state: AgentState) -> str:
    """Route based on request type classification."""
    req = state.get("request_type", "")
    act = state.get("action_type", "")

    logger.info(f"[Router] request_type='{req}' action_type='{act}'")

    if req == "conversational":
        logger.info("[Router] → conversational_node")
        return "conversational_node"

    if req == "web_search":
        logger.info("[Router] → web_search")
        return "web_search"

    # action_type shortcuts — checked before generic "action" branch
    if act == "summary":
        logger.info("[Router] → daily_summary")
        return "daily_summary"

    if act == "gmail":
        logger.info("[Router] → search_gmail")
        return "search_gmail"

    if req == "action":
        # send_email / calendar / reminder are handled inside route_action_type
        logger.info("[Router] → extract_action_details")
        return "extract_action_details"

    # Default: KB retrieval (covers request_type="retrieval" and any unknown value)
    logger.info("[Router] → search_knowledge_base (default)")
    return "search_knowledge_base"


def route_action_type(state: AgentState) -> str:
    """Route to the correct action handler."""
    action_type = state.get("action_type", "")
    if action_type == "send_email":
        return "send_email"
    elif action_type == "calendar":
        return "create_calendar_event"
    elif action_type == "reminder":
        return "save_reminder"
    elif action_type == "data_analysis":
        return "data_analysis"
    elif action_type == "gmail":
        return "search_gmail"
        
    # If it's an action but the type is empty or unrecognized, don't just format output and fail.
    # Fallback to the search cascade so it can attempt to find information.
    return "search_knowledge_base"
