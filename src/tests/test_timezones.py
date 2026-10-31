"""Time zone handling (plan 2.6): "today", "tomorrow" and "8am" belong to the user, and times sent to
Google carry a real UTC offset instead of a naive server time with "Z" appended."""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controllers.classifier import ClassifierController
from helpers.exceptions import ExternalServiceError
from helpers.timezones import day_bounds, is_valid_timezone, local_now, resolve_timezone
from services import notification_engine
from services.daily_summary import DailySummaryService
from stores.calendar.CalendarService import CalendarService
from stores.gmail.GmailService import GmailService

# 22:30 UTC on 1 June is already 1:30am on 2 June in Cairo (UTC+3 in summer) but still 6:30pm on
# 1 June in New York (UTC-4): the same instant is a different "today".
INSTANT = datetime(2026, 6, 1, 22, 30, tzinfo=timezone.utc)

# The package re-exports the class under the module's name, so fetch the module itself.
calendar_module = sys.modules[CalendarService.__module__]


# ── helper ───────────────────────────────────────────────────────────────────
def test_the_same_instant_is_a_different_day_in_different_zones():
    cairo_start, cairo_end = day_bounds("Africa/Cairo", INSTANT)
    ny_start, ny_end = day_bounds("America/New_York", INSTANT)

    assert cairo_start.date().isoformat() == "2026-06-02"
    assert ny_start.date().isoformat() == "2026-06-01"
    for start, end in ((cairo_start, cairo_end), (ny_start, ny_end)):
        assert (start.hour, start.minute, start.second) == (0, 0, 0)
        assert start.utcoffset() is not None  # aware, so its ISO form carries the offset
        assert end - start == timedelta(hours=24)
    assert cairo_start.isoformat().endswith("+03:00") and ny_start.isoformat().endswith("-04:00")


def test_a_daylight_saving_day_is_not_assumed_to_be_24_hours():
    spring = day_bounds("America/New_York", datetime(2026, 3, 8, 15, 0, tzinfo=timezone.utc))
    autumn = day_bounds("America/New_York", datetime(2026, 11, 1, 15, 0, tzinfo=timezone.utc))
    to_utc = lambda bounds: (bounds[1].astimezone(timezone.utc) - bounds[0].astimezone(timezone.utc))
    assert to_utc(spring) == timedelta(hours=23)
    assert to_utc(autumn) == timedelta(hours=25)


def test_unknown_or_missing_zones_fall_back_to_the_default_not_to_an_error():
    default = resolve_timezone(None).key
    assert default == "Africa/Cairo"
    for bad in ("Mars/Olympus", "", "../etc/passwd", "x" * 100, 42):
        assert resolve_timezone(bad).key == default
        assert is_valid_timezone(bad) is False
    assert is_valid_timezone("Asia/Kolkata") and resolve_timezone("Asia/Kolkata").key == "Asia/Kolkata"
    assert local_now("Asia/Tokyo").utcoffset() == timedelta(hours=9)


# ── daily summary ────────────────────────────────────────────────────────────
class RecordingCalendar:
    def __init__(self):
        self.kwargs = None
        outer = self

        class Events:
            def list(self, **kwargs):
                outer.kwargs = kwargs
                return SimpleNamespace(execute=lambda: {"items": []})

        self.service = SimpleNamespace(events=lambda: Events())

    def is_authenticated(self):
        return True


def test_the_summary_asks_google_for_the_users_day_with_a_real_offset():
    for zone in ("Africa/Cairo", "America/Los_Angeles", "Asia/Tokyo"):
        calendar = RecordingCalendar()
        service = DailySummaryService(calendar_service=calendar, timezone_name=zone)
        asyncio.run(service.collect())

        time_min, time_max = calendar.kwargs["timeMin"], calendar.kwargs["timeMax"]
        assert not time_min.endswith("Z") and not time_max.endswith("Z"), zone  # the old bug
        start, end = datetime.fromisoformat(time_min), datetime.fromisoformat(time_max)
        assert start.utcoffset() == ZoneInfo(zone).utcoffset(start.replace(tzinfo=None)), zone
        assert (start.hour, start.minute) == (0, 0) and end - start in (timedelta(hours=23), timedelta(hours=24), timedelta(hours=25))


def test_the_briefing_prompt_states_the_users_date_and_zone():
    class LLM:
        prompt = ""

        async def agenerate_text(self, prompt, **kwargs):
            LLM.prompt = prompt
            return "ok"

    service = DailySummaryService(generation_client=LLM(), timezone_name="Asia/Tokyo")
    asyncio.run(service.generate())
    assert "(Asia/Tokyo)" in LLM.prompt and local_now("Asia/Tokyo").strftime("%Y-%m-%d") in LLM.prompt


# ── calendar ─────────────────────────────────────────────────────────────────
def _calendar_with_recording_insert():
    calendar = CalendarService()
    captured = {}

    class Events:
        def insert(self, calendarId, body, sendUpdates):
            captured["body"] = body
            return SimpleNamespace(execute=lambda: {"id": "evt1", "htmlLink": "link"})

    calendar.service = SimpleNamespace(events=lambda: Events())
    return calendar, captured


