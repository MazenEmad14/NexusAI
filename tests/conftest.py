"""
conftest.py — Shared pytest fixtures and configuration for the NexusAI E2E test suite.

Provides:
  - Environment variable bootstrapping (BEFORE any imports from src/).
  - A fully mocked FastAPI TestClient that bypasses all external I/O.
  - A valid JWT token for an in-memory "test user".
  - A helper to assert graph routing signals on chat responses.
"""

import os
import sys

# ── 1. Patch environment variables BEFORE any src/ imports ──────────────────
#    This ensures pydantic_settings.Settings() doesn't fail on missing env vars.
os.environ.setdefault("APP_NAME", "NexusAI-Test")
os.environ.setdefault("APP_VERSION", "1.0.0")
os.environ.setdefault("FILE_ALLOWED_TYPES", '["pdf","txt","csv"]')
os.environ.setdefault("FILE_MAX_SIZE", "10485760")
os.environ.setdefault("FILE_DEFAULT_CHUNK_SIZE", "512")

os.environ.setdefault("JWT_SECRET_KEY", "e2e-test-super-secret-key-do-not-use-in-prod")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "60")

os.environ.setdefault("MONGODB_URL", "mongodb://localhost:27017/")
os.environ.setdefault("MONGODB_DATABASE", "depi_test")

# LLM / VectorDB backends — use "openai" so LLMProviderFactory.create() doesn't crash;
# we will mock the returned client entirely.
os.environ.setdefault("GENERATION_BACKEND", "openai")
os.environ.setdefault("EMBEDDING_BACKEND", "openai")
os.environ.setdefault("VECTOR_DB_BACKEND", "qdrant")
os.environ.setdefault("VECTOR_DB_PATH", ":memory:")
os.environ.setdefault("VECTOR_DB_DISTANCE_METHOD", "Cosine")

os.environ.setdefault("OPENAI_API_KEY", "sk-test-fake-key")
os.environ.setdefault("COHERE_API_KEY", "cohere-test-fake-key")
os.environ.setdefault("GROQ_API_KEY", "gsk_fake_groq_key")
os.environ.setdefault("GEMINI_API_KEY", "fake-gemini-key")

os.environ.setdefault("GENERATION_MODEL_ID", "gpt-4o-mini")
os.environ.setdefault("EMBEDDING_MODEL_ID", "text-embedding-3-small")
os.environ.setdefault("EMBEDDING_MODEL_SIZE", "1536")
os.environ.setdefault("INPUT_DAFAULT_MAX_CHARACTERS", "4096")
os.environ.setdefault("GENERATION_DAFAULT_MAX_TOKENS", "512")
os.environ.setdefault("GENERATION_DAFAULT_TEMPERATURE", "0.7")

os.environ.setdefault("PRIMARY_LANG", "en")
os.environ.setdefault("DEFAULT_LANG", "en")

# ── 2. Ensure src/ is on sys.path ───────────────────────────────────────────
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import stores.gmail.GmailService
import stores.calendar.CalendarService

# ── 3. Standard library imports ─────────────────────────────────────────────
import uuid
import json
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from bson import ObjectId

import pytest
from fastapi.testclient import TestClient

# ── 4. Internal helpers (safe to import after env setup) ─────────────────────
from helpers import auth_utils


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

TEST_USER_ID   = str(ObjectId())
TEST_USER_EMAIL = "nexusai_tester@example.com"
TEST_USER_NAME  = "NexusAI Tester"
TEST_USER_HASH  = auth_utils.get_password_hash("SecurePass123!")

MOCK_USER_DOC = {
    "_id": ObjectId(TEST_USER_ID),
    "email": TEST_USER_EMAIL,
    "name": TEST_USER_NAME,
    "password_hash": TEST_USER_HASH,
    "is_google_connected": False,
}


