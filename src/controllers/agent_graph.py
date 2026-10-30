"""
Secretary AI Agent — LangGraph Workflow

The core brain of the Secretary AI system.
Orchestrates the full pipeline: query → classify → retrieve/act → format → respond.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta

from langgraph.graph import StateGraph, END

from controllers.classifier import ClassifierController
from controllers.email_summarizer import EmailSummarizerController
from controllers.formatter import ResponseFormatter
from controllers.NLPController import NLPController
from controllers.data_analysis_node import data_analysis_node
from controllers.web_search_node import web_search_node
from controllers.conversational_node import conversational_node
from models.agent_state import AgentState
from models.db_schemes import Query,Reminder
from services.daily_summary import DailySummaryService
from services.retrieval import (
    KB_ANSWER_PROMPT, KBAnswer, choose_sources, detect_priority, parse_kb_answer, rerank_results,
)
from controllers.gmail_query import build_gmail_query
from helpers.exceptions import GoogleAuthRequiredException
from helpers.google_context import get_google_token, update_google_token
from helpers.project_scope import scoped_project_id
from helpers.validation import parse_recipients
from stores.gmail.GmailService import GmailService
from stores.calendar.CalendarService import CalendarService

logger = logging.getLogger('uvicorn.error')


def _ensure_chat_history(state: AgentState) -> list:
    history = state.get("chat_history") or []
    if not isinstance(history, list):
        history = []
    state["chat_history"] = history
    return history


def _append_chat_turn(state: AgentState, role: str, content: str) -> None:
    history = _ensure_chat_history(state)
    if history and history[-1].get("role") == role and history[-1].get("content") == content:
        return
    # Create a NEW list to ensure LangGraph checkpointer detects the state change
    new_history = list(history)
    new_history.append({"role": role, "content": content})
    if len(new_history) > 10:
        new_history = new_history[-10:]
    state["chat_history"] = new_history


def _gmail_for_state(state: AgentState = None) -> GmailService:
    """Build a Gmail client for this request's user.

    A fresh instance is created per call so credentials are never shared
    between concurrent requests from different users. The token comes from the
    request context, not from graph state (state is checkpointed to the database).
    """
    token = get_google_token()
    if not token:
        raise GoogleAuthRequiredException("No Google account connected.")
    service = GmailService()
    refreshed = service.authenticate_from_dict(token)
    if refreshed:
        update_google_token(refreshed)
    return service


def _calendar_for_state(state: AgentState = None) -> CalendarService:
    """Build a Calendar client for this request's user (see _gmail_for_state)."""
    token = get_google_token()
    if not token:
        raise GoogleAuthRequiredException("No Google account connected.")
    service = CalendarService()
    refreshed = service.authenticate_from_dict(token)
    if refreshed:
        update_google_token(refreshed)
    return service


GOOGLE_CONNECT_MESSAGE = "Please connect your Google account from the Integrations panel to perform this action."


# ─────────────────────────────────────────────
# NODE FUNCTIONS
# ─────────────────────────────────────────────

async def save_query_node(state: AgentState, **kwargs) -> AgentState:
    """Save the incoming query to MongoDB and fetch available files."""
    db_client = kwargs.get("db_client")

    if db_client is not None:
        from models.QueryModel import QueryModel
        query_model = await QueryModel.create_instance(db_client=db_client)
        query_record = Query(
            query_text=state["query"],
            user_id=state.get("user_id", ""),
            project_id=state.get("project_id", "default")
        )
        await query_model.create_query(query=query_record)

        # Fetch the user's own uploaded files so data analysis can pick one by name.
        try:
            from models.AssetModel import AssetModel
            from models.ProjectModel import ProjectModel
            project_model = await ProjectModel.create_instance(db_client)
            project = await project_model.get_project_or_create_one(
                scoped_project_id(state.get("user_id", ""), state.get("project_id", "default"))
            )
            asset_model = await AssetModel.create_instance(db_client)
            assets = await asset_model.get_all_project_assets(project.id, "file")
            if assets:
                state["metadata"]["available_files"] = "\n".join(f"{a.asset_name} -> {a.asset_name}" for a in assets)
        except Exception as e:
            logger.error(f"Failed to fetch available files: {e}")

    state["metadata"]["start_time"] = time.time()
    logger.info(f"Query saved: {state['query'][:50]}...")
    
    # Append user message to contextual memory
    _append_chat_turn(state, "user", state["query"])
    return state


