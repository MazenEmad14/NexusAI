"""Request ids, structured logs, redaction, health endpoints and logs free of personal data (plan 3.8)."""
import ast
import asyncio
import json
import logging
import shutil
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from agent import AgentDeps, build_agent_graph, create_initial_state
from agent.nodes import gmail as gmail_node, retrieval as retrieval_node
from helpers.auth_utils import get_current_user
from helpers.config import get_settings
from helpers.error_handlers import install_error_handlers
from helpers.exceptions import LLMUnavailable
from helpers.logging_setup import (
    AccessLogRedactor, ContextFilter, JsonFormatter, RequestContextMiddleware, configure_logging, redact_secrets,
    request_id_var, user_id_var,
)
from helpers.privacy import mask_email, redact_text
from routes import health as health_routes

SRC = Path(__file__).resolve().parents[1]
MONGO_URL = "mongodb://localhost:27017/"


# ── privacy helpers ──────────────────────────────────────────────────────────
def test_redacted_text_shows_size_and_fingerprint_but_never_the_text():
    redacted = redact_text("my password is hunter2")
    assert "hunter2" not in redacted and "22 chars" in redacted
    assert redact_text("my password is hunter2") == redacted  # stable, so two log lines can be matched
    assert redact_text("something else") != redacted
    assert redact_text("") == "<empty>" and redact_text(None) == "<empty>"


def test_emails_are_masked_to_the_domain():
    assert mask_email("alice@example.com") == "a***@example.com"
    assert "alice" not in mask_email("alice@example.com")
    assert mask_email(None) == mask_email("not-an-address") == "<no address>"


# ── secrets in URLs ──────────────────────────────────────────────────────────
def test_secrets_in_query_strings_are_redacted_but_other_parameters_are_kept():
    line = "GET /api/v1/auth/google/callback?code=4/0AbCdEf&state=xyz123&scope=email HTTP/1.1"
    assert redact_secrets(line) == "GET /api/v1/auth/google/callback?code=[redacted]&state=[redacted]&scope=email HTTP/1.1"
    assert redact_secrets("/api/v1/agent/notifications?ticket=abc_DEF-123") == "/api/v1/agent/notifications?ticket=[redacted]"
    assert redact_secrets("/api/v1/tasks?project_id=default") == "/api/v1/tasks?project_id=default"


def test_the_access_log_filter_redacts_uvicorn_style_arguments():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:5000", "GET", "/notifications?ticket=SECRETTICKET", "1.1", 200), None)
    AccessLogRedactor().filter(record)
    assert "SECRETTICKET" not in record.getMessage() and "ticket=[redacted]" in record.getMessage()


# ── formatting ───────────────────────────────────────────────────────────────
def _record(message="hello", **extra):
    record = logging.LogRecord("uvicorn.error", logging.WARNING, "", 0, message, None, None)
    record.__dict__.update({"request_id": "r-1234567890", "user_id": "u1", **extra})
    return record


def test_json_lines_are_valid_json_with_the_request_context():
    line = JsonFormatter().format(_record("مرحبا"))
    entry = json.loads(line)
    assert entry["message"] == "مرحبا" and "\n" not in line  # Arabic is kept readable, one object per line
    assert entry["request_id"] == "r-1234567890" and entry["user_id"] == "u1" and entry["level"] == "WARNING"
    assert entry["ts"].endswith("+00:00")


def test_json_lines_include_the_traceback_of_an_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record("failed")
        record.exc_info = sys.exc_info()
    assert "ValueError: boom" in json.loads(JsonFormatter().format(record))["exception"]


def test_configure_logging_is_idempotent_and_installs_the_context_on_uvicorns_handlers():
    uvicorn_logger = logging.getLogger("uvicorn")
    handler = logging.StreamHandler()
    uvicorn_logger.addHandler(handler)
    try:
        configure_logging("text")
        configure_logging("json")
        configure_logging("json")
        assert sum(isinstance(f, ContextFilter) for f in handler.filters) == 1  # not stacked on every call
        assert isinstance(handler.formatter, JsonFormatter)
    finally:
        uvicorn_logger.removeHandler(handler)
        root = logging.getLogger()
        for h in [h for h in root.handlers if getattr(h, "_app_handler", False)]:
            root.removeHandler(h)


# ── request ids ──────────────────────────────────────────────────────────────
def _app_with_context():
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    seen = {}

    @app.get("/who")
    async def who():
        record = _record()
        ContextFilter().filter(record)
        seen["log_request_id"] = record.request_id
        return {"request_id": request_id_var.get()}

    @app.get("/boom")
    async def boom():
        raise LLMUnavailable("down", reason="unreachable")

    @app.get("/stream")
    async def stream():
        async def chunks():
            for part in ("a", "b", "c"):
                record = _record()
                ContextFilter().filter(record)
                seen.setdefault("stream_ids", set()).add(record.request_id)
                yield part

        return StreamingResponse(chunks(), media_type="text/plain")

    return TestClient(app, raise_server_exceptions=False), seen


