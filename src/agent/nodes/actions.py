"""Actions the user approves before they run: send email, create an event, set a reminder."""

import logging
from agent.deps import AgentDeps
from agent.google_clients import GOOGLE_CONNECT_MESSAGE
from agent.google_clients import calendar_for_request
from agent.google_clients import gmail_for_request
from agent.state import AgentState
from controllers.classifier import ClassifierController
from controllers.formatter import ResponseFormatter
from helpers.exceptions import GoogleAuthRequiredException
from helpers.validation import parse_recipients
from models.db_schemes import Reminder

logger = logging.getLogger('uvicorn.error')


def extract_action_details_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Extract structured details for the action."""
    generation_client = deps.generation_client

    classifier = ClassifierController(generation_client=generation_client)
    details = classifier.extract_action_details(
        query=state["query"],
        action_type=state["action_type"],
        chat_history=state.get("chat_history", []),
        available_files=state.get("metadata", {}).get("available_files", ""),
        timezone_name=state.get("user_timezone") or None,
    )
    
    # Priority 5 Fix: Ensure action_details are never empty for reminder HITL
    if state["action_type"] == "reminder":
        details["task"] = details.get("task") or state["query"]
        details["priority"] = details.get("priority") or state.get("priority", "medium")
        details["date"] = details.get("date", "")
        details["time"] = details.get("time", "")

    state["action_details"] = details
    
    # Priority fix: Lift file_path to state for data_analysis_node
    if state["action_type"] == "data_analysis" and details.get("file_path"):
        state["file_path"] = details["file_path"]

    logger.info(f"Action details extracted: {details}")
    return state


def send_email_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Send an email via Gmail API."""
    details = state.get("action_details", {})

    try:
        gmail_service = gmail_for_request()
    except GoogleAuthRequiredException:
        state["result"] = GOOGLE_CONNECT_MESSAGE
        state["status"] = "error"
        return state

    if parse_recipients(details.get("recipient", "")) is None:
        state["result"] = ResponseFormatter.format_error("I couldn't find a valid recipient email address, so nothing was sent.")
        state["status"] = "error"
        return state

    result = gmail_service.send_email(
        to=details.get("recipient", ""),
        subject=details.get("subject", ""),
        body=details.get("body", "")
    )

    if result:
        state["result"] = ResponseFormatter.format_email_sent(result)
        state["status"] = "success"
        state["metadata"]["source"] = "gmail_send"
        state["metadata"]["message_id"] = result.get("message_id", "")
    else:
        state["result"] = ResponseFormatter.format_error("Failed to send email")
        state["status"] = "error"

    return state


def create_calendar_event_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Create a Google Calendar event."""
    details = state.get("action_details", {})

    try:
        calendar_service = calendar_for_request()
    except GoogleAuthRequiredException:
        state["result"] = GOOGLE_CONNECT_MESSAGE
        state["status"] = "error"
        return state

    result = calendar_service.create_event(
        title=details.get("title", "Meeting"),
        date=details.get("date", ""),
        time=details.get("time", "12:00"),
        attendees=details.get("attendees", []),
        time_zone=state.get("user_timezone") or None,
    )

    if result:
        state["result"] = ResponseFormatter.format_event_created(result)
        state["status"] = "success"
        state["metadata"]["source"] = "calendar"
        state["metadata"]["event_id"] = result.get("event_id", "")
    else:
        state["result"] = ResponseFormatter.format_error("Failed to create calendar event")
        state["status"] = "error"

    return state


async def save_reminder_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Save a reminder to MongoDB."""
    db_client = deps.db_client
    details = state.get("action_details", {})

    if db_client is not None:
        from models.ReminderModel import ReminderModel
        reminder_model = await ReminderModel.create_instance(db_client=db_client)
        reminder = Reminder(
            task=details.get("task", state["query"]),
            date=details.get("date", ""),
            time=details.get("time", ""),
            priority=details.get("priority", state.get("priority", "medium")),
            notes=details.get("notes", ""),
            project_id=state.get("project_id", "default"),
            user_id=state.get("user_id", "")
        )
        await reminder_model.create_reminder(reminder=reminder)

    state["result"] = ResponseFormatter.format_reminder_set(details)
    state["status"] = "success"
    state["metadata"]["source"] = "reminder"
    return state