def test_new_events_are_created_in_the_users_zone_not_a_hard_coded_one():
    calendar, captured = _calendar_with_recording_insert()
    calendar.create_event("Standup", "2026-12-01", "09:00", time_zone="Asia/Tokyo")
    assert captured["body"]["start"]["timeZone"] == captured["body"]["end"]["timeZone"] == "Asia/Tokyo"
    assert captured["body"]["start"]["dateTime"] == "2026-12-01T09:00:00"

    calendar, captured = _calendar_with_recording_insert()
    calendar.create_event("Standup", "2026-12-01", "09:00")  # no zone: the deployment default
    assert captured["body"]["start"]["timeZone"] == "Africa/Cairo"


def test_a_missing_date_means_today_in_the_users_zone(monkeypatch):
    # 22:30 UTC: still the 1st in New York, already the 2nd in Tokyo.
    monkeypatch.setattr(calendar_module, "local_now", lambda zone=None: INSTANT.astimezone(ZoneInfo(zone or "Africa/Cairo")))
    for zone, expected in (("America/New_York", "2026-06-01"), ("Asia/Tokyo", "2026-06-02")):
        calendar, captured = _calendar_with_recording_insert()
        calendar.create_event("Call", "", "10:00", time_zone=zone)
        assert captured["body"]["start"]["dateTime"].startswith(expected), zone


def test_listing_events_uses_utc_with_an_offset_and_reports_failures():
    calendar = CalendarService()
    seen = {}

    class Events:
        def list(self, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(execute=lambda: {"items": [{"id": "1", "summary": "A", "start": {"dateTime": "2026-12-01T09:00:00+02:00"}}]})

    calendar.service = SimpleNamespace(events=lambda: Events())
    events = calendar.list_events(max_results=3, days_ahead=2)

    assert events[0]["title"] == "A"
    start, end = datetime.fromisoformat(seen["timeMin"]), datetime.fromisoformat(seen["timeMax"])
    assert start.utcoffset() == timedelta(0) and end - start == timedelta(days=2)  # offset present, no appended "Z"

    class Failing:
        def list(self, **kwargs):
            raise RuntimeError("quota exceeded")

    calendar.service = SimpleNamespace(events=lambda: Failing())
    with pytest.raises(ExternalServiceError):
        calendar.list_events()  # an empty list must mean "no events", never "Calendar failed"


def test_searching_gmail_reports_failures_instead_of_an_empty_list():
    gmail = GmailService()

    class Messages:
        def list(self, **kwargs):
            raise RuntimeError("backend error")

    gmail.service = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: Messages()))
    with pytest.raises(ExternalServiceError):
        gmail.search_emails("is:unread")


# ── classifier prompt ────────────────────────────────────────────────────────
def test_meeting_extraction_tells_the_model_the_users_current_time_and_zone(monkeypatch):
    import helpers.timezones as tz_module

    monkeypatch.setattr(tz_module, "local_now", lambda zone=None: INSTANT.astimezone(ZoneInfo(zone or "Africa/Cairo")))
    captured = {}

    class FakeLLM:
        enums = SimpleNamespace(SYSTEM=SimpleNamespace(value="system"), USER=SimpleNamespace(value="user"),
                                ASSISTANT=SimpleNamespace(value="assistant"))

        def construct_prompt(self, prompt, role):
            return {"role": role, "content": prompt}

        def generate_text(self, prompt, chat_history=None, **kwargs):
            captured["system"] = chat_history[0]["content"]
            return "{}"

    classifier = ClassifierController(generation_client=FakeLLM())
    classifier.extract_action_details("book a call tomorrow at 5pm", "calendar", timezone_name="America/New_York")
    assert "2026-06-01 18:30 (America/New_York)" in captured["system"]

    classifier.extract_action_details("book a call tomorrow at 5pm", "calendar", timezone_name="Asia/Tokyo")
    assert "2026-06-02 07:30 (Asia/Tokyo)" in captured["system"]


# ── 8am briefing at the user's local 8am ─────────────────────────────────────
def test_the_morning_briefing_goes_out_at_8am_local_time_once_a_day(monkeypatch):
    clock = {"now": datetime(2026, 6, 1, 5, 30, tzinfo=timezone.utc)}  # 08:30 Cairo, 01:30 New York
    monkeypatch.setattr(notification_engine, "local_now", lambda zone=None: clock["now"].astimezone(resolve_timezone(zone)))

    users = [
        {"user_id": "cairo", "email": "c@x.com", "google_token": {"t": 1}, "timezone": "Africa/Cairo"},
        {"user_id": "newyork", "email": "n@x.com", "google_token": {"t": 2}, "timezone": "America/New_York"},
    ]

    class FakeUserModel:
        @classmethod
        async def create_instance(cls, db):
            return cls()

        async def list_google_users(self):
            return users

    monkeypatch.setattr(notification_engine, "UserModel", FakeUserModel)
    started = []

    async def fake_generate(self, user):
        started.append(user["user_id"])

    monkeypatch.setattr(notification_engine.NotificationEngine, "_generate_for_user", fake_generate)

    async def scenario():
        engine = notification_engine.NotificationEngine(SimpleNamespace(mongo_db=object()))

        async def tick():
            await engine.check_once()
            await asyncio.sleep(0)  # let the scheduled tasks run

        await tick()
        assert started == ["cairo"]  # it is only 01:30 in New York

        await tick()
        assert started == ["cairo"]  # not twice on the same local day

        clock["now"] = datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)  # 08:30 in New York
        await tick()
        assert started == ["cairo", "newyork"]

        clock["now"] = datetime(2026, 6, 2, 5, 30, tzinfo=timezone.utc)  # next morning in Cairo
        await tick()
        assert started == ["cairo", "newyork", "cairo"]

    asyncio.run(scenario())
