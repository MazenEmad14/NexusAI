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
from models.agent_state import AgentState
from models.db_schemes import Query, Email, Meeting, Reminder
from services.daily_summary import DailySummaryService
from services.retrieval import detect_priority, rerank_results

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
    history.append({"role": role, "content": content})
    state["chat_history"] = history


# ─────────────────────────────────────────────
# NODE FUNCTIONS
# ─────────────────────────────────────────────

async def save_query_node(state: AgentState, **kwargs) -> AgentState:
    """Save the incoming query to MongoDB."""
    db_client = kwargs.get("db_client")

    if db_client is not None:
        from models.QueryModel import QueryModel
        query_model = await QueryModel.create_instance(db_client=db_client)
        query_record = Query(
            query_text=state["query"],
            project_id=state.get("project_id", "default")
        )
        await query_model.create_query(query=query_record)

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
    """Run parallel retrieval across KB, Gmail, tasks, calendar, and reminders."""
    vectordb_client = kwargs.get("vectordb_client")
    generation_client = kwargs.get("generation_client")
    embedding_client = kwargs.get("embedding_client")
    template_parser = kwargs.get("template_parser")
    gmail_service = kwargs.get("gmail_service")
    calendar_service = kwargs.get("calendar_service")
    db_client = kwargs.get("db_client")

    nlp_controller = NLPController(
        vectordb_client=vectordb_client,
        generation_client=generation_client,
        embedding_client=embedding_client,
        template_parser=template_parser,
    )

    from models.db_schemes import Project
    project = Project(project_id=state.get("project_id", "default"))

    async def _kb_search() -> list:
        if not (vectordb_client and embedding_client):
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

    async def _gmail_search() -> list:
        if not (gmail_service and gmail_service.is_authenticated()):
            return []
        try:
            gmail_query = state["query"]
            if generation_client:
                prompt = f"""Convert the user's natural language query into a Gmail search operator string.
Return ONLY the raw search string, nothing else.
Examples: "emails from ahmed" -> "from:ahmed", "unread emails about project X" -> "is:unread project X"
User Query: "{state['query']}"
"""
                try:
                    response = await asyncio.to_thread(
                        generation_client.generate_text,
                        prompt=prompt,
                        chat_history=state.get("chat_history", []),
                        max_output_tokens=50,
                        temperature=0.0
                    )
                    if response:
                        gmail_query = response.strip().strip("'\"").strip("`")
                        logger.info(f"Generated Gmail Query: {gmail_query}")
                except Exception as e:
                    logger.warning(f"Failed to generate smart Gmail query: {e}")
                    
            emails = await asyncio.to_thread(
                gmail_service.search_emails,
                query=gmail_query,
                max_results=3,
            )
            logger.info(f"Gmail search returned {len(emails) if emails else 0} emails.")
            return [{
                "text": f"{email.get('subject', 'No subject')}\n{email.get('snippet', '')}",
                "score": 0.95,
                "source": "gmail",
                "source_confidence": 1.0,
                "relevance": 0.95,
                "date": email.get("date"),
                "metadata": email,
            } for email in emails]
        except Exception as exc:
            logger.error(f"Gmail search failed: {exc}")
        return []

    async def _task_search() -> list:
        if db_client is None:
            return []
        try:
            from models.TaskModel import TaskModel
            task_model = await TaskModel.create_instance(db_client)
            tasks = await task_model.search_tasks(state.get("project_id", "default"), state["query"], limit=3)
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
        if not (calendar_service and calendar_service.is_authenticated()):
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
            reminders = await reminder_model.get_active_reminders(state.get("project_id", "default"), limit=3)
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

    kb_results, gmail_results, task_results, calendar_results, reminder_results = await asyncio.gather(
        _kb_search(), _gmail_search(), _task_search(), _calendar_search(), _reminder_search()
    )

    aggregated_results = []
    aggregated_results.extend(kb_results)
    aggregated_results.extend(gmail_results)
    aggregated_results.extend(task_results)
    aggregated_results.extend(calendar_results)
    aggregated_results.extend(reminder_results)

    if aggregated_results:
        ranked_results = rerank_results(aggregated_results, limit=7)
        state["knowledge_results"] = ranked_results
        state["emails"] = [item.get("metadata", {}) for item in gmail_results if item.get("metadata")]

        answer = ""
        if generation_client:
            context_text = "\n\n".join([item.get("text", "") for item in ranked_results[:5]])
            try:
                enhanced_prompt = f"You are a Secretary AI. The following context contains the user's real emails and data retrieved from their account. Use this context to answer the user's query directly and accurately. DO NOT say you cannot access emails, because the context ALREADY contains the emails you need.\n\nContext:\n{context_text}\n\nUser Query: {state['query']}"
                answer = generation_client.generate_text(
                    prompt=enhanced_prompt,
                    chat_history=state.get("chat_history", []),
                    max_output_tokens=400,
                    temperature=0.2,
                ) or ""
            except Exception as exc:
                logger.error(f"LLM answer generation failed: {exc}")

        if not answer:
            answer = ranked_results[0].get("text", "")

        state["result"] = ResponseFormatter.format_retrieval_answer(answer, source="multi_source")
        state["status"] = "success"
        state["metadata"]["source"] = "multi_source"
        state["metadata"]["priority"] = state.get("priority", detect_priority(state["query"]))
    else:
        state["knowledge_results"] = []
        state["emails"] = []
        state["result"] = ResponseFormatter.format_no_results()
        state["status"] = "no_results"
        state["metadata"]["source"] = "none"

    logger.info(f"Multi-source search produced {len(state['knowledge_results'])} ranked results")
    return state


