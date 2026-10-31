"""Dashboard response contract (plan 2.13): honest errors, one envelope, validated input, and a single
shared way of getting the caller's Google clients. No database or network is used."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import PyMongoError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import routes.dashboard as dashboard
from models.QueryModel import QueryModel
from models.ReminderModel import ReminderModel
from models.TaskModel import TaskModel
from routes.dashboard import GoogleClients

USER = {"_id": "u1", "email": "a@example.com"}


class FakeLLM:
    def __init__(self):
        self.prompts = []

    async def agenerate_text(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return "Your briefing."


def build_app(google=None, with_db=True, graph="missing"):
    app = FastAPI()
    app.include_router(dashboard.dashboard_router)
    app.dependency_overrides[dashboard.get_current_user] = lambda: USER
    if google is not None:
        app.dependency_overrides[dashboard.get_google_clients] = lambda: google
    if with_db:  # without it the app is "not ready": no services container at all
        app.state.services = SimpleNamespace(
            mongo_db=MagicMock(),
            generation_client=FakeLLM(),
            agent_graph=None if graph == "missing" else graph,
        )
    return app


def make_client(**kwargs):
    return TestClient(build_app(**kwargs), raise_server_exceptions=False)


def assert_error_envelope(response, status_code):
    assert response.status_code == status_code
    body = response.json()
    assert body["status"] == "error" and body["data"] == {} and body["message"]
    return body["message"]


def task(description="Write report", priority="high"):
    return SimpleNamespace(task_id="t1", description=description, deadline=None, priority=priority, status="pending", created_at=None)


# ── tasks / reminders / queries ──────────────────────────────────────────────
def test_tasks_success_uses_the_standard_envelope(monkeypatch):
    async def pending(self, project_id, user_id, limit=10):
        assert (project_id, user_id) == ("default", "u1")
        return [task()]

    monkeypatch.setattr(TaskModel, "get_pending_tasks", pending)
    response = make_client().get("/api/v1/tasks")

    body = response.json()
    assert response.status_code == 200 and body["status"] == "success" and body["message"]
    assert body["data"]["tasks"][0]["description"] == "Write report"
    assert body["data"]["tasks"][0]["priority"] == "high"


def test_an_empty_list_means_no_tasks_not_a_failure(monkeypatch):
    async def none(self, **kwargs):
        return []

    monkeypatch.setattr(TaskModel, "get_pending_tasks", none)
    body = make_client().get("/api/v1/tasks").json()
    assert body["status"] == "success" and body["data"]["tasks"] == []


def test_a_database_outage_is_a_503_not_an_empty_success(monkeypatch):
    async def down(self, **kwargs):
        raise PyMongoError("connection refused to 10.1.2.3")

    monkeypatch.setattr(TaskModel, "get_pending_tasks", down)
    message = assert_error_envelope(make_client().get("/api/v1/tasks"), 503)
    assert "10.1.2.3" not in message


def test_unexpected_errors_are_500_and_never_leak_internal_text(monkeypatch):
    async def broken(self, **kwargs):
        raise RuntimeError("secret internal detail: /srv/app/db.py line 42")

    monkeypatch.setattr(ReminderModel, "get_all_reminders", broken)
    message = assert_error_envelope(make_client().get("/api/v1/reminders"), 500)
    assert "secret" not in message and "/srv" not in message


def test_a_service_that_is_not_ready_answers_503_in_the_standard_envelope():
    client = make_client(with_db=False)
    for route in ("/api/v1/tasks", "/api/v1/reminders", "/api/v1/queries", "/api/v1/memory/thread-1"):
        assert_error_envelope(client.get(route), 503)


def test_project_ids_are_validated():
    client = make_client(google=GoogleClients(state="not_connected"))
    for bad in ("a b", "x" * 65, "../etc", "a%00b", ""):
        for route in ("/api/v1/tasks", "/api/v1/reminders", "/api/v1/queries", "/api/v1/summary/daily"):
            assert client.get(route, params={"project_id": bad}).status_code == 422, (route, bad)


def test_reminders_and_queries_shapes(monkeypatch):
    async def reminders(self, **kwargs):
        return [SimpleNamespace(id="r1", task="Call", date="2026-12-01", time="10:00", priority="high",
                                notes="", is_completed=False, created_at=None)]

    async def queries(self, **kwargs):
        assert kwargs["user_id"] == "u1"
        return [SimpleNamespace(id="q1", query_text="hello", created_at=None)]

    monkeypatch.setattr(ReminderModel, "get_all_reminders", reminders)
    monkeypatch.setattr(QueryModel, "get_recent_queries", queries)
    client = make_client()
    assert client.get("/api/v1/reminders").json()["data"]["reminders"][0]["priority"] == "high"
    assert client.get("/api/v1/queries").json()["data"]["queries"][0]["query_text"] == "hello"


# ── calendar ─────────────────────────────────────────────────────────────────
def test_calendar_not_connected_is_an_empty_success_with_a_hint():
    body = make_client(google=GoogleClients(state="not_connected")).get("/api/v1/calendar/events").json()
    assert body["status"] == "success" and body["data"]["events"] == []
    assert "Connect your Google account" in body["data"]["warning"]


def test_calendar_with_an_expired_google_token_is_a_409_not_a_login_failure():
    response = make_client(google=GoogleClients(state="expired")).get("/api/v1/calendar/events")
    message = assert_error_envelope(response, 409)  # 401 would make the UI log the user out
    assert "Reconnect" in message


def test_calendar_returns_events_and_passes_the_requested_range():
    seen = {}

    class Calendar:
        def list_events(self, max_results, days_ahead):
            seen.update(max_results=max_results, days_ahead=days_ahead)
            return [{"title": "Standup", "start": "2026-12-01T09:00"}]

    google = GoogleClients(calendar=Calendar(), state="connected")
    body = make_client(google=google).get("/api/v1/calendar/events", params={"days_ahead": 3, "max_results": 5}).json()
    assert body["data"]["events"][0]["title"] == "Standup" and seen == {"max_results": 5, "days_ahead": 3}


def test_calendar_api_failure_is_a_502_without_internal_text():
    class Calendar:
        def list_events(self, **kwargs):
            raise RuntimeError("HttpError 500 secret-token-abc")

    response = make_client(google=GoogleClients(calendar=Calendar(), state="connected")).get("/api/v1/calendar/events")
    assert "secret-token" not in assert_error_envelope(response, 502)


def test_calendar_parameters_are_bounded():
    client = make_client(google=GoogleClients(state="not_connected"))
    for params in ({"days_ahead": 0}, {"days_ahead": 1000}, {"max_results": 0}, {"max_results": 100000}):
        assert client.get("/api/v1/calendar/events", params=params).status_code == 422, params


# ── the shared Google dependency ─────────────────────────────────────────────
def _real_dependency_client(monkeypatch, token=None, error=None, built=None):
    class FakeUserModel:
        @classmethod
        async def create_instance(cls, db):
            return cls()

        async def get_google_token(self, email):
            if error:
                raise error
            return token

        async def update_google_token(self, email, new_token):
            built["saved"] = new_token

    monkeypatch.setattr(dashboard, "UserModel", FakeUserModel)
    return make_client()  # no override: the real get_google_clients runs


def test_dependency_reports_not_connected_without_a_token(monkeypatch):
    body = _real_dependency_client(monkeypatch, token=None).get("/api/v1/calendar/events").json()
    assert body["status"] == "success" and "Connect your Google account" in body["data"]["warning"]


def test_dependency_reports_expired_when_google_rejects_the_token(monkeypatch):
    from helpers.exceptions import GoogleAuthRequiredException

    def rejected(token):
        raise GoogleAuthRequiredException("refresh failed")

    monkeypatch.setattr(dashboard.GmailService, "authenticate_from_dict", lambda self, token: rejected(token))
    monkeypatch.setattr(dashboard.CalendarService, "authenticate_from_dict", lambda self, token: rejected(token))
    response = _real_dependency_client(monkeypatch, token={"refresh_token": "r"}).get("/api/v1/calendar/events")
    assert_error_envelope(response, 409)


def test_dependency_builds_per_request_clients_and_saves_a_refreshed_token(monkeypatch):
    saved = {}
    refreshed = {"refresh_token": "r", "token": "new"}
    monkeypatch.setattr(dashboard.GmailService, "authenticate_from_dict", lambda self, token: refreshed)
    monkeypatch.setattr(dashboard.CalendarService, "authenticate_from_dict", lambda self, token: refreshed)
    monkeypatch.setattr(dashboard.CalendarService, "list_events", lambda self, **kw: [{"title": "x"}])

    client = _real_dependency_client(monkeypatch, token={"refresh_token": "r"}, built=saved)
    assert client.get("/api/v1/calendar/events").json()["data"]["events"] == [{"title": "x"}]
    assert saved["saved"] == refreshed


def test_a_database_outage_while_reading_the_token_is_a_503_not_not_connected(monkeypatch):
    client = _real_dependency_client(monkeypatch, error=PyMongoError("down"))
    response = client.get("/api/v1/calendar/events")
    assert response.status_code == 503 and "temporarily unavailable" in response.json()["detail"]


# ── daily summary ────────────────────────────────────────────────────────────
def test_summary_says_which_sources_are_missing_instead_of_claiming_nothing_is_there(monkeypatch):
    async def pending(self, *args, **kwargs):
        raise PyMongoError("down")

    async def reminders(self, *args, **kwargs):
        return []

    monkeypatch.setattr(TaskModel, "get_pending_tasks", pending)
    monkeypatch.setattr(ReminderModel, "get_active_reminders", reminders)
    app = build_app(google=GoogleClients(state="not_connected"))

    response = TestClient(app).get("/api/v1/summary/daily")
    body = response.json()
    prompt = app.state.services.generation_client.prompts[0]

    assert response.status_code == 200 and body["data"]["summary"] == "Your briefing."
    assert set(body["data"]["warnings"]) == {
        "Calendar is not connected.", "Gmail is not connected.", "Tasks could not be loaded right now.",
    }
    assert "No meetings today" not in prompt and "Calendar is not connected." in prompt
    assert "No pending tasks" not in prompt and "Tasks could not be loaded right now." in prompt
    assert "No upcoming reminders." in prompt  # a source that loaded fine and is empty says so


def test_summary_without_problems_has_no_warnings(monkeypatch):
    async def empty(self, *args, **kwargs):
        return []

    class Service:
        def is_authenticated(self):
            return True

        def search_emails(self, **kwargs):
            return []

        service = SimpleNamespace(events=lambda: SimpleNamespace(list=lambda **kw: SimpleNamespace(execute=lambda: {"items": []})))

    monkeypatch.setattr(TaskModel, "get_pending_tasks", empty)
    monkeypatch.setattr(ReminderModel, "get_active_reminders", empty)
    google = GoogleClients(gmail=Service(), calendar=Service(), state="connected")
    body = TestClient(build_app(google=google)).get("/api/v1/summary/daily").json()
    assert body["data"]["warnings"] == []


# ── health, memory, logout ───────────────────────────────────────────────────
def test_health_reports_per_user_google_status():
    connected = make_client(google=GoogleClients(gmail=object(), calendar=object(), state="connected"))
    assert connected.get("/api/v1/health/services").json()["data"] == {
        "database": "ok", "gmail": "ok", "calendar": "ok", "google": "ok",
    }
    disconnected = make_client(google=GoogleClients(state="not_connected"))
    assert disconnected.get("/api/v1/health/services").json()["data"]["gmail"] == "error"


def test_memory_reads_only_the_callers_namespaced_thread():
    seen = []

    class Graph:
        async def aget_state(self, config):
            seen.append(config["configurable"]["thread_id"])
            return SimpleNamespace(values={"chat_history": [{"role": "user", "content": "hi"}]})

    client = make_client(graph=Graph())
    body = client.get("/api/v1/memory/thread-1").json()
    assert body["data"]["chat_history"] == [{"role": "user", "content": "hi"}]
    assert seen == ["u1_thread-1"]  # another user's thread id can never be addressed
    assert client.get("/api/v1/memory/a%20b").status_code == 422


def test_logout_clears_only_the_callers_token(monkeypatch):
    cleared = []

    class FakeUserModel:
        @classmethod
        async def create_instance(cls, db):
            return cls()

        async def update_google_token(self, email, token):
            cleared.append((email, token))

    monkeypatch.setattr(dashboard, "UserModel", FakeUserModel)
    response = make_client().post("/api/v1/auth/gmail/logout")
    assert response.status_code == 200 and cleared == [("a@example.com", None)]


# ── daily summary service, directly ──────────────────────────────────────────
def test_summary_service_flags_a_calendar_that_fails_to_load(monkeypatch):
    from services.daily_summary import DailySummaryService

    class BrokenCalendar:
        def is_authenticated(self):
            return True

        class service:
            @staticmethod
            def events():
                raise RuntimeError("quota exceeded")

    service = DailySummaryService(calendar_service=BrokenCalendar(), gmail_service=None, generation_client=None, db_client=None)
    payload = asyncio.run(service.generate())
    assert "Calendar could not be loaded right now." in payload["warnings"]
    assert "No meetings today" not in payload["summary"]