def detect_request_type_node(state: AgentState, **kwargs) -> AgentState:
    """Classify the query into retrieval or action."""
    generation_client = kwargs.get("generation_client")

    classifier = ClassifierController(generation_client=generation_client)
    classification = classifier.classify_query(
        query=state["query"],
        chat_history=state.get("chat_history", [])
    )

    state["request_type"] = classification["request_type"]
    state["action_type"] = classification.get("action_type", "")
    state["priority"] = classification.get("priority", detect_priority(state["query"]))
    state["metadata"]["priority"] = state["priority"]

    logger.info(f"Classified as: {state['request_type']} / {state['action_type']} [{state['priority']}]")
    return state


async def search_knowledge_base_node(state: AgentState, **kwargs) -> AgentState:
    """Answer from the user's documents, plus tasks/reminders/calendar when the question asks for them."""
    vectordb_client = kwargs.get("vectordb_client")
    generation_client = kwargs.get("generation_client")
    embedding_client = kwargs.get("embedding_client")
    template_parser = kwargs.get("template_parser")
    db_client = kwargs.get("db_client")

    nlp_controller = NLPController(
        vectordb_client=vectordb_client,
        generation_client=generation_client,
        embedding_client=embedding_client,
        template_parser=template_parser,
    )

    from models.db_schemes import Project
    try:
        project = Project(project_id=scoped_project_id(state.get("user_id", ""), state.get("project_id", "default")))
    except ValueError:
        project = None

    async def _kb_search() -> list:
        if not (vectordb_client and embedding_client and project):
            return []
        try:
            results = await asyncio.to_thread(
                nlp_controller.search_vector_db_collection,
                project,
                state["query"],
                5,
            )
            if results and results is not False:
                return [{
                    "text": getattr(r, "text", ""),
                    "score": getattr(r, "score", 0.0),
                    "source": "knowledge_base",
                    "source_confidence": 0.95,
                    "relevance": getattr(r, "score", 0.0),
                    "date": None,
                } for r in results]
        except Exception as exc:
            logger.error(f"KB search failed: {exc}")
        return []

    async def _task_search() -> list:
        if db_client is None:
            return []
        try:
            from models.TaskModel import TaskModel
            task_model = await TaskModel.create_instance(db_client)
            tasks = await task_model.search_tasks(state.get("project_id", "default"), state.get("user_id", ""), state["query"], limit=3)
            return [{
                "text": f"Task: {task.description} (deadline: {task.deadline or 'None'}, priority: {getattr(task, 'priority', 'medium')})",
                "score": 0.9,
                "source": "tasks",
                "source_confidence": 0.95,
                "relevance": 0.95,
                "date": task.deadline,
                "priority": getattr(task, "priority", state.get("priority", "medium")),
            } for task in tasks]
        except Exception as exc:
            logger.error(f"Task search failed: {exc}")
        return []

    async def _calendar_search() -> list:
        try:
            calendar_service = await asyncio.to_thread(_calendar_for_state, state)
        except GoogleAuthRequiredException:
            return []
        except Exception as exc:
            logger.error(f"Calendar client setup failed: {exc}")
            return []
        try:
            events = await asyncio.to_thread(
                calendar_service.list_events,
                max_results=3,
                days_ahead=7,
            )
            return [{
                "text": f"Calendar: {event.get('title', 'Event')} at {event.get('start', '')}",
                "score": 0.9,
                "source": "calendar",
                "source_confidence": 0.95,
                "relevance": 0.9,
                "date": event.get("start"),
            } for event in events]
        except Exception as exc:
            logger.error(f"Calendar search failed: {exc}")
        return []

    async def _reminder_search() -> list:
        if db_client is None:
            return []
        try:
            from models.ReminderModel import ReminderModel
            reminder_model = await ReminderModel.create_instance(db_client)
            reminders = await reminder_model.get_active_reminders(state.get("project_id", "default"), state.get("user_id", ""), limit=3)
            return [{
                "text": f"Reminder: {reminder.task} on {reminder.date} {reminder.time}",
                "score": 0.85,
                "source": "reminders",
                "source_confidence": 0.9,
                "relevance": 0.85,
                "date": reminder.date,
                "priority": getattr(reminder, "priority", state.get("priority", "medium")),
            } for reminder in reminders]
        except Exception as exc:
            logger.error(f"Reminder search failed: {exc}")
        return []

    searches = {
        "knowledge_base": _kb_search,
        "tasks": _task_search,
        "calendar": _calendar_search,
        "reminders": _reminder_search,
    }
    sources = choose_sources(state["query"])
    state["metadata"]["sources_searched"] = sources
    results_per_source = await asyncio.gather(*(searches[name]() for name in sources))
    aggregated_results = [item for results in results_per_source for item in results]

    state["emails"] = []
    if not aggregated_results:
        state["knowledge_results"] = []
        state["result"] = ResponseFormatter.format_no_results()
        state["status"] = "no_results"
        state["metadata"]["source"] = "none"
        logger.info(f"Retrieval searched {sources}: no results")
        return state

    ranked_results = rerank_results(aggregated_results, limit=7)
    state["knowledge_results"] = ranked_results

    # One LLM call: the model says whether the context answers the question and gives the answer.
    answer = None
    if generation_client:
        context_text = "\n\n".join(item.get("text", "") for item in ranked_results)
        try:
            raw = await generation_client.agenerate_text(
                prompt=KB_ANSWER_PROMPT.format(context=context_text, question=state["query"]),
                chat_history=state.get("chat_history", []),
                max_output_tokens=400,
                temperature=0.2,
            )
            parsed = parse_kb_answer(raw)
            if parsed is not None:
                answer = parsed
            elif raw and raw.strip():
                # Not valid JSON: use the text as the answer rather than dropping it.
                answer = KBAnswer(found=True, answer=raw.strip())
        except Exception as exc:
            logger.error(f"LLM answer generation failed: {exc}")

    if answer is None:
        # No LLM answer available: fall back to the best matching passage.
        answer = KBAnswer(found=True, answer=ranked_results[0].get("text", ""))

    if not answer.found or not answer.answer.strip():
        state["result"] = ResponseFormatter.format_no_results()
        state["status"] = "no_results"
        state["metadata"]["source"] = "none"
    else:
        single_source = len({item.get("source") for item in ranked_results}) == 1
        state["result"] = ResponseFormatter.format_retrieval_answer(
            answer.answer.strip(), source="knowledge_base" if single_source and sources == ["knowledge_base"] else "multi_source"
        )
        state["status"] = "success"
        state["metadata"]["source"] = "knowledge_base" if sources == ["knowledge_base"] else "multi_source"
        state["metadata"]["priority"] = state.get("priority", detect_priority(state["query"]))

    logger.info(f"Retrieval searched {sources}: {len(ranked_results)} ranked results, status={state['status']}")
    return state


