"""
Dashboard API Routes — Serve MongoDB and Google Calendar data to the frontend.

Provides REST endpoints for:
  - Tasks (from MongoDB)
  - Reminders (from MongoDB)
  - Calendar Events (from Google Calendar API)
  - Daily Summary (LLM-generated briefing)
"""

from fastapi import APIRouter, status, Request, Depends
from fastapi.responses import JSONResponse
from helpers.auth_utils import get_current_user
import logging

logger = logging.getLogger('uvicorn.error')

dashboard_router = APIRouter(
    prefix="/api/v1",
    tags=["api_v1", "dashboard"],
)


# ─────────────────────────────────────────────
# GET /api/v1/tasks
# ─────────────────────────────────────────────
@dashboard_router.get("/tasks")
async def get_tasks(request: Request, project_id: str = "default", current_user: dict = Depends(get_current_user)):
    """Fetch pending tasks from MongoDB."""
    try:
        if not hasattr(request.app, "mongo_db") or request.app.mongo_db is None:
            return JSONResponse(content={
                "status": "success",
                "data": {"tasks": []},
                "message": "No database connection available"
            })

        try:
            from models.TaskModel import TaskModel
            task_model = await TaskModel.create_instance(db_client=request.app.mongo_db)
            tasks = await task_model.get_pending_tasks(project_id=project_id, user_id=current_user["_id"], limit=50)
        except Exception:
            tasks = []

        return JSONResponse(content={
            "status": "success",
            "data": {
                "tasks": [
                    {
                        "id": str(t.task_id) if t.task_id else "",
                        "description": t.description,
                        "deadline": t.deadline.isoformat() if t.deadline else None,
                        "status": t.status,
                        "created_at": t.created_at.isoformat() if t.created_at else None,
                    }
                    for t in tasks
                ]
            },
            "message": "Tasks loaded successfully"
        })

    except Exception as e:
        logger.error(f"Error fetching tasks: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status": "error", "data": {}, "message": str(e)}
        )


# ─────────────────────────────────────────────
# GET /api/v1/reminders
# ─────────────────────────────────────────────
@dashboard_router.get("/reminders")
async def get_reminders(request: Request, project_id: str = "default", current_user: dict = Depends(get_current_user)):
    """Fetch reminders from MongoDB."""
    try:
        if not hasattr(request.app, "mongo_db") or request.app.mongo_db is None:
            return JSONResponse(content={
                "status": "success",
                "data": {"reminders": []},
                "message": "No database connection available"
            })

        try:
            from models.ReminderModel import ReminderModel
            reminder_model = await ReminderModel.create_instance(db_client=request.app.mongo_db)
            reminders = await reminder_model.get_all_reminders(project_id=project_id, user_id=current_user["_id"], limit=50)
        except Exception:
            reminders = []

        return JSONResponse(content={
            "status": "success",
            "data": {
                "reminders": [
                    {
                        "id": str(r.id) if r.id else "",
                        "task": r.task,
                        "date": r.date,
                        "time": r.time,
                        "notes": r.notes,
                        "is_completed": r.is_completed,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in reminders
                ]
            },
            "message": "Reminders loaded successfully"
        })

    except Exception as e:
        logger.error(f"Error fetching reminders: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status": "error", "data": {}, "message": str(e)}
        )


# ─────────────────────────────────────────────
# GET /api/v1/calendar/events
# ─────────────────────────────────────────────
@dashboard_router.get("/calendar/events")
async def get_calendar_events(request: Request, days_ahead: int = 7, max_results: int = 20):
    """Fetch upcoming Google Calendar events."""
    try:
        calendar_service = request.app.calendar_service

        if not calendar_service or not calendar_service.is_authenticated():
            return JSONResponse(content={
                "status": "success",
                "data": {
                    "events": [],
                    "warning": "Calendar service not authenticated"
                },
                "message": "Calendar events loaded successfully"
            })

        events = calendar_service.list_events(
            max_results=max_results,
            days_ahead=days_ahead
        )

        return JSONResponse(content={
            "status": "success",
            "data": {"events": events},
            "message": "Calendar events loaded successfully"
        })

    except Exception as e:
        logger.error(f"Error fetching calendar events: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status": "error", "data": {}, "message": str(e)}
        )


