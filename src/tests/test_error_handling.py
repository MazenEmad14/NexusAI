"""Domain exceptions and what the user sees when something fails (plan 3.6)."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from pymongo.errors import DuplicateKeyError, PyMongoError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import AgentDeps, build_agent_graph, create_initial_state
from agent.nodes import actions, gmail as gmail_node
from helpers.auth_utils import create_access_token, get_current_user
from helpers.error_handlers import install_error_handlers, public_message_for
from helpers.exceptions import (
    DomainError, EmailAlreadyRegistered, ExternalServiceError, GoogleAuthRequiredException,
    LLMOutputError, LLMUnavailable, ValidationFailed,
)
from routes import agent as agent_routes
from routes.dashboard import envelope
from stores.llm.providers.CoHereProvider import CoHereProvider
from stores.llm.providers.GroqProvider import GroqProvider
from stores.llm.providers.OpenAIProvider import OpenAIProvider


# ── the exceptions themselves ────────────────────────────────────────────────
class SdkError(Exception):
    """Looks like an SDK error: a name and sometimes a status code."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(SdkError): ...
class APITimeoutError(SdkError): ...
class APIConnectionError(SdkError): ...


@pytest.mark.parametrize("exc, reason", [
    (RateLimitError("slow down", 429), "rate_limited"),
    (SdkError("bad key", 401), "auth"),
    (SdkError("no such model", 404), "model_not_found"),
    (APITimeoutError("timed out"), "timeout"),
    (APIConnectionError("cannot connect"), "unreachable"),
    (ConnectionResetError("reset"), "unreachable"),
    (SdkError("boom", 500), "error"),
])
def test_provider_errors_are_classified(exc, reason):
    error = LLMUnavailable.from_exception(exc, provider="GroqProvider")
    assert error.reason == reason and error.provider == "GroqProvider"
    assert error.__cause__ is exc and str(exc) in str(error)  # the original text stays for the logs
    assert isinstance(error, DomainError) and error.status_code == 503


def test_what_the_user_is_told_does_not_include_internal_detail():
    secret = "Error code: 401 - invalid api key gsk_live_SECRET at https://internal.example/v1"
    error = LLMUnavailable.from_exception(SdkError(secret, 401), provider="GroqProvider")
    assert "SECRET" not in error.public_message and "internal" not in error.public_message
    assert "busy" in LLMUnavailable.from_exception(RateLimitError("x", 429)).public_message
    assert "too long" in LLMUnavailable.from_exception(APITimeoutError("x")).public_message
    assert LLMUnavailable.from_exception(error) is error  # already translated: not wrapped twice

    assert "could not be understood" in LLMOutputError("the JSON does not match: secret detail", raw="x").public_message
    assert "secret" not in ExternalServiceError("Gmail request failed (500): secret detail", service="Gmail").public_message
    assert GoogleAuthRequiredException().status_code == 409 and "reconnect" in GoogleAuthRequiredException().public_message.lower()
    assert ValidationFailed("That date is not valid").public_message == "That date is not valid"


# ── the handlers ─────────────────────────────────────────────────────────────
def _app_raising(exc):
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise exc

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("exc, status, code", [
    (LLMUnavailable("groq 404 model gone", provider="Groq", reason="model_not_found"), 503, "llm_unavailable"),
    (GoogleAuthRequiredException(), 409, "google_auth_required"),
    (EmailAlreadyRegistered(), 409, "email_taken"),
    (ValidationFailed("Please give a valid date"), 400, "invalid_input"),
    (ExternalServiceError("Gmail 500 internal", service="Gmail"), 502, "external_service_error"),
    (PyMongoError("connection refused to mongodb://admin:hunter2@10.0.0.5:27017"), 503, "database_unavailable"),
    (RuntimeError("secret internal detail: /srv/app/db.py line 42"), 500, "internal_error"),
])
def test_every_failure_reaches_the_client_as_the_same_clean_json(exc, status, code):
    response = _app_raising(exc).get("/boom")

    assert response.status_code == status
    body = response.json()
    assert set(body) == {"detail", "code"} and body["code"] == code and body["detail"]
    for leak in ("hunter2", "10.0.0.5", "/srv/app", "secret internal", "Gmail 500", "groq 404"):
        assert leak not in response.text