async def _store_tasks_from_emails(state: AgentState, db_client, generation_client, emails: list, summaries: list) -> int:
    """Mine new emails for tasks and reminders. Each email is mined once, and a task the user
    already has is not created again, so repeating a request never duplicates anything."""
    if db_client is None:
        return 0

    from controllers.task_extractor import TaskExtractorController, parse_deadline
    from models.ReminderModel import ReminderModel
    from models.TaskModel import TaskModel
    from models.db_schemes.reminder import Reminder
    from models.db_schemes.task import Task

    user_id = state.get("user_id", "")
    project_id = state.get("project_id", "default")
    task_model = await TaskModel.create_instance(db_client)

    message_ids = [email.get("message_id") for email in emails if email.get("message_id")]
    unscanned = set(await task_model.filter_unscanned_emails(user_id, message_ids))
    new_pairs = [(email, summary) for email, summary in zip(emails, summaries) if email.get("message_id") in unscanned]
    if not new_pairs:
        return 0

    extractor = TaskExtractorController(generation_client=generation_client)
    combined_text = "\n".join(summary.get("summary", "") for _, summary in new_pairs)
    extracted = await asyncio.to_thread(extractor.extract_tasks, combined_text)

    reminder_model = await ReminderModel.create_instance(db_client)
    only_email = new_pairs[0][0] if len(new_pairs) == 1 else None
    created = 0
    for data in extracted:
        task = Task(
            project_id=project_id,
            user_id=user_id,
            description=data["description"],
            priority=data.get("priority") or state.get("priority", "medium"),
            source_email_id=only_email.get("message_id") if only_email else None,
            source_subject=only_email.get("subject") if only_email else None,
        )
        parsed = parse_deadline(data.get("deadline"))
        if parsed:
            task.deadline = parsed[0]

        if not await task_model.create_task_if_new(task):
            continue
        created += 1

        if parsed:
            deadline, has_time = parsed
            await reminder_model.create_reminder(Reminder(
                project_id=project_id,
                user_id=user_id,
                task=f"Deadline: {task.description}",
                date=deadline.strftime("%Y-%m-%d"),
                time=deadline.strftime("%H:%M") if has_time else "",
                priority=task.priority,
                notes="Auto-extracted from email.",
            ))

    await task_model.mark_emails_scanned(user_id, list(unscanned))
    logger.info(f"Extracted {len(extracted)} tasks from {len(new_pairs)} new emails; created {created}.")
    return created