def test_every_response_carries_a_request_id_and_log_lines_share_it():
    client, seen = _app_with_context()
    response = client.get("/who")
    request_id = response.headers["x-request-id"]
    assert len(request_id) == 16 and response.json()["request_id"] == request_id
    assert seen["log_request_id"] == request_id  # what the handler's log lines were stamped with
    assert client.get("/who").headers["x-request-id"] != request_id  # one per request


def test_a_valid_incoming_request_id_is_kept_and_a_hostile_one_is_replaced():
    client, _ = _app_with_context()
    assert client.get("/who", headers={"X-Request-ID": "frontend-abc-12345"}).headers["x-request-id"] == "frontend-abc-12345"
    for bad in ("short", "has spaces in it!!", "x" * 100, "inject;{evil}"):
        issued = client.get("/who", headers={"X-Request-ID": bad}).headers["x-request-id"]
        assert issued != bad and len(issued) == 16


def test_errors_tell_the_user_the_request_id():
    client, _ = _app_with_context()
    response = client.get("/boom")
    assert response.status_code == 503
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_streaming_responses_keep_their_id_throughout():
    client, seen = _app_with_context()
    response = client.get("/stream")
    assert response.text == "abc" and seen["stream_ids"] == {response.headers["x-request-id"]}


def test_the_context_does_not_leak_between_requests():
    client, _ = _app_with_context()
    client.get("/who")
    assert request_id_var.get() == "" and user_id_var.get() == ""


def test_the_signed_in_user_is_added_to_the_logs(monkeypatch):
    from helpers.auth_utils import create_access_token
    from models.UserModel import UserModel

    async def find(self, email):
        return {"_id": "user-42", "email": email}

    monkeypatch.setattr(UserModel, "get_user_by_email", find)
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    app.state.services = SimpleNamespace(mongo_db=MagicMock())

    @app.get("/me")
    async def me(user: dict = Depends(get_current_user)):
        record = _record()
        ContextFilter().filter(record)
        return {"logged_as": record.user_id}

    token = create_access_token({"sub": "a@example.com"})
    assert TestClient(app).get("/me", headers={"Authorization": f"Bearer {token}"}).json() == {"logged_as": "user-42"}


def test_the_app_is_wired_for_request_ids():
    assert any(m.cls is RequestContextMiddleware for m in main.app.user_middleware)
    cors = next(m for m in main.app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    assert "X-Request-ID" in cors.kwargs["expose_headers"]


# ── health ───────────────────────────────────────────────────────────────────
def _health_client(**services):
    app = FastAPI()
    app.include_router(health_routes.health_router)
    if services:
        app.state.services = SimpleNamespace(**services)
    return TestClient(app)


class FakeMongo:
    def __init__(self, error=None, delay=0):
        self.error, self.delay = error, delay

    async def command(self, name):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return {"ok": 1}


class FakeVector:
    def __init__(self, error=None, delay=0):
        self.error, self.delay = error, delay

    def list_all_collections(self):
        import time

        time.sleep(self.delay)
        if self.error:
            raise self.error
        return []


def test_liveness_touches_nothing_and_always_answers():
    assert _health_client().get("/health").json() == {"status": "ok"}  # no services at all


def test_readiness_is_ok_when_everything_responds():
    response = _health_client(mongo_db=FakeMongo(), vectordb_client=FakeVector()).get("/health/ready")
    body = response.json()
    assert response.status_code == 200 and body["status"] == "ready" and body["version"] == get_settings().APP_VERSION
    assert body["checks"] == {"mongodb": "ok", "vector_db": "ok", "configuration": "ok"}


def test_readiness_is_503_and_names_what_failed_without_leaking_why():
    response = _health_client(
        mongo_db=FakeMongo(error=ConnectionError("mongodb://admin:hunter2@10.0.0.5 refused")), vectordb_client=FakeVector(),
    ).get("/health/ready")
    assert response.status_code == 503 and response.json()["status"] == "degraded"
    assert response.json()["checks"]["mongodb"] == "error" and response.json()["checks"]["vector_db"] == "ok"
    assert "hunter2" not in response.text and "10.0.0.5" not in response.text


def test_a_hanging_dependency_times_out_instead_of_hanging_the_probe(monkeypatch):
    monkeypatch.setattr(health_routes, "CHECK_TIMEOUT_SECONDS", 0.2)
    response = _health_client(mongo_db=FakeMongo(delay=5), vectordb_client=FakeVector()).get("/health/ready")
    assert response.status_code == 503 and response.json()["checks"]["mongodb"] == "timeout"


def test_readiness_reports_a_broken_configuration(monkeypatch):
    monkeypatch.setattr(get_settings(), "GROQ_API_KEY", None)
    response = _health_client(mongo_db=FakeMongo(), vectordb_client=FakeVector()).get("/health/ready")
    assert response.status_code == 503 and response.json()["checks"]["configuration"] == "error"


def test_readiness_before_startup_finishes_is_503_starting():
    response = _health_client().get("/health/ready")
    assert response.status_code == 503 and response.json()["status"] == "starting"


def _mongo_available() -> bool:
    import pymongo

    try:
        pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=1000).server_info()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _mongo_available(), reason="MongoDB is not reachable")