def test_http_exceptions_and_validation_errors_keep_their_own_format():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/teapot")
    async def teapot():
        raise HTTPException(status_code=418, detail="I am a teapot")

    @app.get("/number/{n}")
    async def number(n: int):
        return n

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/teapot").json() == {"detail": "I am a teapot"}
    assert client.get("/number/abc").status_code == 422


def test_public_message_for_covers_streaming_errors_too():
    assert "not available" in public_message_for(LLMUnavailable("x"))
    assert "database" in public_message_for(PyMongoError("x")).lower()
    assert public_message_for(RuntimeError("secret")) == "Something went wrong. Please try again."


def test_the_dashboard_envelope_maps_domain_errors_without_leaking():
    app = FastAPI()

    @app.get("/google")
    @envelope("ok", "Failed")
    async def google():
        raise GoogleAuthRequiredException()

    @app.get("/db")
    @envelope("ok", "Failed")
    async def db():
        raise PyMongoError("mongodb://admin:hunter2@host")

    client = TestClient(app, raise_server_exceptions=False)
    google_response = client.get("/google")
    assert google_response.status_code == 409 and google_response.json()["status"] == "error"
    assert "reconnect" in google_response.json()["message"].lower()
    db_response = client.get("/db")
    assert db_response.status_code == 503 and "hunter2" not in db_response.text


# ── providers translate SDK errors ───────────────────────────────────────────
def _raising_chat(exc):
    def create(**kwargs):
        raise exc

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _raising_async_chat(exc):
    async def create(**kwargs):
        raise exc

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


@pytest.mark.parametrize("make", [lambda: OpenAIProvider(api_key="x"), lambda: GroqProvider(api_key="x")])
def test_openai_and_groq_raise_llm_unavailable_for_sdk_errors(make):
    provider = make()
    provider.set_generation_model("m")

    provider.client = _raising_chat(RateLimitError("too many requests", 429))
    with pytest.raises(LLMUnavailable) as sync_error:
        provider.generate_text("hi")
    assert sync_error.value.reason == "rate_limited"

    provider.async_client = _raising_async_chat(SdkError("model gone", 404))
    with pytest.raises(LLMUnavailable) as async_error:
        asyncio.run(provider.agenerate_text("hi"))
    assert async_error.value.reason == "model_not_found"


def test_cohere_chat_and_embeddings_raise_llm_unavailable():
    provider = CoHereProvider(api_key="x")
    provider.set_generation_model("m")
    provider.set_embedding_model("e", 3)

    def chat(**kwargs):
        raise APIConnectionError("cannot connect")

    def embed(**kwargs):
        raise SdkError("bad key", 401)

    provider.client = SimpleNamespace(chat=chat, embed=embed)
    with pytest.raises(LLMUnavailable):
        provider.generate_text("hi")
    with pytest.raises(LLMUnavailable) as error:
        provider.embed_texts(["a"])
    assert error.value.reason == "auth"


def test_a_rejected_json_mode_is_still_recognised_after_translation():
    """llm_json asks again without JSON mode when the provider refuses it; the wrapper must not hide that."""
    from pydantic import BaseModel
    from helpers.llm_json import llm_json

    class Reply(BaseModel):
        ok: bool

    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if "response_format" in kwargs:
            raise SdkError("Error code: 400 - 'response_format' of type 'json_object' is not supported", 400)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])

    async def acreate(**kwargs):
        return create(**kwargs)

    provider = GroqProvider(api_key="x")
    provider.set_generation_model("m")
    provider.async_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=acreate)))

    assert asyncio.run(llm_json(provider, "p", Reply, json_mode=True)) == Reply(ok=True)
    assert ["response_format" in call for call in calls] == [True, False]