async def search_gmail_node(state: AgentState, **kwargs) -> AgentState:
    """Search the user's Gmail, summarise the matches and extract tasks from new emails."""
    generation_client = kwargs.get("generation_client")

    logger.info("Searching Gmail...")

    try:
        gmail_service = await asyncio.to_thread(_gmail_for_state, state)
    except GoogleAuthRequiredException:
        state["result"] = GOOGLE_CONNECT_MESSAGE
        state["status"] = "error"
        return state

    plan = await build_gmail_query(generation_client, state["query"], state.get("chat_history", []))
    emails = await asyncio.to_thread(gmail_service.search_emails, query=plan.query, max_results=plan.max_results)
    state["emails"] = emails

    if not emails:
        state["result"] = ResponseFormatter.format_no_results()
        state["status"] = "no_results"
        state["metadata"]["source"] = "none"
        logger.info("Gmail search: 0 results")
        return state

    summarizer = EmailSummarizerController(generation_client=generation_client)
    summaries = await asyncio.to_thread(summarizer.summarize_emails, emails)

    state["result"] = ResponseFormatter.format_email_results(summaries)
    state["status"] = "success"
    state["metadata"]["source"] = "gmail"

    try:
        await _store_tasks_from_emails(state, kwargs.get("db_client"), generation_client, emails, summaries)
    except Exception:
        # Task extraction is a bonus; it must never fail the user's email search.
        logger.error("Task extraction from emails failed", exc_info=True)

    logger.info(f"Gmail search: {len(emails)} results")
    return state


