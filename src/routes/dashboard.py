"""
Dashboard API Routes — serve the logged-in user's tasks, reminders, calendar, summary and history.

Response contract (every route):
  success:  200 {"status": "success", "data": {...}, "message": "..."}
  failure:  4xx/5xx {"status": "error", "data": {}, "message": "<safe, human-readable text>"}

An empty list always means "nothing to show". A failure is never reported as an empty success,
and internal error text is logged, not sent to the client.
"""

import asyncio
import functools
import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse, Response
from pymongo.errors import PyMongoError

from helpers.auth_utils import get_current_user
from helpers.services import services_of
from helpers.exceptions import DomainError, GoogleAuthRequiredException
from models.UserModel import UserModel
from services.daily_summary import DailySummaryService
from stores.calendar.CalendarService import CalendarService
from stores.gmail.GmailService import GmailService

logger = logging.getLogger('uvicorn.error')

dashboard_router = APIRouter(
    prefix="/api/v1",
    tags=["api_v1", "dashboard"],
)

PROJECT_ID_PATTERN = r"^[A-Za-z0-9]{1,64}$"
THREAD_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


# ─────────────────────────────────────────────
# Response envelope
# ─────────────────────────────────────────────
class DashboardError(Exception):
    """An expected failure with a message that is safe to show the user."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"status": "error", "data": {}, "message": message})


def envelope(success_message: str, failure_message: str):
    """Wrap a route that returns its `data` dict (or raises) into the standard response."""

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                data = await fn(*args, **kwargs)
            except DashboardError as exc:
                return _error_response(exc.status_code, exc.message)
            except DomainError as exc:
                logger.warning(f"{failure_message}: {exc.code}: {exc}")
                return _error_response(exc.status_code, exc.public_message)
            except PyMongoError:
                logger.error(failure_message, exc_info=True)
                return _error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "The database is temporarily unavailable")
            except Exception:
                logger.error(failure_message, exc_info=True)
                return _error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, failure_message)
            if isinstance(data, Response):
                return data
            return JSONResponse(content={"status": "success", "data": data, "message": success_message})

        return wrapper

    return decorator


def _services(request: Request):
    """The running services; while the app is not ready this is the standard 503 envelope."""
    try:
        return services_of(request)
    except HTTPException as exc:
        raise DashboardError(exc.status_code, "The service is not ready")


def _database(request: Request):
    return _services(request).mongo_db


# ─────────────────────────────────────────────
# Google clients (one dependency for every route that needs them)
# ─────────────────────────────────────────────
@dataclass
class GoogleClients:
    """The caller's own Gmail/Calendar clients, built per request and never shared between users."""

    gmail: Optional[GmailService] = None
    calendar: Optional[CalendarService] = None
    state: str = "not_connected"  # "not_connected" | "expired" | "connected"


def _build_google_clients(token: dict):
    gmail = calendar = None
    refreshed = None
    try:
        gmail = GmailService()
        refreshed = gmail.authenticate_from_dict(token)
    except GoogleAuthRequiredException as exc:
        logger.warning(f"Gmail not usable for this user: {exc}")
        gmail = None
    try:
        calendar = CalendarService()
        refreshed = calendar.authenticate_from_dict(token) or refreshed
    except GoogleAuthRequiredException as exc:
        logger.warning(f"Calendar not usable for this user: {exc}")
        calendar = None
    return gmail, calendar, refreshed


