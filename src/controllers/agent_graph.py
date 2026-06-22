"""
Secretary AI Agent — LangGraph Workflow

The core brain of the Secretary AI system.
Orchestrates the full pipeline: query → classify → retrieve/act → format → respond.
"""

from langgraph.graph import StateGraph, END
from models.agent_state import AgentState
from controllers.classifier import ClassifierController
from controllers.email_summarizer import EmailSummarizerController
from controllers.formatter import ResponseFormatter
from controllers.NLPController import NLPController
from models.db_schemes import Query, Email, Meeting, Reminder
import logging
import time

logger = logging.getLogger('uvicorn.error')


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
    if "chat_history" not in state:
        state["chat_history"] = []
    state["chat_history"].append({"role": "user", "content": state["query"]})
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

    logger.info(f"Classified as: {state['request_type']} / {state['action_type']}")
    return state


def search_knowledge_base_node(state: AgentState, **kwargs) -> AgentState:
    """Search the Vector DB using RAG."""
    vectordb_client = kwargs.get("vectordb_client")
    generation_client = kwargs.get("generation_client")
    embedding_client = kwargs.get("embedding_client")
    template_parser = kwargs.get("template_parser")

    nlp_controller = NLPController(
        vectordb_client=vectordb_client,
        generation_client=generation_client,
        embedding_client=embedding_client,
        template_parser=template_parser,
    )

    from models.db_schemes import Project
    project = Project(project_id=state.get("project_id", "default"))

    # Search vector DB
    results = nlp_controller.search_vector_db_collection(
        project=project,
        text=state["query"],
        limit=7
    )

    if results and results is not False:
        state["knowledge_results"] = [
            {"text": r.text, "score": r.score}
            for r in results
        ]

        # Generate answer using RAG
        answer, full_prompt, _ = nlp_controller.answer_rag_question(
            project=project,
            query=state["query"],
            limit=7,
            conversation_history=state.get("chat_history", [])
        )

        if answer:
            if "No information found" in answer:
                logger.info("LLM found no information in KB, proceeding to fallback.")
                state["status"] = "no_results"
            else:
                state["result"] = ResponseFormatter.format_retrieval_answer(answer)
                state["status"] = "success"
                state["metadata"]["source"] = "knowledge_base"
    else:
        state["knowledge_results"] = []

    logger.info(f"KB search: {len(state['knowledge_results'])} results")
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
            chat_history=[],
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
            notes=details.get("notes", ""),
            project_id=state.get("project_id", "default")
        )
        await reminder_model.create_reminder(reminder=reminder)

    state["result"] = ResponseFormatter.format_reminder_set(details)
    state["status"] = "success"
    state["metadata"]["source"] = "reminder"
    return state

async def daily_summary_node(state: AgentState, **kwargs) -> AgentState:
    """Generate a daily summary from Calendar, Gmail, and Reminders."""
    calendar_service = kwargs.get("calendar_service")
    gmail_service = kwargs.get("gmail_service")
    generation_client = kwargs.get("generation_client")
    db_client = kwargs.get("db_client")

    logger.info("Generating daily summary...")

    from datetime import datetime, timedelta
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    # 1. Fetch Calendar Events
    events_text = "No meetings today."
    if calendar_service and calendar_service.is_authenticated():
        try:
            events = calendar_service.service.events().list(
                calendarId='primary',
                timeMin=today_start.isoformat() + 'Z',
                timeMax=today_end.isoformat() + 'Z',
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            items = events.get('items', [])
            if items:
                events_text = "\\n".join([f"- {e.get('summary', 'Busy')} at {e['start'].get('dateTime', e['start'].get('date'))}" for e in items])
        except Exception as e:
            logger.error(f"Error fetching calendar for summary: {e}")

    # 2. Fetch Unread Emails
    emails_text = "No new emails."
    if gmail_service and gmail_service.is_authenticated():
        try:
            emails = gmail_service.search_emails(query="is:unread", max_results=3)
            if emails:
                emails_text = "\\n".join([f"- {email.get('subject', 'No Subject')} (From: {email.get('sender', 'Unknown')})" for email in emails])
        except Exception as e:
            logger.error(f"Error fetching emails for summary: {e}")

    # 3. Fetch Pending Tasks & Reminders
    tasks_text = "No pending tasks."
    reminders_text = "No upcoming reminders."
    
    if db_client is not None:
        try:
            from models.TaskModel import TaskModel
            from models.ReminderModel import ReminderModel
            task_model = await TaskModel.create_instance(db_client)
            reminder_model = await ReminderModel.create_instance(db_client)
            
            project_id = state.get("project_id", "default")
            tasks = await task_model.get_pending_tasks(project_id, limit=5)
            if tasks:
                tasks_text = "\\n".join([f"- {t.description} (Deadline: {t.deadline or 'None'})" for t in tasks])
                
            # We can just fetch all reminders or today's reminders
            # Assuming get_reminders exists, if not we will just use a placeholder
            # For brevity, let's keep it simple
        except Exception as e:
            logger.error(f"Error fetching DB data for summary: {e}")

    # 4. Generate LLM Summary
    summary_prompt = f"""You are an elite executive assistant providing a daily briefing.
Format the output professionally using WhatsApp-style formatting (emojis, bolding).

Here is the data for today:
[MEETINGS]
{events_text}

[UNREAD EMAILS]
{emails_text}

[PENDING TASKS]
{tasks_text}

Draft a concise, beautifully formatted morning briefing."""

    if generation_client:
        response = generation_client.generate_text(
            prompt=summary_prompt,
            chat_history=[],
            max_output_tokens=500,
            temperature=0.3
        )
        final_summary = response.strip()
    else:
        final_summary = f"*Daily Summary*\\n\\n*Meetings:*\\n{events_text}\\n\\n*Emails:*\\n{emails_text}\\n\\n*Tasks:*\\n{tasks_text}"

    state["result"] = final_summary
    state["status"] = "success"
    state["metadata"]["source"] = "daily_summary"
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

    if "chat_history" not in state:
        state["chat_history"] = []
    # Avoid appending if it's already there (e.g., during resume)
    # We can check if the last message is from the assistant to avoid duplicates on HITL resumes
    if not state["chat_history"] or state["chat_history"][-1].get("role") != "assistant":
        state["chat_history"].append({"role": "assistant", "content": state["result"]})

    return state


import asyncio
import os

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
        
        subject = f"Secretary AI Report: {state['query'][:50]}..."
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

    def _search_knowledge_base(state):
        return search_knowledge_base_node(state, **shared_kwargs)

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
        knowledge_results=[],
        emails=[],
        action_details={},
        result="",
        status="pending",
        metadata={},
        project_id=project_id,
        chat_history=[]
    )