# ─────────────────────────────────────────────────────────────────────────────
# MOCK LLM CLIENT FACTORY
# ─────────────────────────────────────────────────────────────────────────────

def _build_mock_llm():
    """
    Returns a MagicMock that simulates the generation_client interface.

    generate_text() inspects the `prompt` argument and returns a deterministic
    JSON string that the ClassifierController / node functions expect.
    """
    mock_llm = MagicMock()
    mock_llm.embedding_size = 1536

    # Enum stubs used by ClassifierController.construct_prompt()
    mock_llm.enums = MagicMock()
    mock_llm.enums.SYSTEM.value = "system"
    mock_llm.enums.USER.value   = "user"
    mock_llm.enums.ASSISTANT.value = "assistant"

    mock_llm.construct_prompt.side_effect = lambda prompt, role: {
        "role": role, "content": prompt
    }

    def _generate(prompt="", chat_history=None, max_output_tokens=512,
                  temperature=0.7, **kwargs):
        p = prompt.lower()

        # ── Intent Classification ──────────────────────────────────────────
        if "classify this query:" in p:
            if "world cup" in p or "general knowledge" in p:
                return '{"request_type": "web_search", "action_type": "", "priority": "low"}'
            if "main topic of the document" in p or "uploaded" in p:
                return '{"request_type": "retrieval", "action_type": "", "priority": "medium"}'
            if "unread emails" in p or "last 3" in p and "email" in p:
                return '{"request_type": "action", "action_type": "gmail", "priority": "medium"}'
            if "send an email" in p or "email to ahmed" in p:
                return '{"request_type": "action", "action_type": "send_email", "priority": "medium"}'
            if "schedule a meeting" in p or "dev team" in p:
                return '{"request_type": "action", "action_type": "calendar", "priority": "medium"}'
            if "analyze" in p and ("csv" in p or "sales data" in p or "highest month" in p):
                return '{"request_type": "action", "action_type": "data_analysis", "priority": "medium"}'
            if "why do you think" in p or "follow-up" in p:
                return '{"request_type": "action", "action_type": "data_analysis", "priority": "low"}'
            # Default safe fallback
            return '{"request_type": "web_search", "action_type": "", "priority": "low"}'

        # ── Gmail Query Converter ──────────────────────────────────────────
        if "gmail search operator" in p or "convert the user" in p:
            if "unread" in p:
                return "is:unread"
            return ""

        # ── Email Detail Extraction ────────────────────────────────────────
        if "extract email details" in p or "recipient" in p and "subject" in p:
            return json.dumps({
                "recipient": "ahmed@example.com",
                "subject": "Running Late",
                "body": "Hi Ahmed, I wanted to let you know I will be late. Sorry for the inconvenience."
            })

        # ── Calendar Event Extraction ──────────────────────────────────────
        if "meeting/calendar event" in p or "extract meeting" in p or "calendar event details" in p:
            return json.dumps({
                "action": "create",
                "title": "Dev Team Meeting",
                "date": "2026-07-09",
                "time": "17:00",
                "duration_minutes": 60,
                "attendees": [],
                "is_modification": False,
                "reference_hint": None
            })

        # ── KB / RAG Answer Generation ─────────────────────────────────────
        if "only using the provided context blocks" in p:
            return "The main topic of the document is test automation and CI/CD pipelines."

        # ── Task Extraction ────────────────────────────────────────────────
        if "text to extract from" in p:
            return json.dumps([{
                "description": "Reply to the sprint planning invite",
                "deadline": "2026-07-10 10:00",
                "priority": "medium"
            }])

        # ── Email Summarizer ───────────────────────────────────────────────
        if "summarize this email" in p:
            return json.dumps({
                "summary": "Summarized 3 unread emails about project updates.",
                "action_items": ["Reply to the sprint planning invite"],
                "urgency": "medium"
            })

        # ── Web Search Answer ──────────────────────────────────────────────
        if "web search agent" in p or ("search snippets" in p and "world cup" in p):
            return "Argentina won the 2022 FIFA World Cup by defeating France in the final."

        # ── Data Analysis Followup ─────────────────────────────────────────
        if "why" in p and ("high" in p or "highest" in p):
            return "Based on the previous analysis, the month performed best due to strong seasonal demand."

        # ── Generic LLM fallback ───────────────────────────────────────────
        return "I processed your request successfully."

    mock_llm.generate_text.side_effect = _generate
    mock_llm.embed_text.return_value = [0.01] * 1536
    return mock_llm


