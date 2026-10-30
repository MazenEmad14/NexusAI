import asyncio
import logging
from datetime import time as datetime_time

from helpers.event_bus import publish
from helpers.timezones import local_now
from models.UserModel import UserModel
from services.daily_summary import DailySummaryService
from stores.calendar.CalendarService import CalendarService
from stores.gmail.GmailService import GmailService

logger = logging.getLogger("uvicorn.error")


def _build_clients(token: dict):
    """Per-user Google clients plus a refreshed token dict (if any) for persistence."""
    gmail = calendar = None
    refreshed = None
    try:
        gmail = GmailService()
        refreshed = gmail.authenticate_from_dict(token)
    except Exception:
        gmail = None
    try:
        calendar = CalendarService()
        refreshed = calendar.authenticate_from_dict(token) or refreshed
    except Exception:
        calendar = None
    return gmail, calendar, refreshed


class NotificationEngine:
    """Delivers each user's daily summary once per day, after 08:00 server time."""

    def __init__(self, services, poll_seconds: int = 60):
        self.services = services
        self.poll_seconds = poll_seconds
        self._task = None
        self._stopped = asyncio.Event()
        self._last_summary_date: dict[str, object] = {}

    def start(self):
        if not self._task:
            self._task = asyncio.create_task(self._run())
            logger.info("Notification engine started.")

    async def stop(self):
        self._stopped.set()
        if self._task:
            await self._task

    async def _run(self):
        while not self._stopped.is_set():
            try:
                await self.check_once()
            except Exception as exc:
                logger.error(f"Notification engine check failed: {exc}", exc_info=True)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def check_once(self):
        user_model = await UserModel.create_instance(self.services.mongo_db)
        for user in await user_model.list_google_users():
            user_id = user["user_id"]
            # 08:00 and "today" are the user's own, not the server's.
            now = local_now(user.get("timezone"))
            if now.time() < datetime_time(hour=8) or self._last_summary_date.get(user_id) == now.date():
                continue
            # Mark before launching so a slow summary is not started again on the next tick.
            self._last_summary_date[user_id] = now.date()
            asyncio.create_task(self._generate_for_user(user))

    async def _generate_for_user(self, user: dict):
        user_id = user["user_id"]
        try:
            gmail, calendar, refreshed = await asyncio.to_thread(_build_clients, user["google_token"])
            if refreshed and refreshed != user["google_token"]:
                user_model = await UserModel.create_instance(self.services.mongo_db)
                await user_model.update_google_token(user["email"], refreshed)

            service = DailySummaryService(
                calendar_service=calendar,
                gmail_service=gmail,
                generation_client=getattr(self.services, "generation_client", None),
                db_client=getattr(self.services, "mongo_db", None),
                timezone_name=user.get("timezone"),
            )
            summary = await service.generate(project_id="default", user_id=user_id)
            await publish(user_id, summary["summary"])
            logger.info(f"Daily summary delivered for user {user_id}")
        except Exception as exc:
            # Allow a retry on the next tick.
            self._last_summary_date.pop(user_id, None)
            logger.error(f"Error generating daily summary for user {user_id}: {exc}", exc_info=True)