async def search_gmail_node(state: AgentState, **kwargs) -> AgentState:
    """Search Gmail for fallback information and extract tasks."""
    gmail_service = kwargs.get("gmail_service")
    generation_client = kwargs.get("generation_client")

    logger.info("Searching Gmail for fallback...")

    if not gmail_service.is_authenticated():
        success = gmail_service.authenticate()
        if not success:
            state["result"] = ResponseFormatter.format_error("Gmail service not configured")
            state["status"] = "error"
            return state

    # Create a simple search query from the user's input
    prompt = f"""Convert the user's natural language query into a Gmail search operator string.
Return ONLY the raw search string, nothing else.
Examples: "emails from ahmed" -> "from:ahmed", "unread emails about project X" -> "is:unread project X"
User Query: "{state['query']}"
"""
    
    gmail_query = state['query']
    if generation_client:
        response = generation_client.generate_text(
            prompt=prompt,
            chat_history=state.get("chat_history", []),
            max_output_tokens=50,
            temperature=0.0
        )
        if response:
            gmail_query = response.strip().strip("'\"").strip("`")
            logger.info(f"Converted query to Gmail format: '{gmail_query}'")

    emails = gmail_service.search_emails(query=gmail_query, max_results=5)
    state["emails"] = emails

    if emails:
        summarizer = EmailSummarizerController(generation_client=generation_client)
        summaries = summarizer.summarize_emails(emails)

        state["result"] = ResponseFormatter.format_email_results(summaries)
        state["status"] = "success"
        state["metadata"]["source"] = "gmail"
        
        # --- PHASE 2: TASK EXTRACTION ---
        from controllers.task_extractor import TaskExtractorController
        task_extractor = TaskExtractorController(generation_client=generation_client)
        combined_text = "\n".join([s.get("summary", "") for s in summaries])
        extracted_tasks = task_extractor.extract_tasks(combined_text)
        
        if extracted_tasks:
            db_client = kwargs.get("db_client")
            if db_client is not None:
                from models.TaskModel import TaskModel
                from models.db_schemes.task import Task
                from models.ReminderModel import ReminderModel
                from models.db_schemes.reminder import Reminder
                
                task_model = await TaskModel.create_instance(db_client)
                reminder_model = await ReminderModel.create_instance(db_client)
                
                for task_data in extracted_tasks:
                    task = Task(
                        project_id=state.get("project_id", "default"),
                        description=task_data.get("description", ""),
                        priority=task_data.get("priority", state.get("priority", "medium")),
                    )
                    
                    deadline_str = task_data.get("deadline")
                    if deadline_str:
                        from datetime import datetime
                        try:
                            # Attempt to parse
                            task.deadline = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
                        except Exception as e:
                            logger.error(f"Failed to parse task deadline {deadline_str}: {e}")
                            
                    await task_model.create_task(task)
                    
                    # Automate Reminder creation
                    if task.deadline:
                        rem = Reminder(
                            project_id=state.get("project_id", "default"),
                            task=f"Deadline: {task.description}",
                            date=deadline_str,
                            priority=task.priority,
                            notes="Auto-extracted from email."
                        )
                        await reminder_model.create_reminder(rem)
                        
                logger.info(f"Extracted and saved {len(extracted_tasks)} tasks from emails.")
    else:
        state["result"] = ResponseFormatter.format_no_results()
        state["status"] = "no_results"
        state["metadata"]["source"] = "none"

    logger.info(f"Gmail search: {len(emails)} results")
    return state


