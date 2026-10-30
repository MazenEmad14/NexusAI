import sys
from pathlib import Path

import importlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class DummyTaskModel:
    @classmethod
    async def create_instance(cls, db_client):
        return cls()

    async def get_pending_tasks(self, project_id, limit=10):
        return []


class DummyReminderModel:
    @classmethod
    async def create_instance(cls, db_client):
        return cls()

    async def get_all_reminders(self, project_id, limit=50):
        return []


class DummyQueryModel:
    @classmethod
    async def create_instance(cls, db_client):
        return cls()

    async def get_recent_queries(self, project_id, limit=10):
        return []


def _build_client(monkeypatch):
    import routes.dashboard as dashboard_module

    monkeypatch.setattr(dashboard_module, "TaskModel", DummyTaskModel, raising=False)
    monkeypatch.setattr(dashboard_module, "ReminderModel", DummyReminderModel, raising=False)
    monkeypatch.setattr(dashboard_module, "QueryModel", DummyQueryModel, raising=False)

    class DummyUserModel:
        @classmethod
        async def create_instance(cls, db_client):
            return cls()

        async def get_google_token(self, email):
            return None

    monkeypatch.setattr(dashboard_module, "UserModel", DummyUserModel)

    app = FastAPI()
    app.include_router(dashboard_module.dashboard_router)
    app.dependency_overrides[dashboard_module.get_current_user] = lambda: {"_id": "u1", "email": "a@example.com"}
    app.mongo_db = object()
    app.generation_client = None
    app.agent_graph = None
    return TestClient(app)


def test_tasks_endpoint_returns_standard_payload(monkeypatch):
    client = _build_client(monkeypatch)

    response = client.get("/api/v1/tasks")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["tasks"] == []
    assert payload["message"]


def test_health_services_endpoint_reports_status(monkeypatch):
    client = _build_client(monkeypatch)

    response = client.get("/api/v1/health/services")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["database"] in {"ok", "error"}
