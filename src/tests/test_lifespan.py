"""Application startup and shutdown (plan 2.16). Uses a throwaway database on the local MongoDB and is
skipped when none is reachable."""
import asyncio
import shutil
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from helpers.config import get_settings
from helpers.services import Services, services_of

MONGO_URL = "mongodb://localhost:27017/"
SRC = Path(__file__).resolve().parents[1]


def _mongo_available() -> bool:
    import pymongo

    try:
        pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=1000).server_info()
        return True
    except Exception:
        return False


needs_mongo = pytest.mark.skipif(not _mongo_available(), reason="MongoDB is not reachable")


@pytest.fixture
def isolated_settings(monkeypatch):
    """Point the app at a throwaway database and vector store instead of the developer's own."""
    settings = get_settings()
    db_name = f"test_lifespan_{uuid.uuid4().hex[:8]}"
    qdrant_name = f"test_qdrant_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(settings, "MONGODB_URL", MONGO_URL)
    monkeypatch.setattr(settings, "MONGODB_DATABASE", db_name)
    monkeypatch.setattr(settings, "VECTOR_DB_PATH", qdrant_name)
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")  # clients are built but never called
    monkeypatch.setattr(settings, "COHERE_API_KEY", "test-key")
    yield SimpleNamespace(db_name=db_name)

    import pymongo

    pymongo.MongoClient(MONGO_URL).drop_database(db_name)
    shutil.rmtree(SRC / "assets" / "database" / qdrant_name, ignore_errors=True)


# ── no deprecated hooks, configuration is honoured ───────────────────────────
def test_the_app_uses_a_lifespan_not_deprecated_event_hooks():
    assert main.app.router.on_startup == [] and main.app.router.on_shutdown == []
    assert main.app.router.lifespan_context is not None


def test_cors_origins_come_from_the_settings():
    cors = next(m for m in main.app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    assert cors.kwargs["allow_origins"] == get_settings().CORS_ORIGINS


def test_requests_before_the_app_is_ready_get_503_not_a_crash():
    # No `with`: the lifespan has not run, so there is no services container.
    client = TestClient(main.app, raise_server_exceptions=False)
    response = client.post("/api/v1/auth/local/signin", json={"email": "a@example.com", "password": "x" * 9})
    assert response.status_code == 503 and response.json()["detail"] == "The service is not ready"


def test_services_of_returns_the_container_or_503():
    app = FastAPI()
    request = SimpleNamespace(app=app)
    with pytest.raises(Exception) as exc:
        services_of(request)
    assert exc.value.status_code == 503

    app.state.services = SimpleNamespace(mongo_db="db")
    assert services_of(request).mongo_db == "db"


# ── the real startup ─────────────────────────────────────────────────────────
@needs_mongo
def test_startup_builds_everything_with_the_configured_database_and_shutdown_releases_it(isolated_settings):
    with TestClient(main.app) as client:
        services = main.app.state.services
        assert isinstance(services, Services)
        for name in ("mongo_conn", "mongo_db", "db_engine", "db_client", "generation_client", "embedding_client",
                     "vectordb_client", "template_parser", "checkpointer", "agent_graph", "notification_engine", "scheduler"):
            assert getattr(services, name) is not None, name

        # The configured database is used (it was hard-coded to "depi" before) and has its indexes.
        assert services.mongo_db.name == isolated_settings.db_name
        indexes = client.portal.call(lambda: services.mongo_db.users.index_information())
        assert "email_1" in indexes
        assert services.scheduler.running and services.vectordb_client.client is not None

        # And the app serves requests.
        signup = client.post("/api/v1/auth/local/signup", json={"name": "T", "email": "t@example.com", "password": "correct horse 42"})
        assert signup.status_code == 200

    # Shutdown: nothing left running, vector store released, app marked not ready again.
    assert main.app.state.services is None
    assert services.scheduler.running is False
    assert services.vectordb_client.client is None
    assert services.notification_engine._task.done()


# ── startup that fails part-way, shutdown that meets a failing client ────────
class Recorder:
    def __init__(self):
        self.closed = []


def _fake_factories(monkeypatch, recorder, generation_close_fails=False, vector_connect_fails=False):
    class FakeClient:
        def __init__(self, name, fails=False):
            self.name, self.fails = name, fails

        def set_generation_model(self, **kwargs): ...
        def set_embedding_model(self, **kwargs): ...

        async def aclose(self):
            recorder.closed.append(self.name)
            if self.fails:
                raise RuntimeError(f"{self.name} refused to close")

    class FakeLLMFactory:
        def __init__(self, settings): ...

        def create(self, provider):
            name = "generation" if provider == get_settings().GENERATION_BACKEND else "embedding"
            return FakeClient(name, fails=generation_close_fails and name == "generation")

    class FakeVectorDB:
        def connect(self):
            if vector_connect_fails:
                raise RuntimeError("storage folder is locked")
            recorder.closed.append("vectordb-connected")

        def disconnect(self):
            recorder.closed.append("vectordb")

    class FakeVectorFactory:
        def __init__(self, settings): ...

        def create(self, provider):
            return FakeVectorDB()

    monkeypatch.setattr(main, "LLMProviderFactory", FakeLLMFactory)
    monkeypatch.setattr(main, "VectorDBProviderFactory", FakeVectorFactory)


@needs_mongo
def test_a_failed_startup_releases_what_it_had_already_opened(isolated_settings, monkeypatch):
    recorder = Recorder()
    _fake_factories(monkeypatch, recorder)
    def failing_build(*args, **kwargs):
        raise RuntimeError("the graph could not be built")

    monkeypatch.setattr(main, "build_agent_graph", failing_build)
    app = FastAPI()

    async def attempt():
        async with main.lifespan(app):
            pytest.fail("startup should have failed")

    with pytest.raises(RuntimeError, match="graph could not be built"):
        asyncio.run(attempt())

    # Everything opened before the failure was closed again, in reverse order, and no request was served.
    assert recorder.closed == ["vectordb-connected", "vectordb", "embedding", "generation"]
    assert getattr(app.state, "services", None) is None


@needs_mongo
def test_a_failure_while_connecting_the_vector_store_closes_the_earlier_clients(isolated_settings, monkeypatch):
    recorder = Recorder()
    _fake_factories(monkeypatch, recorder, vector_connect_fails=True)
    app = FastAPI()

    async def attempt():
        async with main.lifespan(app):
            pass

    with pytest.raises(RuntimeError, match="locked"):
        asyncio.run(attempt())
    assert recorder.closed == ["embedding", "generation"]


@needs_mongo
def test_shutdown_reaches_every_resource_even_if_one_fails_to_close(isolated_settings, monkeypatch):
    recorder = Recorder()
    _fake_factories(monkeypatch, recorder, generation_close_fails=True)
    app = FastAPI()

    async def run():
        async with main.lifespan(app):
            assert app.state.services is not None

    asyncio.run(run())  # must not raise although the generation client refuses to close

    assert "generation" in recorder.closed and "embedding" in recorder.closed and "vectordb" in recorder.closed
    assert app.state.services is None
