"""The daily briefing."""

import logging
from agent.deps import AgentDeps
from agent.google_clients import calendar_for_request
from agent.google_clients import gmail_for_request
from agent.state import AgentState
from services.daily_summary import DailySummaryService
import asyncio

logger = logging.getLogger('uvicorn.error')


async def daily_summary_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Generate a daily summary from Calendar, Gmail, tasks, and reminders."""
    generation_client = deps.generation_client
    db_client = deps.db_client

    logger.info("Generating daily summary...")

    try:
        calendar_service = await asyncio.to_thread(calendar_for_request)
    except Exception:
        calendar_service = None
    try:
        gmail_service = await asyncio.to_thread(gmail_for_request)
    except Exception:
        gmail_service = None

    summary_service = DailySummaryService(
        calendar_service=calendar_service,
        gmail_service=gmail_service,
        generation_client=generation_client,
        db_client=db_client,
        timezone_name=state.get("user_timezone") or None,
    )
    summary_payload = await summary_service.generate(
        project_id=state.get("project_id", "default"),
        user_id=state.get("user_id", ""),
        chat_history=state.get("chat_history", []),
    )

    state["result"] = summary_payload.get("summary", "")
    state["status"] = "success"
    state["metadata"]["source"] = "daily_summary"
    state["metadata"]["priority"] = state.get("priority", "medium")
    return state