def test_the_real_app_reports_ready_with_its_real_databases(monkeypatch):
    settings = get_settings()
    db_name, qdrant_name = f"test_health_{uuid.uuid4().hex[:8]}", f"test_qdrant_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(settings, "MONGODB_URL", MONGO_URL)
    monkeypatch.setattr(settings, "MONGODB_DATABASE", db_name)
    monkeypatch.setattr(settings, "VECTOR_DB_PATH", qdrant_name)
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(settings, "COHERE_API_KEY", "test-key")
    try:
        with TestClient(main.app) as client:
            assert client.get("/health").json() == {"status": "ok"}
            ready = client.get("/health/ready")
            assert ready.status_code == 200 and ready.json()["checks"] == {"mongodb": "ok", "vector_db": "ok", "configuration": "ok"}
            assert ready.headers["x-request-id"]
    finally:
        import pymongo

        pymongo.MongoClient(MONGO_URL).drop_database(db_name)
        shutil.rmtree(SRC / "assets" / "database" / qdrant_name, ignore_errors=True)


# ── logs contain no personal data ────────────────────────────────────────────
SECRETS = ("SECRETQUERYTEXT", "secret.person@example.com", "SECRETSUBJECT", "SECRETBODY", "SECRETPASSAGE", "SECRETMAILSUBJECT", "SECRETSENDER")


class ChattyLLM:
    enums = SimpleNamespace(
        SYSTEM=SimpleNamespace(value="system"), USER=SimpleNamespace(value="user"), ASSISTANT=SimpleNamespace(value="assistant")
    )

    def __init__(self, classification):
        self.classification = classification

    def construct_prompt(self, prompt, role):
        return {"role": role, "content": prompt}

    def generate_text(self, prompt, **kwargs):
        if "Classify this query" in prompt:
            return json.dumps(self.classification)
        if "Summarize this email" in prompt:
            return json.dumps({"summary": "SECRETBODY summary", "action_items": [], "urgency": "low"})
        return json.dumps({"recipient": "secret.person@example.com", "subject": "SECRETSUBJECT", "body": "SECRETBODY"})

    async def agenerate_text(self, prompt, **kwargs):
        if "Gmail search" in prompt or "Convert the user's request" in prompt:
            return '{"query": "from:SECRETSENDER", "max_results": 2}'
        if "ONLY the context" in prompt:
            return '{"found": true, "answer": "SECRETPASSAGE says yes"}'
        return "A reply mentioning SECRETBODY."