# ─────────────────────────────────────────────
# GET /api/v1/summary/daily
# ─────────────────────────────────────────────
@dashboard_router.get("/summary/daily")
async def get_daily_summary(request: Request, project_id: str = "default"):
    """Generate an LLM-powered daily briefing from Calendar, Gmail, Tasks, and Reminders."""
    try:
        calendar_service = request.app.calendar_service
        gmail_service = request.app.gmail_service
        generation_client = request.app.generation_client
        db_client = request.app.mongo_db

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
                    events_text = "\n".join([
                        f"- {e.get('summary', 'Busy')} at {e['start'].get('dateTime', e['start'].get('date'))}"
                        for e in items
                    ])
            except Exception as e:
                logger.error(f"Error fetching calendar for summary: {e}")

        # 2. Fetch Unread Emails
        emails_text = "No new emails."
        if gmail_service and gmail_service.is_authenticated():
            try:
                emails = gmail_service.search_emails(query="is:unread", max_results=5)
                if emails:
                    emails_text = "\n".join([
                        f"- {email.get('subject', 'No Subject')} (From: {email.get('sender', 'Unknown')})"
                        for email in emails
                    ])
            except Exception as e:
                logger.error(f"Error fetching emails for summary: {e}")

        # 3. Fetch Pending Tasks
        tasks_text = "No pending tasks."
        if db_client is not None:
            try:
                from models.TaskModel import TaskModel
                task_model = await TaskModel.create_instance(db_client)
                tasks = await task_model.get_pending_tasks(project_id, limit=5)
                if tasks:
                    tasks_text = "\n".join([
                        f"- {t.description} (Deadline: {t.deadline or 'None'})"
                        for t in tasks
                    ])
            except Exception as e:
                logger.error(f"Error fetching tasks for summary: {e}")

        # 4. Fetch Active Reminders
        reminders_text = "No upcoming reminders."
        if db_client is not None:
            try:
                from models.ReminderModel import ReminderModel
                reminder_model = await ReminderModel.create_instance(db_client)
                reminders = await reminder_model.get_active_reminders(project_id, limit=5)
                if reminders:
                    reminders_text = "\n".join([
                        f"- {r.task} on {r.date} {r.time}"
                        for r in reminders
                    ])
            except Exception as e:
                logger.error(f"Error fetching reminders for summary: {e}")

        # 5. Generate LLM Summary
        summary_prompt = f"""You are Aria, an elite executive assistant providing a daily briefing.
Format the output professionally using WhatsApp-style formatting (emojis, bolding with *text*).

Here is the data for today:
[MEETINGS]
{events_text}

[UNREAD EMAILS]
{emails_text}

[PENDING TASKS]
{tasks_text}

[UPCOMING REMINDERS]
{reminders_text}

Draft a concise, beautifully formatted morning briefing."""

        summary = ""
        if generation_client:
            response = generation_client.generate_text(
                prompt=summary_prompt,
                chat_history=[],
                max_output_tokens=600,
                temperature=0.3
            )
            summary = response.strip() if response else ""

        if not summary:
            summary = (
                f"📊 **Daily Summary**\n\n"
                f"**📅 Meetings:**\n{events_text}\n\n"
                f"**📩 Emails:**\n{emails_text}\n\n"
                f"**✅ Tasks:**\n{tasks_text}\n\n"
                f"**🔔 Reminders:**\n{reminders_text}"
            )

        return JSONResponse(content={
            "status": "success",
            "data": {"summary": summary},
            "message": "Daily summary generated successfully"
        })

    except Exception as e:
        logger.error(f"Error generating daily summary: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status": "error", "data": {}, "message": str(e)}
        )


# ─────────────────────────────────────────────
# POST /api/v1/auth/gmail/logout
# ─────────────────────────────────────────────
@dashboard_router.get("/health/services")
async def health_services(request: Request):
    """Return a stable health payload for the dashboard and UI status widgets."""
    try:
        db_ok = False
        if hasattr(request.app, "mongo_db") and request.app.mongo_db is not None:
            db_ok = True

        gmail_ok = bool(getattr(request.app, "gmail_service", None) and getattr(request.app.gmail_service, "service", None))
        calendar_ok = bool(getattr(request.app, "calendar_service", None) and getattr(request.app.calendar_service, "service", None))

        return JSONResponse(content={
            "status": "success",
            "data": {
                "database": "ok" if db_ok else "error",
                "gmail": "ok" if gmail_ok else "error",
                "calendar": "ok" if calendar_ok else "error",
            },
            "message": "Service health retrieved"
        })
    except Exception as e:
        logger.error(f"Error checking service health: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status": "error", "data": {}, "message": str(e)}
        )


@dashboard_router.get("/memory/{thread_id}")
async def get_thread_memory(request: Request, thread_id: str):
    """Return the persisted chat history for a thread when available."""
    try:
        agent_graph = getattr(request.app, "agent_graph", None)
        if not agent_graph:
            return JSONResponse(content={"status": "success", "data": {"thread_id": thread_id, "chat_history": []}, "message": "No agent graph available"})

        config = {"configurable": {"thread_id": thread_id}}
        try:
            state_snapshot = agent_graph.get_state(config)
            chat_history = state_snapshot.values.get("chat_history", []) if state_snapshot and state_snapshot.values else []
        except Exception:
            chat_history = []

        return JSONResponse(content={
            "status": "success",
            "data": {"thread_id": thread_id, "chat_history": chat_history},
            "message": "Thread memory retrieved"
        })
    except Exception as e:
        logger.error(f"Error retrieving thread memory: {e}", exc_info=True)
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"status": "error", "data": {}, "message": str(e)})


@dashboard_router.post("/auth/gmail/logout")
async def logout_gmail(request: Request):
    """Logout of Gmail and delete token."""
    try:
        gmail_service = request.app.gmail_service
        if gmail_service:
            gmail_service.logout_gmail()
            return JSONResponse(content={"message": "Gmail logged out successfully"})
        return JSONResponse(content={"warning": "Gmail service not initialized"})
    except Exception as e:
        logger.error(f"Error logging out of Gmail: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": str(e)}
        )

# ─────────────────────────────────────────────
# GET /api/v1/queries
# ─────────────────────────────────────────────
@dashboard_router.get("/queries")
async def get_queries(request: Request, project_id: str = "default"):
    """Fetch query history from MongoDB."""
    try:
        if not hasattr(request.app, "mongo_db") or request.app.mongo_db is None:
            return JSONResponse(content={
                "status": "success",
                "data": [],
                "message": "No database connection available"
            })

        try:
            from models.QueryModel import QueryModel
            query_model = await QueryModel.create_instance(db_client=request.app.mongo_db)
            queries = await query_model.get_recent_queries(project_id=project_id, limit=50)
        except Exception:
            queries = []

        return JSONResponse(content={
            "status": "success",
            "data": [
                {
                    "id": str(q.query_id) if q.query_id else "",
                    "query_text": q.query_text,
                    "created_at": q.timestamp.isoformat() if q.timestamp else None,
                }
                for q in queries
            ],
            "message": "Query history fetched"
        })

    except Exception as e:
        logger.error(f"Error fetching queries: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status": "error", "data": [], "message": str(e)}
        )