def _build_mock_vectordb():
    """Returns a MagicMock that simulates a Qdrant / vector DB client."""
    mock_vdb = MagicMock()
    mock_vdb.connect.return_value = True
    mock_vdb.disconnect.return_value = True

    mock_result = MagicMock()
    mock_result.text  = "The main topic of the document is test automation and CI/CD pipelines."
    mock_result.score = 0.97
    mock_vdb.search_by_vector.return_value = [mock_result]
    mock_vdb.get_collection_info.return_value = MagicMock(vectors_count=42)
    return mock_vdb


def _build_mock_gmail():
    """Returns a MagicMock simulating GmailService (read-only, no real OAuth)."""
    mock_gmail = MagicMock()
    mock_gmail.is_authenticated.return_value = True
    mock_gmail.authenticate.return_value = True
    mock_gmail.search_emails.return_value = [
        {
            "id": "msg_001",
            "subject": "Sprint Planning",
            "snippet": "Let's discuss the sprint planning for next week.",
            "body": "Hi team, let's discuss the sprint planning for next week. Please be prepared.",
            "date": "2026-07-07",
            "from": "manager@company.com",
            "unread": True,
        },
        {
            "id": "msg_002",
            "subject": "Deployment Update",
            "snippet": "The deployment to staging was successful.",
            "body": "The deployment to staging was successful. Please verify your feature.",
            "date": "2026-07-07",
            "from": "devops@company.com",
            "unread": True,
        },
        {
            "id": "msg_003",
            "subject": "Invoice #12345",
            "snippet": "Please find attached the invoice for July.",
            "body": "Please find attached the invoice for July.",
            "date": "2026-07-06",
            "from": "billing@vendor.com",
            "unread": True,
        },
    ]
    mock_gmail.send_email.return_value = {
        "message_id": "sent_msg_001",
        "thread_id": "thread_001",
        "status": "sent",
    }
    return mock_gmail


def _build_mock_calendar():
    """Returns a MagicMock simulating CalendarService."""
    mock_cal = MagicMock()
    mock_cal.is_authenticated.return_value = True
    mock_cal.authenticate.return_value = True
    mock_cal.list_events.return_value = [
        {"title": "Team Standup", "start": "2026-07-09T09:00:00"},
        {"title": "Product Review", "start": "2026-07-10T14:00:00"},
    ]
    mock_cal.create_event.return_value = {
        "event_id": "cal_evt_001",
        "title": "Dev Team Meeting",
        "link": "https://calendar.google.com/event?eid=cal_evt_001",
    }
    return mock_cal


def _build_mock_user_model(user_doc: dict):
    """Returns a coroutine-based mock for UserModel."""
    mock_instance = AsyncMock()
    mock_instance.get_user_by_email.return_value = user_doc
    mock_instance.create_user.return_value = True
    mock_instance.update_google_auth_status.return_value = True
    return mock_instance


