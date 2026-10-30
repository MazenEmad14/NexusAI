"""
Dashboard API Routes — Serve MongoDB and Google Calendar data to the frontend.

Provides REST endpoints for:
  - Tasks (from MongoDB)
  - Reminders (from MongoDB)
  - Calendar Events (from Google Calendar API)
  - Daily Summary (LLM-generated briefing)
"""

import asyncio
import logging

from fastapi import APIRouter, status, Request, Depends
from fastapi.responses import JSONResponse

from helpers.auth_utils import get_current_user
from models.UserModel import UserModel
from services.daily_summary import DailySummaryService
from stores.calendar.CalendarService import CalendarService
from stores.gmail.GmailService import GmailService

logger = logging.getLogger('uvicorn.error')

dashboard_router = APIRouter(
    prefix="/api/v1",
    tags=["api_v1", "dashboard"],
)


def _build_google_clients(token: dict):
    """Build per-request Gmail/Calendar clients from one user's token (never shared)."""
    gmail = calendar = None
    refreshed = None
    try:
        gmail = GmailService()
        refreshed = gmail.authenticate_from_dict(token)
    except Exception as exc:
        logger.warning(f"Gmail unavailable for this user: {exc}")
        gmail = None
    try:
        calendar = CalendarService()
        refreshed = calendar.authenticate_from_dict(token) or refreshed
    except Exception as exc:
        logger.warning(f"Calendar unavailable for this user: {exc}")
        calendar = None
    return gmail, calendar, refreshed


async def _google_clients_for(request: Request, current_user: dict):
    """Return (gmail, calendar) for the logged-in user, or (None, None) without a token."""
    user_model = await UserModel.create_instance(request.app.mongo_db)
    token = await user_model.get_google_token(current_user["email"])
    if not token:
        return None, None
    gmail, calendar, refreshed = await asyncio.to_thread(_build_google_clients, token)
    if refreshed and refreshed != token:
        await user_model.update_google_token(current_user["email"], refreshed)
    return gmail, calendar


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
            tasks = await task_model.get_pending_tasks(project_id=project_id, user_id=str(current_user["_id"]), limit=50)
        except Exception as e:
            logger.error("Error fetching tasks for dashboard", exc_info=True)
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
            reminders = await reminder_model.get_all_reminders(project_id=project_id, user_id=str(current_user["_id"]), limit=50)
        except Exception as e:
            logger.error("Error fetching reminders for dashboard", exc_info=True)
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
async def get_calendar_events(request: Request, days_ahead: int = 7, max_results: int = 20, current_user: dict = Depends(get_current_user)):
    """Fetch upcoming Google Calendar events for the logged-in user."""
    try:
        _, calendar_service = await _google_clients_for(request, current_user)

        if calendar_service is None:
            return JSONResponse(content={
                "status": "success",
                "data": {
                    "events": [],
                    "warning": "Calendar service not authenticated for this user"
                },
                "message": "Calendar events loaded successfully"
            })

        events = await asyncio.to_thread(
            calendar_service.list_events,
            max_results=max_results,
            days_ahead=days_ahead,
        )

        return JSONResponse(content={
            "status": "success",
            "data": {"events": events},
            "message": "Calendar events loaded successfully"
        })

    except Exception:
        logger.error("Error fetching calendar events", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status": "error", "data": {}, "message": "Failed to load calendar events"}
        )


# ─────────────────────────────────────────────
# GET /api/v1/summary/daily
# ─────────────────────────────────────────────
@dashboard_router.get("/summary/daily")
async def get_daily_summary(request: Request, project_id: str = "default", current_user: dict = Depends(get_current_user)):
    """Generate an LLM-powered daily briefing from Calendar, Gmail, Tasks, and Reminders."""
    try:
        gmail_service, calendar_service = await _google_clients_for(request, current_user)

        summary_service = DailySummaryService(
            calendar_service=calendar_service,
            gmail_service=gmail_service,
            generation_client=request.app.generation_client,
            db_client=request.app.mongo_db,
        )
        payload = await summary_service.generate(
            project_id=project_id,
            user_id=str(current_user["_id"]),
        )

        return JSONResponse(content={
            "status": "success",
            "data": {"summary": payload["summary"]},
            "message": "Daily summary generated successfully"
        })

    except Exception:
        logger.error("Error generating daily summary", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status": "error", "data": {}, "message": "Failed to generate daily summary"}
        )