def extract_action_details_node(state: AgentState, **kwargs) -> AgentState:
    """Extract structured details for the action."""
    generation_client = kwargs.get("generation_client")

    classifier = ClassifierController(generation_client=generation_client)
    details = classifier.extract_action_details(
        query=state["query"],
        action_type=state["action_type"],
        chat_history=state.get("chat_history", [])
    )
    
    # Priority 5 Fix: Ensure action_details are never empty for reminder HITL
    if state["action_type"] == "reminder":
        details["task"] = details.get("task") or state["query"]
        details["priority"] = details.get("priority") or state.get("priority", "medium")
        details["date"] = details.get("date", "")
        details["time"] = details.get("time", "")

    state["action_details"] = details

    logger.info(f"Action details extracted: {details}")
    return state


def send_email_node(state: AgentState, **kwargs) -> AgentState:
    """Send an email via Gmail API."""
    gmail_service = kwargs.get("gmail_service")
    details = state.get("action_details", {})

    if not gmail_service.is_authenticated():
        success = gmail_service.authenticate()
        if not success:
            state["result"] = ResponseFormatter.format_error("Gmail service not configured")
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
    calendar_service = kwargs.get("calendar_service")
    details = state.get("action_details", {})

    if not calendar_service.is_authenticated():
        success = calendar_service.authenticate()
        if not success:
            state["result"] = ResponseFormatter.format_error("Calendar service not configured")
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
            project_id=state.get("project_id", "default")
        )
        await reminder_model.create_reminder(reminder=reminder)

    state["result"] = ResponseFormatter.format_reminder_set(details)
    state["status"] = "success"
    state["metadata"]["source"] = "reminder"
    return state

