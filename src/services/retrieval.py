import asyncio
from datetime import datetime, timezone

from pydantic import BaseModel


PRIORITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def detect_priority(text: str = "") -> str:
    lowered = (text or "").lower()
    if any(word in lowered for word in ("critical", "asap", "immediately", "urgent", "blocker")):
        return "critical"
    if any(word in lowered for word in ("high priority", "important", "deadline", "by tomorrow", "by friday")):
        return "high"
    if any(word in lowered for word in ("low priority", "whenever", "no rush")):
        return "low"
    return "medium"


def _recency_score(value) -> float:
    if not value:
        return 0.0
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is not None:  # compare in UTC; dropping the offset would shift the age by hours
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception as e:
            import logging
            logging.getLogger("uvicorn.error").warning(f"Error parsing date for recency score: {e}", exc_info=True)
            return 0.2
    age_days = max((datetime.utcnow() - dt).days, 0)
    return max(0.0, 1.0 - min(age_days, 30) / 30)


def rerank_results(results: list[dict], limit: int = 7) -> list[dict]:
    for item in results:
        priority = item.get("priority") or detect_priority(item.get("text", ""))
        item["priority"] = priority
        relevance = float(item.get("relevance", item.get("score", 0.5)) or 0.0)
        confidence = float(item.get("source_confidence", 0.5))
        priority_score = PRIORITY_WEIGHT.get(priority, 2) / 4
        recency = _recency_score(item.get("date") or item.get("created_at") or item.get("deadline"))
        
        # Boost structured sources over generic text
        source_boost = 0.5 if item.get("source") != "knowledge_base" else 0.0
        
        item["rank_score"] = round(
            (relevance * 0.45) + (recency * 0.2) + (priority_score * 0.25) + (confidence * 0.1) + source_boost,
            4,
        )
    return sorted(results, key=lambda result: result["rank_score"], reverse=True)[:limit]


async def maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value


# ── source selection ─────────────────────────────────────────────────────────
# The retrieval node searches the knowledge base and, only when the question asks
# for them, the user's tasks, reminders or calendar. Gmail has its own route.
_SOURCE_KEYWORDS = {
    "tasks": ("task", "todo", "to-do", "to do", "مهمة", "مهام", "المهام"),
    "reminders": ("remind", "تذكير", "ذكرني", "تذكيرات"),
    "calendar": ("meeting", "calendar", "schedule", "event", "appointment", "agenda",
                 "اجتماع", "موعد", "ميعاد", "جدول"),
}


def choose_sources(query: str) -> list[str]:
    lowered = (query or "").lower()
    sources = ["knowledge_base"]
    for name, words in _SOURCE_KEYWORDS.items():
        if any(word in lowered for word in words):
            sources.append(name)
    return sources


# ── structured answer ────────────────────────────────────────────────────────

class KBAnswer(BaseModel):
    found: bool
    answer: str = ""
