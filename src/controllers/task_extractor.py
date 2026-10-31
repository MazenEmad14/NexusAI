from helpers.exceptions import LLMOutputError
from helpers.llm_json import llm_json_sync
from stores.llm.LLMInterface import LLMInterface
import logging
from helpers.prompts import render
import re
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger('uvicorn.error')

_RELATIVE_DAYS = {"today": 0, "tomorrow": 1, "اليوم": 0, "النهاردة": 0, "النهارده": 0,
                  "بكرة": 1, "بكره": 1, "غدا": 1, "غداً": 1}


def parse_deadline(text, now: Optional[datetime] = None) -> Optional[tuple[datetime, bool]]:
    """Parse an extracted deadline into (datetime, has_time), or None if it cannot be resolved.

    Accepts "YYYY-MM-DD HH:MM", "YYYY-MM-DD", today/tomorrow and "in N days" (English and Arabic).
    """
    if not text:
        return None
    value = str(text).strip()
    for fmt, has_time in (("%Y-%m-%d %H:%M", True), ("%Y-%m-%d", False)):
        try:
            return datetime.strptime(value, fmt), has_time
        except ValueError:
            pass

    now = now or datetime.now()
    lowered = value.lower()
    if lowered in _RELATIVE_DAYS:
        return (now + timedelta(days=_RELATIVE_DAYS[lowered])).replace(hour=0, minute=0, second=0, microsecond=0), False
    match = re.fullmatch(r"in (\d{1,3}) days?", lowered)
    if match:
        return (now + timedelta(days=int(match.group(1)))).replace(hour=0, minute=0, second=0, microsecond=0), False
    return None

class TaskExtractorController:
    def __init__(self, generation_client: LLMInterface):
        self.generation_client = generation_client

    def extract_tasks(self, text: str) -> list[dict]:
        """
        Extract tasks and deadlines from summarized emails or text.
        Returns a list of dicts with description, deadline, and priority.
        """
        system_prompt = render("tasks", "extract_system")
        try:
            chat_history = [
                self.generation_client.construct_prompt(
                    prompt=system_prompt,
                    role=self.generation_client.enums.SYSTEM.value,
                )
            ]

            reply = llm_json_sync(
                self.generation_client,
                render("tasks", "extract_user", text=text),
                list[dict] | dict,
                chat_history=chat_history,
                max_output_tokens=300,
                temperature=0.0,
            )
        except LLMOutputError as exc:
            logger.error(f"Task extraction reply unusable: {exc}")
            return []
        except Exception as e:
            logger.error(f"Error extracting tasks: {e}")
            return []

        if isinstance(reply, dict):  # {"tasks": [...]} instead of a bare array
            reply = next((value for value in reply.values() if isinstance(value, list)), [])
        return self._sanitize(reply)

    @staticmethod
    def _sanitize(tasks: list) -> list[dict]:
        """Bound size and shape of tasks derived from untrusted email text before they are stored."""
        clean = []
        for task in tasks[:10]:
            if not isinstance(task, dict):
                continue
            description = str(task.get("description") or "").strip()[:200]
            if not description:
                continue
            deadline = task.get("deadline")
            priority = task.get("priority")
            clean.append({
                "description": description,
                "deadline": str(deadline).strip()[:40] if deadline else None,
                "priority": priority if priority in ("low", "medium", "high", "critical") else "medium",
            })
        return clean