def test_gemini_raises_instead_of_returning_none():
    from stores.llm.providers.GeminiProvider import GeminiProvider

    provider = GeminiProvider(api_key="x")

    class Broken:
        def invoke(self, messages):
            raise APIConnectionError("cannot connect")

    provider._llm = lambda *a, **k: Broken()
    with pytest.raises(LLMUnavailable):
        provider.generate_text("hi")


# ── the agent explains a failure instead of showing "nothing found" ──────────
class DownLLM:
    """A model that cannot be reached."""

    enums = SimpleNamespace(
        SYSTEM=SimpleNamespace(value="system"), USER=SimpleNamespace(value="user"), ASSISTANT=SimpleNamespace(value="assistant")
    )

    def construct_prompt(self, prompt, role):
        return {"role": role, "content": prompt}

    def generate_text(self, prompt, **kwargs):
        raise LLMUnavailable("model not found", provider="Groq", reason="model_not_found")

    async def agenerate_text(self, prompt, **kwargs):
        raise LLMUnavailable("model not found", provider="Groq", reason="model_not_found")


def _run_graph(llm, query, thread):
    graph = build_agent_graph(AgentDeps(generation_client=llm), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": thread}}

    async def go():
        final = await graph.ainvoke(create_initial_state(query, user_id="u1"), config=config)
        return final, await graph.aget_state(config)

    return _run(go())


def _run(coro):
    return asyncio.run(coro)


def test_when_the_model_is_down_the_reply_says_so_and_nothing_else_runs():
    final, snapshot = _run_graph(DownLLM(), "what is on my plate this week", "down-1")

    assert final["status"] == "error" and final["metadata"]["error_code"] == "llm_unavailable"
    assert "not available" in final["result"].lower()
    assert "No information found" not in final["result"]  # it used to answer as if it had searched and found nothing
    assert snapshot.next == ()  # finished; no approval pause


def test_when_the_model_is_down_an_email_request_is_not_sent_for_approval_with_empty_details():
    final, snapshot = _run_graph(DownLLM(), "send an email to ahmed@example.com that I will be late", "down-2")

    assert final["status"] == "error" and final["metadata"]["error_code"] == "llm_unavailable"
    assert snapshot.next == ()  # before: the user was asked to approve an email with no recipient or body


def test_a_busy_model_is_described_as_busy():
    class Busy(DownLLM):
        def generate_text(self, prompt, **kwargs):
            raise LLMUnavailable("429", provider="Groq", reason="rate_limited")

    final, _ = _run_graph(Busy(), "what is on my plate", "down-3")
    assert "busy" in final["result"].lower()


def _gmail_graph_run(monkeypatch, gmail, llm=None):
    classification = {"request_type": "action", "action_type": "gmail", "priority": "low"}

    class Classifying(DownLLM):
        def generate_text(self, prompt, **kwargs):
            return json.dumps(classification)

        async def agenerate_text(self, prompt, **kwargs):
            return '{"query": "", "max_results": 3}'

    monkeypatch.setattr(gmail_node, "gmail_for_request", lambda: gmail)
    return _run_graph(llm or Classifying(), "show my latest emails", "gmail-run")[0]


def test_gmail_failures_are_explained_not_reported_as_no_results(monkeypatch):
    class Failing:
        def __init__(self, error):
            self.error = error

        def search_emails(self, **kwargs):
            raise self.error

    expired = _gmail_graph_run(monkeypatch, Failing(GoogleAuthRequiredException()))
    assert expired["status"] == "error" and "reconnect" in expired["result"].lower()

    outage = _gmail_graph_run(monkeypatch, Failing(ExternalServiceError("Gmail 503", service="Gmail")))
    assert outage["status"] == "error" and "not responding" in outage["result"]
    assert "No information found" not in outage["result"] and "503" not in outage["result"]


def test_an_unavailable_model_while_building_the_gmail_search_is_reported(monkeypatch):
    class OnlyClassifies(DownLLM):
        def generate_text(self, prompt, **kwargs):
            return json.dumps({"request_type": "action", "action_type": "gmail", "priority": "low"})

    monkeypatch.setattr(gmail_node, "gmail_for_request", lambda: SimpleNamespace(search_emails=lambda **k: pytest.fail("must not search")))
    final = _run_graph(OnlyClassifies(), "show my latest emails", "gmail-down")[0]
    assert final["status"] == "error" and final["metadata"]["error_code"] == "llm_unavailable"


@pytest.mark.parametrize("error, fragment", [
    (GoogleAuthRequiredException(), "reconnect"),
    (ExternalServiceError("Gmail 500", service="Gmail"), "not responding"),
    (ValidationFailed("That email address is not valid, so nothing was sent."), "not valid"),
])
def test_approved_actions_tell_the_user_why_they_failed(monkeypatch, error, fragment):
    class FailingGmail:
        def send_email(self, **kwargs):
            raise error

    monkeypatch.setattr(actions, "gmail_for_request", lambda: FailingGmail())
    state = {"action_details": {"recipient": "a@example.com", "subject": "s", "body": "b"}, "metadata": {}}
    result = actions.send_email_node(state, AgentDeps())
    assert result["status"] == "error" and fragment in result["result"].lower()


def test_a_calendar_failure_is_explained(monkeypatch):
    class FailingCalendar:
        def create_event(self, **kwargs):
            raise ValidationFailed("I couldn't understand that date or time.")

    monkeypatch.setattr(actions, "calendar_for_request", lambda: FailingCalendar())
    state = {"action_details": {"title": "x", "date": "tomorrow-ish", "time": "soon"}, "metadata": {}}
    result = actions.create_calendar_event_node(state, AgentDeps())
    assert result["status"] == "error" and "date or time" in result["result"]


# ── a database failure is not an unknown user ────────────────────────────────
def test_a_database_outage_does_not_sign_the_user_out(monkeypatch):
    """get_current_user used to turn "database down" into 401 "User not found", which logs people out."""
    from models.UserModel import UserModel

    async def down(self, email):
        raise PyMongoError("connection lost")

    monkeypatch.setattr(UserModel, "get_user_by_email", down)
    app = FastAPI()
    install_error_handlers(app)
    app.state.services = SimpleNamespace(mongo_db=MagicMock())

    @app.get("/me")
    async def me(user: dict = Depends(get_current_user)):
        return {"ok": True}

    token = create_access_token({"sub": "a@example.com"})
    response = TestClient(app, raise_server_exceptions=False).get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503 and response.json()["code"] == "database_unavailable"


def test_user_model_reports_duplicates_and_lets_outages_through():
    from models.UserModel import UserModel
    from models.db_schemes.user import User

    class Collection:
        def __init__(self, error):
            self.error = error

        async def insert_one(self, doc):
            raise self.error

        async def find_one(self, *args, **kwargs):
            raise self.error

    def model(error):
        users = UserModel(MagicMock())
        users.collection = Collection(error)
        return users

    user = User(name="A", email="a@example.com", password_hash="x")
    with pytest.raises(EmailAlreadyRegistered):
        _run(model(DuplicateKeyError("E11000 duplicate key")).create_user(user))
    with pytest.raises(PyMongoError):
        _run(model(PyMongoError("down")).create_user(user))
    with pytest.raises(PyMongoError):
        _run(model(PyMongoError("down")).get_user_by_email("a@example.com"))  # not None, which meant "no such user"


# ── the agent routes ─────────────────────────────────────────────────────────
class RaisingGraph:
    """Just enough of a graph for the chat routes; fails the way a real run can."""

    def __init__(self, error):
        self.error = error

    async def aget_state(self, config):
        return SimpleNamespace(values={}, next=())

    async def ainvoke(self, *args, **kwargs):
        raise self.error

    async def astream_events(self, *args, **kwargs):
        raise self.error
        yield  # pragma: no cover  (makes this an async generator)


@pytest.fixture
def chat_client(monkeypatch):
    from models.UserModel import UserModel

    async def no_token(self, email):
        return None

    monkeypatch.setattr(UserModel, "get_google_token", no_token)

    def make(error):
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(agent_routes.agent_router)
        app.dependency_overrides[agent_routes.get_current_user] = lambda: {"_id": "u1", "email": "a@example.com"}
        app.state.services = SimpleNamespace(mongo_db=MagicMock(), agent_graph=RaisingGraph(error))
        return TestClient(app, raise_server_exceptions=False)

    return make


def test_a_failing_chat_run_is_reported_honestly_not_as_an_expired_session(chat_client):
    for error, status, fragment in (
        (RuntimeError("secret bug in node x"), 500, "Something went wrong"),
        (PyMongoError("connection lost"), 503, "database"),
        (LLMUnavailable("404", provider="Groq", reason="model_not_found"), 503, "not available"),
    ):
        response = chat_client(error).post("/api/v1/agent/chat", json={"query": "hello", "thread_id": "t1"})
        assert response.status_code == status
        assert fragment in response.json()["detail"] and "secret" not in response.text
        assert "Session expired" not in response.text  # what every error used to be called


def test_a_failing_stream_sends_a_readable_error_event(chat_client):
    client = chat_client(LLMUnavailable("404", provider="Groq", reason="model_not_found"))
    body = client.post("/api/v1/agent/chat/stream", json={"query": "hello", "thread_id": "t1"}).text

    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {")]
    error = next(event for event in events if event["type"] == "error")
    assert "not available" in error["content"] and error["code"] == "llm_unavailable"
    assert "[DONE]" in body


# ── guard ────────────────────────────────────────────────────────────────────
def test_the_messages_users_see_for_failures_never_blame_the_session_by_default():
    source = (Path(__file__).resolve().parents[1] / "routes" / "agent.py").read_text(encoding="utf-8")
    # "Session expired or invalid." is only for ThreadStateLoadError now; it is not a catch-all message.
    assert source.count('"Session expired or invalid."') <= 3
    assert "except Exception as e:\n        logger.error(f\"Error in agent chat endpoint" not in source


# ── "nothing found" must mean nothing was found ──────────────────────────────
def _retrieval(monkeypatch, query, nlp_error=None, nlp_results=(), tasks=None, llm=None):
    from agent.nodes import retrieval as retrieval_node
    from models.TaskModel import TaskModel

    class FakeNLP:
        def __init__(self, **kw): ...

        def search_vector_db_collection(self, project, text, limit):
            if nlp_error:
                raise nlp_error
            return list(nlp_results)

    async def search_tasks(self, *args, **kwargs):
        if isinstance(tasks, Exception):
            raise tasks
        return tasks or []

    monkeypatch.setattr(retrieval_node, "NLPController", FakeNLP)
    monkeypatch.setattr(TaskModel, "search_tasks", search_tasks)
    deps = AgentDeps(vectordb_client=object(), embedding_client=object(), generation_client=llm, db_client=MagicMock())
    state = {"query": query, "user_id": "u1", "project_id": "default", "metadata": {}, "chat_history": []}
    return _run(retrieval_node.search_knowledge_base_node(state, deps))


def test_a_vector_store_failure_is_not_reported_as_no_information_found(monkeypatch):
    result = _retrieval(monkeypatch, "what does the contract say", nlp_error=RuntimeError("qdrant storage is locked"))
    assert result["status"] == "error" and result["metadata"]["unavailable_sources"] == ["knowledge_base"]
    assert "No information found" not in result["result"] and "locked" not in result["result"]


def test_an_unavailable_embedding_model_is_named_as_the_cause(monkeypatch):
    result = _retrieval(monkeypatch, "what does the contract say",
                        nlp_error=LLMUnavailable("embed 401", provider="CoHereProvider", reason="auth"))
    assert result["status"] == "error" and result["metadata"]["error_code"] == "llm_unavailable"


def test_genuinely_finding_nothing_is_still_no_results(monkeypatch):
    result = _retrieval(monkeypatch, "what does the contract say", nlp_results=[])
    assert result["status"] == "no_results" and "unavailable_sources" not in result["metadata"]


def test_one_failing_source_does_not_hide_what_another_found(monkeypatch):
    task = SimpleNamespace(description="Send the report", deadline=None, priority="high")

    class LLM:
        async def agenerate_text(self, prompt, **kwargs):
            return '{"found": true, "answer": "You have a task to send the report."}'

    result = _retrieval(monkeypatch, "which tasks do I have", nlp_error=RuntimeError("qdrant down"), tasks=[task], llm=LLM())
    assert result["status"] == "success" and "send the report" in result["result"]
    assert result["metadata"]["unavailable_sources"] == ["knowledge_base"]  # and the gap is recorded


def test_when_every_searched_source_fails_the_reply_is_an_error_even_with_the_tasks_source(monkeypatch):
    result = _retrieval(monkeypatch, "which tasks do I have", nlp_error=RuntimeError("down"), tasks=PyMongoError("down"))
    assert result["status"] == "error" and result["metadata"]["unavailable_sources"] == ["knowledge_base", "tasks"]


def test_a_failed_web_search_is_an_error_but_an_empty_one_is_not(monkeypatch):
    import types
    from agent.nodes import web

    class FailingDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def text(self, query, max_results=3):
            raise ConnectionError("search backend unreachable")

    class EmptyDDGS(FailingDDGS):
        def text(self, query, max_results=3):
            return []

    class LLM:
        async def agenerate_text(self, prompt, **kwargs):
            return "I could not find anything about that."

    monkeypatch.delenv("GOOGLE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_SEARCH_CX", raising=False)
    state = lambda: {"query": "who won yesterday", "metadata": {}, "chat_history": []}

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FailingDDGS))
    failed = _run(web.web_search_node(state(), AgentDeps(generation_client=LLM())))
    assert failed["status"] == "error" and failed["metadata"]["error_code"] == "external_service_error"
    assert "unreachable" not in failed["result"]

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=EmptyDDGS))
    empty = _run(web.web_search_node(state(), AgentDeps(generation_client=LLM())))
    assert empty["status"] == "success"  # no hits is an answer, not a failure