def _run_and_collect_logs(caplog, monkeypatch, query, classification, before=None):
    caplog.set_level(logging.DEBUG)
    if before:
        before(monkeypatch)
    graph = build_agent_graph(
        AgentDeps(generation_client=ChattyLLM(classification), vectordb_client=object(), embedding_client=object(), db_client=None),
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": uuid.uuid4().hex}}

    async def go():
        await graph.ainvoke(create_initial_state(query, user_id="u1"), config=config)
        if (await graph.aget_state(config)).next:  # approval pause: approve so the send step also logs
            await graph.ainvoke(None, config=config)

    asyncio.run(go())
    return caplog.text


def _assert_clean(text):
    assert text, "the run should have logged something"
    for secret in SECRETS:
        assert secret not in text, f"{secret!r} was written to the log"


def test_an_email_request_does_not_log_the_recipient_subject_or_body(caplog, monkeypatch):
    from agent.nodes import actions

    class Gmail:
        def send_email(self, **kwargs):
            return {"message_id": "m1", "to": kwargs["to"], "subject": kwargs["subject"], "status": "sent"}

    text = _run_and_collect_logs(
        caplog, monkeypatch, "send an email to secret.person@example.com about SECRETQUERYTEXT",
        {"request_type": "action", "action_type": "send_email"},
        before=lambda mp: mp.setattr(actions, "gmail_for_request", lambda: Gmail()),
    )
    _assert_clean(text)
    assert "fields=" in text  # it still says which fields were extracted, which is useful for debugging


def test_a_document_question_does_not_log_the_question_or_the_passages(caplog, monkeypatch):
    class FakeNLP:
        def __init__(self, **kw): ...

        def search_vector_db_collection(self, project, text, limit):
            return [SimpleNamespace(text="SECRETPASSAGE in the contract", score=0.9)]

    text = _run_and_collect_logs(
        caplog, monkeypatch, "what does the SECRETQUERYTEXT clause say",
        {"request_type": "retrieval", "action_type": ""},
        before=lambda mp: mp.setattr(retrieval_node, "NLPController", FakeNLP),
    )
    _assert_clean(text)


def test_an_email_search_does_not_log_the_filter_or_the_messages(caplog, monkeypatch):
    class Gmail:
        def search_emails(self, query, max_results):
            return [{"message_id": "m1", "sender": "SECRETSENDER@example.com", "subject": "SECRETMAILSUBJECT",
                     "date": "", "body": "SECRETBODY text", "snippet": "SECRETBODY"}]

    text = _run_and_collect_logs(
        caplog, monkeypatch, "show emails from SECRETSENDER about SECRETQUERYTEXT",
        {"request_type": "action", "action_type": "gmail"},
        before=lambda mp: mp.setattr(gmail_node, "gmail_for_request", lambda: Gmail()),
    )
    _assert_clean(text)
    assert "filter=yes" in text


def test_a_web_search_does_not_log_the_query_or_the_results(caplog, monkeypatch):
    import types

    class DDGS:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def text(self, query, max_results=3):
            return [{"title": "SECRETPASSAGE", "href": "https://example.com", "body": "SECRETBODY"}]

    monkeypatch.delenv("GOOGLE_SEARCH_API_KEY", raising=False)
    monkeypatch.setattr(get_settings(), "GOOGLE_SEARCH_API_KEY", None)
    monkeypatch.setattr(get_settings(), "GOOGLE_SEARCH_CX", None)
    text = _run_and_collect_logs(
        caplog, monkeypatch, "who won SECRETQUERYTEXT",
        {"request_type": "web_search", "action_type": ""},
        before=lambda mp: mp.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=DDGS)),
    )
    _assert_clean(text)


# ── guard: new log lines must not bring personal data back ───────────────────
SENSITIVE_NAMES = {
    "query", "details", "prompt", "full_prompt", "documents_prompts", "body", "subject", "recipient", "email", "emails",
    "user_email", "snippet", "content", "reply", "summary", "description", "transcript",
}
SAFE_WRAPPERS = {"redact_text", "mask_email", "len", "sorted", "bool", "type", "int", "str_len"}


def _sensitive_names_in(node):
    """Sensitive identifiers used in a log call, ignoring those inside a safe wrapper such as len(...)."""
    found = []

    def walk(current):
        if isinstance(current, ast.Call):
            name = current.func.id if isinstance(current.func, ast.Name) else getattr(current.func, "attr", "")
            if name in SAFE_WRAPPERS:
                return
        if isinstance(current, ast.Name) and current.id in SENSITIVE_NAMES:
            found.append(current.id)
        elif isinstance(current, ast.Attribute) and current.attr in SENSITIVE_NAMES:
            found.append(current.attr)
        elif isinstance(current, ast.Subscript) and isinstance(current.slice, ast.Constant) and current.slice.value in SENSITIVE_NAMES:
            found.append(str(current.slice.value))
        for child in ast.iter_child_nodes(current):
            walk(child)

    walk(node)
    return found


def test_no_log_call_mentions_what_users_type_or_their_mail_and_documents():
    log_methods = {"debug", "info", "warning", "error", "exception", "critical"}
    offenders = []
    for path in SRC.rglob("*.py"):
        parts = set(path.relative_to(SRC).parts)
        if parts & {"tests", "__pycache__", "alembic"} or path.name.startswith("test_"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in log_methods):
                continue
            target = node.func.value
            if not ((isinstance(target, ast.Name) and "log" in target.id.lower()) or (isinstance(target, ast.Attribute) and "log" in target.attr.lower())):
                continue
            for argument in node.args:
                for name in _sensitive_names_in(argument):
                    offenders.append(f"{path.relative_to(SRC)}:{node.lineno} logs {name!r}")
    assert not offenders, "wrap with redact_text()/mask_email()/len() or log a count instead:\n" + "\n".join(offenders)