async def get_google_clients(request: Request, current_user: dict = Depends(get_current_user)) -> GoogleClients:
    # Runs before the route's own error handling, so failures here are raised as HTTP errors.
    try:
        user_model = await UserModel.create_instance(_database(request))
        token = await user_model.get_google_token(current_user["email"])
    except DashboardError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except PyMongoError:
        logger.error("Could not read the Google token", exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The database is temporarily unavailable")
    if not token:
        return GoogleClients(state="not_connected")

    gmail, calendar, refreshed = await asyncio.to_thread(_build_google_clients, token)
    if refreshed and refreshed != token:
        await user_model.update_google_token(current_user["email"], refreshed)
    if gmail is None and calendar is None:
        return GoogleClients(state="expired")
    return GoogleClients(gmail=gmail, calendar=calendar, state="connected")


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


# ─────────────────────────────────────────────
# GET /api/v1/tasks
# ─────────────────────────────────────────────
@dashboard_router.get("/tasks")
@envelope("Tasks loaded successfully", "Failed to load tasks")
async def get_tasks(
    request: Request,
    project_id: str = Query("default", pattern=PROJECT_ID_PATTERN),
    current_user: dict = Depends(get_current_user),
):
    """Pending tasks of the logged-in user."""
    from models.TaskModel import TaskModel
    task_model = await TaskModel.create_instance(db_client=_database(request))
    tasks = await task_model.get_pending_tasks(project_id=project_id, user_id=str(current_user["_id"]), limit=50)
    return {
        "tasks": [
            {
                "id": str(t.task_id) if t.task_id else "",
                "description": t.description,
                "deadline": _iso(t.deadline),
                "priority": t.priority,
                "status": t.status,
                "created_at": _iso(t.created_at),
            }
            for t in tasks
        ]
    }


# ─────────────────────────────────────────────
# GET /api/v1/reminders
# ─────────────────────────────────────────────
@dashboard_router.get("/reminders")
@envelope("Reminders loaded successfully", "Failed to load reminders")
async def get_reminders(
    request: Request,
    project_id: str = Query("default", pattern=PROJECT_ID_PATTERN),
    current_user: dict = Depends(get_current_user),
):
    """Reminders of the logged-in user."""
    from models.ReminderModel import ReminderModel
    reminder_model = await ReminderModel.create_instance(db_client=_database(request))
    reminders = await reminder_model.get_all_reminders(project_id=project_id, user_id=str(current_user["_id"]), limit=50)
    return {
        "reminders": [
            {
                "id": str(r.id) if r.id else "",
                "task": r.task,
                "date": r.date,
                "time": r.time,
                "priority": r.priority,
                "notes": r.notes,
                "is_completed": r.is_completed,
                "created_at": _iso(r.created_at),
            }
            for r in reminders
        ]
    }


# ─────────────────────────────────────────────
# GET /api/v1/calendar/events
# ─────────────────────────────────────────────
@dashboard_router.get("/calendar/events")
@envelope("Calendar events loaded successfully", "Failed to load calendar events")
async def get_calendar_events(
    days_ahead: int = Query(7, ge=1, le=90),
    max_results: int = Query(20, ge=1, le=100),
    google: GoogleClients = Depends(get_google_clients),
):
    """Upcoming Google Calendar events for the logged-in user."""
    if google.state == "not_connected":
        # A normal state, not a failure: there is simply nothing to show yet.
        return {"events": [], "warning": "Connect your Google account to see calendar events"}
    if google.calendar is None:
        # 409, not 401: the sign-in session is fine, only the Google connection needs renewing.
        raise DashboardError(status.HTTP_409_CONFLICT, "Your Google connection has expired. Reconnect your Google account.")

    try:
        events = await asyncio.to_thread(google.calendar.list_events, max_results=max_results, days_ahead=days_ahead)
    except Exception:
        logger.error("Google Calendar request failed", exc_info=True)
        raise DashboardError(status.HTTP_502_BAD_GATEWAY, "Google Calendar is not responding. Try again shortly.")
    return {"events": events}


# ─────────────────────────────────────────────
# GET /api/v1/summary/daily
# ─────────────────────────────────────────────
@dashboard_router.get("/summary/daily")
@envelope("Daily summary generated successfully", "Failed to generate the daily summary")
async def get_daily_summary(
    request: Request,
    project_id: str = Query("default", pattern=PROJECT_ID_PATTERN),
    current_user: dict = Depends(get_current_user),
    google: GoogleClients = Depends(get_google_clients),
):
    """LLM-written briefing from Calendar, Gmail, tasks and reminders.

    A source that cannot be read is named in `warnings` (and in the briefing itself) instead of
    being reported as empty.
    """
    summary_service = DailySummaryService(
        calendar_service=google.calendar,
        gmail_service=google.gmail,
        generation_client=_services(request).generation_client,
        db_client=_database(request),
        timezone_name=current_user.get("timezone"),
    )
    payload = await summary_service.generate(project_id=project_id, user_id=str(current_user["_id"]))
    return {"summary": payload["summary"], "warnings": payload.get("warnings", [])}


# ─────────────────────────────────────────────
# GET /api/v1/health/services
# ─────────────────────────────────────────────
@dashboard_router.get("/health/services")
@envelope("Service health retrieved", "Failed to check service health")
async def health_services(request: Request, google: GoogleClients = Depends(get_google_clients)):
    """Status of the services the UI shows. Google is reported for the logged-in user only."""
    google_ok = "ok" if google.state == "connected" else "error"
    return {
        "database": "ok" if getattr(getattr(request.app.state, "services", None), "mongo_db", None) is not None else "error",
        "gmail": "ok" if google.gmail else "error",
        "calendar": "ok" if google.calendar else "error",
        "google": google_ok,
    }


# ─────────────────────────────────────────────
# GET /api/v1/memory/{thread_id}
# ─────────────────────────────────────────────
@dashboard_router.get("/memory/{thread_id}")
@envelope("Thread memory retrieved", "Failed to retrieve thread memory")
async def get_thread_memory(
    request: Request,
    thread_id: str = Path(pattern=THREAD_ID_PATTERN),
    current_user: dict = Depends(get_current_user),
):
    """Saved chat history of one of the caller's own threads."""
    agent_graph = _services(request).agent_graph

    # Threads are namespaced by user id, exactly as in the agent routes, so only the caller's own
    # history can ever be read.
    config = {"configurable": {"thread_id": f"{str(current_user['_id'])}_{thread_id}"}}
    snapshot = await agent_graph.aget_state(config)
    chat_history = snapshot.values.get("chat_history", []) if snapshot and snapshot.values else []
    return {"thread_id": thread_id, "chat_history": chat_history}


# ─────────────────────────────────────────────
# POST /api/v1/auth/gmail/logout
# ─────────────────────────────────────────────
@dashboard_router.post("/auth/gmail/logout")
@envelope("Gmail logged out successfully", "Failed to log out of Gmail")
async def logout_gmail(request: Request, current_user: dict = Depends(get_current_user)):
    """Disconnect the logged-in user's Google account (clears only their token)."""
    user_model = await UserModel.create_instance(_database(request))
    await user_model.update_google_token(current_user["email"], None)
    return {}


# ─────────────────────────────────────────────
# GET /api/v1/queries
# ─────────────────────────────────────────────
@dashboard_router.get("/queries")
@envelope("Query history fetched", "Failed to fetch query history")
async def get_queries(
    request: Request,
    project_id: str = Query("default", pattern=PROJECT_ID_PATTERN),
    current_user: dict = Depends(get_current_user),
):
    """The logged-in user's recent queries."""
    from models.QueryModel import QueryModel
    query_model = await QueryModel.create_instance(db_client=_database(request))
    queries = await query_model.get_recent_queries(project_id=project_id, user_id=str(current_user["_id"]), limit=50)
    return {
        "queries": [
            {"id": str(q.id) if q.id else "", "query_text": q.query_text, "created_at": _iso(q.created_at)}
            for q in queries
        ]
    }