def test_gmail_listing_messages_that_cannot_be_read_is_a_failure_not_an_empty_inbox():
    from stores.gmail.GmailService import GmailService

    class Messages:
        def __init__(self, ids):
            self.ids = ids

        def list(self, **kwargs):
            return SimpleNamespace(execute=lambda: {"messages": [{"id": i} for i in self.ids]})

        def get(self, **kwargs):
            raise RuntimeError("backend error")

    def service(ids):
        gmail = GmailService()
        gmail.service = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: Messages(ids)))
        return gmail

    with pytest.raises(ExternalServiceError):
        service(["m1", "m2"]).search_emails("is:unread")
    assert service([]).search_emails("is:unread") == []  # a really empty inbox still returns []


def test_an_error_does_not_leak_into_the_next_turn_of_the_same_conversation():
    """Metadata is carried between turns; the error code of a failed turn must not stick to a good one."""
    class Flaky(DownLLM):
        down = True

        def generate_text(self, prompt, **kwargs):
            if Flaky.down:
                raise LLMUnavailable("down", reason="unreachable")
            return json.dumps({"request_type": "conversational", "action_type": ""})

        async def agenerate_text(self, prompt, **kwargs):
            return "Hello again!"

    graph = build_agent_graph(AgentDeps(generation_client=Flaky()), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "flaky"}}

    async def two_turns():
        first = await graph.ainvoke(create_initial_state("what is on my plate", user_id="u1"), config=config)
        carried = (await graph.aget_state(config)).values
        second_input = create_initial_state("and now?", user_id="u1")
        second_input["chat_history"] = carried["chat_history"]
        second_input["metadata"] = dict(carried["metadata"])  # what the chat route does
        Flaky.down = False
        second = await graph.ainvoke(second_input, config=config)
        return first, second

    first, second = _run(two_turns())
    assert first["metadata"]["error_code"] == "llm_unavailable"
    assert second["status"] == "success" and "error_code" not in second["metadata"]
