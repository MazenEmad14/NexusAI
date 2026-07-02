import asyncio
import logging
from datetime import datetime, time as datetime_time

from services.daily_summary import DailySummaryService
from services.retrieval import detect_priority

logger = logging.getLogger("uvicorn.error")


class NotificationEngine:
    def __init__(self, app, poll_seconds: int = 60):
        self.app = app
        self.poll_seconds = poll_seconds
        self._task = None
        self._stopped = asyncio.Event()
        self._last_daily_summary_date = None

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
        now = datetime.now()
        if now.time() >= datetime_time(hour=8) and self._last_daily_summary_date != now.date():
            summary_service = DailySummaryService(
                calendar_service=getattr(self.app, "calendar_service", None),
                gmail_service=getattr(self.app, "gmail_service", None),
                generation_client=getattr(self.app, "generation_client", None),
                db_client=getattr(self.app, "mongo_db", None),
            )
            summary = await summary_service.generate(project_id="default")
            logger.info(f"Daily summary ready for notification: {summary['summary'][:120]}")
            self._last_daily_summary_date = now.date()

        gmail_service = getattr(self.app, "gmail_service", None)
        if gmail_service and gmail_service.is_authenticated():
            urgent_emails = gmail_service.search_emails(query="is:unread newer_than:1d", max_results=5)
            for email in urgent_emails:
                priority = detect_priority(f"{email.get('subject', '')} {email.get('snippet', '')}")
                if priority in ("critical", "high"):
                    logger.info(f"Urgent email alert: {email.get('subject', 'No Subject')} [{priority}]")