# ─────────────────────────────────────────────────────────────────────────────
# APPLICATION FIXTURE
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def patched_app():
    """
    Session-scoped fixture that assembles the FastAPI application with ALL
    external dependencies replaced by deterministic mocks:

      - MongoDB (motor) → AsyncMock
      - LLM provider (OpenAI/Cohere/Gemini…) → _build_mock_llm()
      - Vector DB (Qdrant/Chroma…) → _build_mock_vectordb()
      - GmailService → _build_mock_gmail()
      - CalendarService → _build_mock_calendar()
      - APScheduler & NotificationEngine → no-ops
      - get_current_user dependency → returns MOCK_USER_DOC directly

    The returned object is the FastAPI `app` instance with `.agent_graph`,
    `.gmail_service`, and `.calendar_service` attributes already set.
    """
    mock_llm    = _build_mock_llm()
    mock_vdb    = _build_mock_vectordb()
    mock_gmail  = _build_mock_gmail()
    mock_cal    = _build_mock_calendar()

    # Patch heavy startup dependencies
    mock_collection = MagicMock()
    mock_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="mock_id"))
    mock_collection.update_one = AsyncMock(return_value=True)
    mock_collection.create_index = AsyncMock(return_value=True)
    
    mock_mongo_db = MagicMock()
    mock_mongo_db.list_collection_names = AsyncMock(return_value=[])
    mock_mongo_db.__getitem__.return_value = mock_collection
    mock_mongo_db.tasks = mock_collection
    mock_mongo_db.users = mock_collection
    
    mock_mongo_client  = MagicMock()
    mock_mongo_client.__getitem__.return_value = mock_mongo_db

    mock_user_model_instance = _build_mock_user_model(MOCK_USER_DOC)

    # Patch 'get_settings' cache so every call inside src/ gets a valid object
    from helpers.config import get_settings
    real_settings = get_settings.cache_clear()   # clear any stale cache

    with \
        patch("motor.motor_asyncio.AsyncIOMotorClient",
              return_value=mock_mongo_client), \
        patch("stores.llm.LLMProviderFactory.LLMProviderFactory.create",
              return_value=mock_llm), \
        patch("stores.vectordb.VectorDBProviderFactory.VectorDBProviderFactory.create",
              return_value=mock_vdb), \
        patch.object(sys.modules["stores.gmail.GmailService"], "GmailService",
              return_value=mock_gmail), \
        patch.object(sys.modules["stores.calendar.CalendarService"], "CalendarService",
              return_value=mock_cal), \
        patch("apscheduler.schedulers.asyncio.AsyncIOScheduler.start"), \
        patch("apscheduler.schedulers.asyncio.AsyncIOScheduler.shutdown"), \
        patch("apscheduler.schedulers.asyncio.AsyncIOScheduler.add_job"), \
        patch("services.notification_engine.NotificationEngine.start"), \
        patch("services.notification_engine.NotificationEngine.stop",
              new_callable=AsyncMock), \
        patch("models.UserModel.UserModel.create_instance",
              new_callable=AsyncMock,
              return_value=mock_user_model_instance):

        # Import AFTER patches are active so module-level startup code is mocked
        import importlib
        import src.main as main_module
        importlib.reload(main_module)

        _app = main_module.app

        # Inject explicit service references so tests can access them
        _app.state.mock_llm    = mock_llm
        _app.state.mock_vdb    = mock_vdb
        _app.state.mock_gmail  = mock_gmail
        _app.state.mock_cal    = mock_cal
        _app.state.mock_user   = MOCK_USER_DOC

        yield _app


@pytest.fixture(scope="session")
def valid_token():
    """Generate a valid JWT for the test user (no DB required)."""
    return auth_utils.create_access_token({"sub": TEST_USER_EMAIL})


@pytest.fixture(scope="session")
def auth_headers(valid_token):
    """Authorization header dict ready to pass to TestClient."""
    return {"Authorization": f"Bearer {valid_token}"}


@pytest.fixture(scope="session")
def client(patched_app):
    """
    Session-scoped TestClient.  Uses lifespan context so startup/shutdown
    hooks run exactly once across the entire test session.
    """
    with TestClient(patched_app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def thread_id():
    """Fresh UUID-based thread ID per test to ensure isolated graph state."""
    return f"e2e_thread_{uuid.uuid4().hex}"
