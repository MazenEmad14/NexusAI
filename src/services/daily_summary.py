import asyncio
import logging
from datetime import datetime, timedelta

from models.ReminderModel import ReminderModel
from models.TaskModel import TaskModel

logger = logging.getLogger("uvicorn.error")


def _join_or_default(items: list[str], default: str) -> str:
    return "\n".join(items) if items else default


class DailySummaryService:
    def __init__(self, calendar_service=None, gmail_service=None, generation_client=None, db_client=None):
        self.calendar_service = calendar_service
        self.gmail_service = gmail_service
        self.generation_client = generation_client
        self.db_client = db_client

    async def collect(self, project_id: str = "default", user_id: str = "") -> dict:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        events = []
        if self.calendar_service and self.calendar_service.is_authenticated():
            try:
                request = self.calendar_service.service.events().list(
                    calendarId="primary",
                    timeMin=today_start.isoformat() + "Z",
                    timeMax=today_end.isoformat() + "Z",
                    singleEvents=True,
                    orderBy="startTime",
                )
                raw = await asyncio.to_thread(request.execute)
                for event in raw.get("items", []):
                    start = event.get("start", {})
                    events.append(
                        {
                            "event_id": event.get("id", ""),
                            "title": event.get("summary", "Busy"),
                            "start": start.get("dateTime", start.get("date", "")),
                            "link": event.get("htmlLink", ""),
                        }
                    )
            except Exception as exc:
                logger.error(f"Error fetching calendar for summary: {exc}")

        emails = []
        if self.gmail_service and self.gmail_service.is_authenticated():
            try:
                emails = await asyncio.to_thread(self.gmail_service.search_emails, query="is:unread", max_results=5)
            except Exception as exc:
                logger.error(f"Error fetching Gmail for summary: {exc}")

        tasks = []
        reminders = []
        if self.db_client is not None:
            try:
                task_model = await TaskModel.create_instance(self.db_client)
                tasks = await task_model.get_pending_tasks(project_id, user_id, limit=10)
            except Exception as exc:
                logger.error(f"Error fetching tasks for summary: {exc}")

            try:
                reminder_model = await ReminderModel.create_instance(self.db_client)
                reminders = await reminder_model.get_active_reminders(project_id, user_id, limit=10)
            except Exception as exc:
                logger.error(f"Error fetching reminders for summary: {exc}")

        return {
            "events": events,
            "emails": emails,
            "tasks": tasks,
            "reminders": reminders,
        }

    def render_source_text(self, data: dict) -> dict:
        events_text = _join_or_default(
            [f"- {event.get('title', 'Busy')} at {event.get('start', '')}" for event in data["events"]],
            "No meetings today.",
        )
        emails_text = _join_or_default(
            [
                f"- {email.get('subject', 'No Subject')} (From: {email.get('sender', 'Unknown')})"
                for email in data["emails"]
            ],
            "No new emails.",
        )
        tasks_text = _join_or_default(
            [
                f"- {task.description} (Deadline: {task.deadline or 'None'}, Priority: {getattr(task, 'priority', 'medium')})"
                for task in data["tasks"]
            ],
            "No pending tasks.",
        )
        reminders_text = _join_or_default(
            [
                f"- {reminder.task} on {reminder.date} {reminder.time}".strip()
                for reminder in data["reminders"]
            ],
            "No upcoming reminders.",
        )

        return {
            "events_text": events_text,
            "emails_text": emails_text,
            "tasks_text": tasks_text,
            "reminders_text": reminders_text,
        }

    async def generate(self, project_id: str = "default", user_id: str = "", chat_history: list = None) -> dict:
        data = await self.collect(project_id=project_id, user_id=user_id)
        text = self.render_source_text(data)

        prompt = f"""You are Aria, an elite executive assistant providing a daily briefing.
Use the available calendar, email, task, and reminder data. Highlight critical and high priority items first.

Mandatory Response Rules:
1. Be human, natural, and highly friendly. Avoid scripted templates or artificial introductions.
2. Be concise and answer exactly what was asked. Keep simple answers short, and complex answers detailed but without fluff. Make it suitable and direct for voice interaction.
3. NEVER use Emojis.
4. Do not repeat the user's question at the beginning of the response.
5. If an action was taken, confirm it simply and clearly without excessive celebration.
6. If there is no result or an error occurred, state it directly and calmly without over-apologizing or giving complex technical explanations.
7. Always respond in the EXACT same language that the user used (Arabic or English).

[MEETINGS]
{text["events_text"]}

[UNREAD EMAILS]
{text["emails_text"]}

[PENDING TASKS]
{text["tasks_text"]}

[UPCOMING REMINDERS]
{text["reminders_text"]}

Draft a concise morning briefing."""

        summary = ""
        if self.generation_client:
            try:
                response = await self.generation_client.agenerate_text(prompt=prompt,
                    chat_history=list(chat_history or []),
                    max_output_tokens=600,
                    temperature=0.3,
                )
                summary = response.strip() if response else ""
            except Exception as exc:
                logger.error(f"Error generating daily summary: {exc}")

        if not summary:
            summary = (
                f"Daily Summary\n\n"
                f"Meetings:\n{text['events_text']}\n\n"
                f"Emails:\n{text['emails_text']}\n\n"
                f"Tasks:\n{text['tasks_text']}\n\n"
                f"Reminders:\n{text['reminders_text']}"
            )

        return {"summary": summary, "source_data": text}