# ─────────────────────────────────────────────
# POST /api/v1/auth/gmail/logout
# ─────────────────────────────────────────────
@dashboard_router.get("/health/services")
async def health_services(request: Request, current_user: dict = Depends(get_current_user)):
    """Return a stable health payload for the dashboard and UI status widgets."""
    try:
        db_ok = getattr(request.app, "mongo_db", None) is not None

        user_model = await UserModel.create_instance(request.app.mongo_db)
        google_ok = bool(await user_model.get_google_token(current_user["email"]))

        return JSONResponse(content={
            "status": "success",
            "data": {
                "database": "ok" if db_ok else "error",
                "gmail": "ok" if google_ok else "error",
                "calendar": "ok" if google_ok else "error",
            },
            "message": "Service health retrieved"
        })
    except Exception:
        logger.error("Error checking service health", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status": "error", "data": {}, "message": "Failed to check service health"}
        )


@dashboard_router.get("/memory/{thread_id}")
async def get_thread_memory(request: Request, thread_id: str, current_user: dict = Depends(get_current_user)):
    """Return the persisted chat history for one of the caller's threads."""
    try:
        agent_graph = getattr(request.app, "agent_graph", None)
        if not agent_graph:
            return JSONResponse(content={"status": "success", "data": {"thread_id": thread_id, "chat_history": []}, "message": "No agent graph available"})

        # Threads are namespaced by user id, exactly as in the agent routes.
        namespaced_thread = f"{str(current_user['_id'])}_{thread_id}"
        config = {"configurable": {"thread_id": namespaced_thread}}
        try:
            state_snapshot = await agent_graph.aget_state(config)
            chat_history = state_snapshot.values.get("chat_history", []) if state_snapshot and state_snapshot.values else []
        except Exception:
            logger.error("Error fetching thread memory", exc_info=True)
            chat_history = []

        return JSONResponse(content={
            "status": "success",
            "data": {"thread_id": thread_id, "chat_history": chat_history},
            "message": "Thread memory retrieved"
        })
    except Exception:
        logger.error("Error retrieving thread memory", exc_info=True)
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"status": "error", "data": {}, "message": "Failed to retrieve thread memory"})


@dashboard_router.post("/auth/gmail/logout")
async def logout_gmail(request: Request, current_user: dict = Depends(get_current_user)):
    """Disconnect the logged-in user's Google account (clears only their token)."""
    try:
        user_model = await UserModel.create_instance(request.app.mongo_db)
        await user_model.update_google_token(current_user["email"], None)
        return JSONResponse(content={"message": "Gmail logged out successfully"})
    except Exception:
        logger.error("Error logging out of Gmail", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to log out of Gmail"}
        )

# ─────────────────────────────────────────────
# GET /api/v1/queries
# ─────────────────────────────────────────────
@dashboard_router.get("/queries")
async def get_queries(request: Request, project_id: str = "default", current_user: dict = Depends(get_current_user)):
    """Fetch the logged-in user's query history from MongoDB."""
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
            queries = await query_model.get_recent_queries(
                project_id=project_id, user_id=str(current_user["_id"]), limit=50
            )
        except Exception:
            logger.error("Error fetching queries", exc_info=True)
            queries = []

        return JSONResponse(content={
            "status": "success",
            "data": [
                {
                    "id": str(q.id) if q.id else "",
                    "query_text": q.query_text,
                    "created_at": q.created_at.isoformat() if q.created_at else None,
                }
                for q in queries
            ],
            "message": "Query history fetched"
        })

    except Exception:
        logger.error("Error fetching queries", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status": "error", "data": [], "message": "Failed to fetch query history"}
        )