async def daily_summary_node(state: AgentState, **kwargs) -> AgentState:
    """Generate a daily summary from Calendar, Gmail, tasks, and reminders."""
    calendar_service = kwargs.get("calendar_service")
    gmail_service = kwargs.get("gmail_service")
    generation_client = kwargs.get("generation_client")
    db_client = kwargs.get("db_client")

    logger.info("Generating daily summary...")

    summary_service = DailySummaryService(
        calendar_service=calendar_service,
        gmail_service=gmail_service,
        generation_client=generation_client,
        db_client=db_client,
    )
    summary_payload = await summary_service.generate(
        project_id=state.get("project_id", "default"),
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

    _append_chat_turn(state, "assistant", state["result"])

    return state


def send_report_email_node(state: AgentState, **kwargs) -> AgentState:
    """Send an async report email with the final result (Non-blocking)."""
    gmail_service = kwargs.get("gmail_service")
    
    if gmail_service and gmail_service.is_authenticated():
        report_email = os.getenv("REPORT_EMAIL")
        if not report_email:
            try:
                profile = gmail_service.service.users().getProfile(userId='me').execute()
                report_email = profile.get('emailAddress')
            except Exception:
                # If fetching fails, default to a safe value or don't send
                logger.error("Could not fetch user profile email for report.")
                return state
        
        safe_query = state['query'].replace('\n', ' ').replace('\r', '')
        subject = f"Secretary AI Report: {safe_query[:50]}..."
        body = (
            f"<h2>Secretary AI Execution Report</h2>"
            f"<p><b>Query:</b> {state['query']}</p>"
            f"<p><b>Status:</b> {state.get('status', 'unknown')}</p>"
            f"<p><b>Source:</b> {state.get('metadata', {}).get('source', 'unknown')}</p>"
            f"<p><b>Processing Time:</b> {state.get('metadata', {}).get('processing_time', 'unknown')}</p>"
            f"<hr>"
            f"<h3>Final Result:</h3>"
            f"<pre style='white-space: pre-wrap; font-family: monospace;'>{state.get('result', '')}</pre>"
        )
        
        try:
            # Fire and forget: Use threading to avoid blocking LangGraph and avoid asyncio loop errors
            import threading
            threading.Thread(
                target=gmail_service.send_email,
                kwargs={
                    "to": report_email,
                    "subject": subject,
                    "body": body,
                    "html": True
                },
                daemon=True
            ).start()
            logger.info("Dispatched async report email successfully.")
        except Exception as e:
            logger.error(f"Failed to dispatch async report email: {e}")
            
    return state


# ─────────────────────────────────────────────
# ROUTING FUNCTIONS
# ─────────────────────────────────────────────

def route_request_type(state: AgentState) -> str:
    """Route based on request type classification."""
    if state["request_type"] == "action":
        return "extract_action_details"
    
    # If action_type is summary, route to daily_summary_node
    if state["action_type"] == "summary":
        return "daily_summary"

    # Default to KB
    return "search_knowledge_base"


def route_kb_results(state: AgentState) -> str:
    """Route based on KB search results — if found, go to format; else search Gmail."""
    if state.get("knowledge_results") and len(state["knowledge_results"]) > 0 and state.get("status") == "success":
        return "format_output"
    return "search_gmail"


def route_action_type(state: AgentState) -> str:
    """Route to the correct action handler."""
    action_type = state.get("action_type", "")
    if action_type == "send_email":
        return "send_email"
    elif action_type == "calendar":
        return "create_calendar_event"
    elif action_type == "reminder":
        return "save_reminder"
    return "format_output"


# ─────────────────────────────────────────────
# BUILD THE GRAPH
# ─────────────────────────────────────────────

def build_agent_graph(
    db_client=None,
    generation_client=None,
    embedding_client=None,
    vectordb_client=None,
    template_parser=None,
    gmail_service=None,
    calendar_service=None,
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
        "gmail_service": gmail_service,
        "calendar_service": calendar_service,
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

    def _format_output(state):
        return format_output_node(state, **shared_kwargs)

    def _send_report_email(state: AgentState):
        return send_report_email_node(state, **shared_kwargs)

    # Build the graph
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("save_query", _save_query)
    graph.add_node("detect_request_type", _detect_request_type)
    graph.add_node("search_knowledge_base", _search_knowledge_base)
    graph.add_node("search_gmail", _search_gmail)
    graph.add_node("extract_action_details", _extract_action_details)
    graph.add_node("send_email", _send_email)
    graph.add_node("create_calendar_event", _create_calendar_event)
    graph.add_node("save_reminder", _save_reminder)
    graph.add_node("daily_summary", _daily_summary)
    graph.add_node("format_output", _format_output)
    graph.add_node("send_report_email", _send_report_email)

    # Set entry point
    graph.set_entry_point("save_query")

    # Add edges
    graph.add_edge("save_query", "detect_request_type")

    # Conditional: retrieval vs action
    graph.add_conditional_edges(
        "detect_request_type",
        route_request_type,
        {
            "search_knowledge_base": "search_knowledge_base",
            "daily_summary": "daily_summary",
            "extract_action_details": "extract_action_details",
        }
    )

    # Conditional: KB found → format, not found → Gmail
    graph.add_conditional_edges(
        "search_knowledge_base",
        route_kb_results,
        {
            "format_output": "format_output",
            "search_gmail": "search_gmail",
        }
    )

    # Gmail → format output
    graph.add_edge("search_gmail", "format_output")

    graph.add_edge("daily_summary", "format_output")

    # Conditional: action type routing
    graph.add_conditional_edges(
        "extract_action_details",
        route_action_type,
        {
            "send_email": "send_email",
            "create_calendar_event": "create_calendar_event",
            "save_reminder": "save_reminder",
            "format_output": "format_output",
        }
    )

    # Action results → format output
    graph.add_edge("send_email", "format_output")
    graph.add_edge("create_calendar_event", "format_output")
    graph.add_edge("save_reminder", "format_output")

    # Format → Report Email
    graph.add_edge("format_output", "send_report_email")
    
    # Report Email → END
    graph.add_edge("send_report_email", END)

    # Compile with Memory Checkpointer and HITL Interrupts
    from langgraph.checkpoint.memory import MemorySaver
    memory_saver = MemorySaver()
    
    # Priority Fix: Ensure every server reload starts with fresh checkpoint memory
    # Fully clear MemorySaver storage to prevent stale graph state contamination
    if hasattr(memory_saver, "storage") and hasattr(memory_saver.storage, "clear"):
        memory_saver.storage.clear()
    if hasattr(memory_saver, "writes") and hasattr(memory_saver.writes, "clear"):
        memory_saver.writes.clear()
    if hasattr(memory_saver, "blobs") and hasattr(memory_saver.blobs, "clear"):
        memory_saver.blobs.clear()
        
    logger.info("Memory initialized fresh")
    logger.info("Checkpoint storage cleared")
    
    compiled_graph = graph.compile(
        checkpointer=memory_saver,
        interrupt_before=["send_email", "create_calendar_event", "save_reminder"]
    )
    logger.info("Secretary AI Agent graph compiled successfully with Memory and HITL")

    return compiled_graph


def create_initial_state(query: str, project_id: str = "default") -> AgentState:
    """Create the initial state for a new agent invocation."""
    return AgentState(
        query=query,
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
        chat_history=[]
    )
