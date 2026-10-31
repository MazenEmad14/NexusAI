"""What the agent expects back from the model for each structured request.

Each class describes one reply. The validators turn the usual sloppiness (nulls, a string where a
list belongs, odd capitalisation) into sensible values instead of failing, so a retry is only spent
on replies that are really unusable.
"""
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

PRIORITIES = ("low", "medium", "high", "critical")
ACTION_TYPES = ("summary", "send_email", "gmail", "calendar", "reminder", "data_analysis")
REQUEST_TYPES = ("action", "retrieval", "web_search", "conversational")


def _text(value: Any, default: str = "") -> str:
    return default if value is None else str(value).strip()


# ── classification ───────────────────────────────────────────────────────────
class Classification(BaseModel):
    request_type: str = "retrieval"
    action_type: str = ""
    priority: Optional[str] = None  # None: the caller works it out from the query

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data):
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        everything = " ".join(str(v) for v in data.values()).lower()

        request_type = _text(data.get("request_type", data.get("category", "retrieval")), "retrieval").lower()
        action_type = _text(data.get("action_type", data.get("subcategory", data.get("type", "")))).lower()

        if action_type not in ACTION_TYPES:
            # Models sometimes put the action in another key or a longer phrase; look through every value.
            action_type = next((a for a in ("send_email", "calendar", "gmail", "data_analysis", "reminder") if a in everything), "")
            if not action_type and "summary" in everything and "action + summary" in everything:
                action_type = "summary"
        if action_type in ACTION_TYPES:
            request_type = "action"
        if request_type not in REQUEST_TYPES:
            request_type = "web_search" if "web_search" in everything else "conversational" if "conversational" in everything else "retrieval"

        priority = _text(data.get("priority")).lower()
        return {"request_type": request_type, "action_type": action_type, "priority": priority if priority in PRIORITIES else None}


# ── details of an action the user asked for ──────────────────────────────────
class EmailDetails(BaseModel):
    recipient: str = ""
    subject: str = ""
    body: str = ""

    @field_validator("recipient", "subject", "body", mode="before")
    @classmethod
    def _blank_if_missing(cls, value):
        return _text(value)


class MeetingDetails(BaseModel):
    action: str = "create"
    title: str = "Meeting"
    date: str = ""
    time: str = ""
    duration_minutes: int = 60
    attendees: list[str] = Field(default_factory=list)
    is_modification: bool = False
    reference_hint: Optional[str] = None

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value):
        return _text(value) or "Meeting"

    @field_validator("action", mode="before")
    @classmethod
    def _action(cls, value):
        return _text(value, "create").lower() or "create"

    @field_validator("date", "time", mode="before")
    @classmethod
    def _unknown_is_blank(cls, value):
        return _text(value)  # the calendar then uses today / 12:00

    @field_validator("duration_minutes", mode="before")
    @classmethod
    def _duration(cls, value):
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 60

    @field_validator("attendees", mode="before")
    @classmethod
    def _attendees(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        return [str(v).strip() for v in value if v is not None and str(v).strip()]

    @field_validator("is_modification", mode="before")
    @classmethod
    def _flag(cls, value):
        return value if isinstance(value, bool) else str(value).strip().lower() == "true"


class ReminderDetails(BaseModel):
    task: str = ""
    date: str = ""
    time: str = ""
    priority: str = "medium"
    notes: str = ""

    @field_validator("task", "date", "time", "notes", mode="before")
    @classmethod
    def _blank_if_missing(cls, value):
        return _text(value)

    @field_validator("priority", mode="before")
    @classmethod
    def _priority(cls, value):
        value = _text(value).lower()
        return value if value in PRIORITIES else "medium"


class FileDetails(BaseModel):
    file_path: str = ""

    @field_validator("file_path", mode="before")
    @classmethod
    def _blank_if_missing(cls, value):
        return _text(value)


# ── what comes out of emails ─────────────────────────────────────────────────
class EmailSummary(BaseModel):
    summary: str = ""
    action_items: list[str] = Field(default_factory=list)
    urgency: str = "medium"

    # The reply is derived from untrusted email text, so size and shape are bounded.
    @field_validator("summary", mode="before")
    @classmethod
    def _summary(cls, value):
        return _text(value)[:1000]

    @field_validator("action_items", mode="before")
    @classmethod
    def _items(cls, value):
        return [str(item)[:300] for item in value[:10]] if isinstance(value, list) else []

    @field_validator("urgency", mode="before")
    @classmethod
    def _urgency(cls, value):
        value = _text(value).lower()
        return value if value in ("high", "medium", "low") else "medium"


class UrgentEmail(BaseModel):
    sender: str = ""
    subject: str = ""
    reason: str = ""


class UrgentVerdict(BaseModel):
    is_urgent: bool = False
    urgent_emails: list[UrgentEmail] = Field(default_factory=list)
    message: str = ""

    @field_validator("is_urgent", mode="before")
    @classmethod
    def _flag(cls, value):
        return value if isinstance(value, bool) else str(value).strip().lower() == "true"

    @field_validator("message", mode="before")
    @classmethod
    def _message(cls, value):
        return _text(value)[:1000]

    @field_validator("urgent_emails", mode="before")
    @classmethod
    def _emails(cls, value):
        return [item for item in value[:10] if isinstance(item, dict)] if isinstance(value, list) else []