def extract_action_details_node(state: AgentState, **kwargs) -> AgentState:
    """Extract structured details for the action."""
    generation_client = kwargs.get("generation_client")

    classifier = ClassifierController(generation_client=generation_client)
    details = classifier.extract_action_details(
        query=state["query"],
        action_type=state["action_type"],
        chat_history=state.get("chat_history", []),
        available_files=state.get("metadata", {}).get("available_files", "")
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


def send_email_node(state: AgentState, **kwargs) -> AgentState:
    """Send an email via Gmail API."""
    details = state.get("action_details", {})

    try:
        gmail_service = _gmail_for_state(state)
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


def create_calendar_event_node(state: AgentState, **kwargs) -> AgentState:
    """Create a Google Calendar event."""
    details = state.get("action_details", {})

    try:
        calendar_service = _calendar_for_state(state)
    except GoogleAuthRequiredException:
        state["result"] = GOOGLE_CONNECT_MESSAGE
        state["status"] = "error"
        return state

    result = calendar_service.create_event(
        title=details.get("title", "Meeting"),
        date=details.get("date", ""),
        time=details.get("time", "12:00"),
        attendees=details.get("attendees", []),
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


async def save_reminder_node(state: AgentState, **kwargs) -> AgentState:
    """Save a reminder to MongoDB."""
    db_client = kwargs.get("db_client")
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

async def daily_summary_node(state: AgentState, **kwargs) -> AgentState:
    """Generate a daily summary from Calendar, Gmail, tasks, and reminders."""
    generation_client = kwargs.get("generation_client")
    db_client = kwargs.get("db_client")

    logger.info("Generating daily summary...")

    try:
        calendar_service = await asyncio.to_thread(_calendar_for_state, state)
    except Exception:
        calendar_service = None
    try:
        gmail_service = await asyncio.to_thread(_gmail_for_state, state)
    except Exception:
        gmail_service = None

    summary_service = DailySummaryService(
        calendar_service=calendar_service,
        gmail_service=gmail_service,
        generation_client=generation_client,
        db_client=db_client,
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


def format_output_node(state: AgentState, **kwargs) -> AgentState:
    """Final formatting — add processing time."""
    start_time = state.get("metadata", {}).get("start_time", 0)
    if start_time:
        processing_time = round(time.time() - start_time, 2)
        state["metadata"]["processing_time"] = f"{processing_time}s"

    if not state.get("result"):
        state["result"] = ResponseFormatter.format_no_results()
        state["status"] = "no_results"

    content_for_history = state["result"]
    chart_data = state.get("chart_data")
    if chart_data:
        import json
        content_for_history += f"\n[Chart Data]: {json.dumps(chart_data)}"

    _append_chat_turn(state, "assistant", content_for_history)

    return state


# ─────────────────────────────────────────────
# ROUTING FUNCTIONS
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# BUILD THE GRAPH
# ─────────────────────────────────────────────

def build_agent_graph(
    db_client=None,
    generation_client=None,
    embedding_client=None,
    vectordb_client=None,
    template_parser=None,
    checkpointer=None,
):
    """
    Build and compile the Secretary AI LangGraph workflow.

    Returns a compiled graph that can be invoked with:
        result = await graph.ainvoke(initial_state)
    """

    # Shared kwargs for all nodes
    shared_kwargs = {
        "db_client": db_client,
        "generation_client": generation_client,
        "embedding_client": embedding_client,
        "vectordb_client": vectordb_client,
        "template_parser": template_parser,
    }

    # Wrap nodes to inject kwargs
    async def _save_query(state):
        return await save_query_node(state, **shared_kwargs)

    def _detect_request_type(state):
        return detect_request_type_node(state, **shared_kwargs)

    async def _search_knowledge_base(state):
        return await search_knowledge_base_node(state, **shared_kwargs)

    async def _search_gmail(state):
        return await search_gmail_node(state, **shared_kwargs)

    def _extract_action_details(state):
        return extract_action_details_node(state, **shared_kwargs)

    def _send_email(state):
        return send_email_node(state, **shared_kwargs)

    def _create_calendar_event(state):
        return create_calendar_event_node(state, **shared_kwargs)

    async def _save_reminder(state: AgentState):
        return await save_reminder_node(state, **shared_kwargs)

    async def _daily_summary(state: AgentState):
        return await daily_summary_node(state, **shared_kwargs)

    async def _conversational(state: AgentState):
        return await conversational_node(state, **shared_kwargs)

    async def _data_analysis(state: AgentState):
        return await data_analysis_node(state, **shared_kwargs)

    async def _web_search(state: AgentState):
        return await web_search_node(state, **shared_kwargs)

    def _format_output(state):
        return format_output_node(state, **shared_kwargs)

    # Build the graph
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("save_query", _save_query)
    graph.add_node("detect_request_type", _detect_request_type)
    graph.add_node("conversational_node", _conversational)
    graph.add_node("search_knowledge_base", _search_knowledge_base)
    graph.add_node("search_gmail", _search_gmail)
    graph.add_node("extract_action_details", _extract_action_details)
    graph.add_node("send_email", _send_email)
    graph.add_node("create_calendar_event", _create_calendar_event)
    graph.add_node("save_reminder", _save_reminder)
    graph.add_node("daily_summary", _daily_summary)
    graph.add_node("data_analysis", _data_analysis)
    graph.add_node("web_search", _web_search)
    graph.add_node("format_output", _format_output)

    # Set entry point
    graph.set_entry_point("save_query")

    # Add edges
    graph.add_edge("save_query", "detect_request_type")

    # Conditional: retrieval vs action
    graph.add_conditional_edges(
        "detect_request_type",
        route_request_type,
        {
            "conversational_node": "conversational_node",
            "search_knowledge_base": "search_knowledge_base",
            "daily_summary": "daily_summary",
            "extract_action_details": "extract_action_details",
            "data_analysis": "data_analysis",
            "web_search": "web_search",
            "search_gmail": "search_gmail",
        }
    )

    # conversational_node → format output (skip web search entirely)
    graph.add_edge("conversational_node", "format_output")

    # Retrieval and Gmail answer from one source each; there is no cascade to other sources.
    graph.add_edge("search_knowledge_base", "format_output")
    graph.add_edge("search_gmail", "format_output")

    # Web search → format output
    graph.add_edge("web_search", "format_output")

    graph.add_edge("daily_summary", "format_output")

    # Conditional: action type routing
    graph.add_conditional_edges(
        "extract_action_details",
        route_action_type,
        {
            "send_email": "send_email",
            "create_calendar_event": "create_calendar_event",
            "save_reminder": "save_reminder",
            "data_analysis": "data_analysis",
            "search_gmail": "search_gmail",
            "search_knowledge_base": "search_knowledge_base",
            "format_output": "format_output",
        }
    )

    # Action results → format output
    graph.add_edge("send_email", "format_output")
    graph.add_edge("create_calendar_event", "format_output")
    graph.add_edge("save_reminder", "format_output")
    graph.add_edge("data_analysis", "format_output")

    # Format → END (Disabled report email per user request)
    graph.add_edge("format_output", END)

    # Compile with the injected persistent checkpointer and HITL interrupts
    compiled_graph = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["send_email", "create_calendar_event", "save_reminder"]
    )
    logger.info("Aria Agent graph compiled successfully with persistent checkpointer and HITL")

    return compiled_graph


def create_initial_state(query: str, project_id: str = "default", file_path: str = None, user_id: str = "") -> AgentState:
    """Create the initial state for a new agent invocation."""
    return AgentState(
        query=query,
        user_id=user_id,
        user_email="",
        request_type="",
        action_type="",
        priority="medium",
        knowledge_results=[],
        emails=[],
        action_details={},
        result="",
        status="pending",
        metadata={},
        project_id=project_id,
        chat_history=[],
        file_path=file_path,
        chart_data=None,
    )
