"""Retrieval routing and Gmail node behaviour (plan 2.4, 2.5). LLM, Gmail and vector DB are faked;
the task-dedupe tests use a throwaway database on the local MongoDB and are skipped without one."""
import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controllers import agent_graph
from controllers.gmail_query import GmailSearchPlan, build_gmail_query, parse_gmail_plan
from controllers.task_extractor import parse_deadline
from services.retrieval import choose_sources, parse_kb_answer


# ── 2.5 Gmail query plan ─────────────────────────────────────────────────────
def test_gmail_plan_parses_valid_json_and_fenced_json():
    assert parse_gmail_plan('{"query": "from:ahmed", "max_results": 3}') == GmailSearchPlan(query="from:ahmed", max_results=3)
    fenced = '```json\n{"query": "", "max_results": 7}\n```'
    assert parse_gmail_plan(fenced) == GmailSearchPlan(query="", max_results=7)


def test_gmail_plan_recovers_from_malformed_json():
    # Trailing comment makes this invalid JSON; the fields must still be recovered.
    raw = '{"query": "has:attachment", "max_results": 4 // fix later\n}'
    plan = parse_gmail_plan(raw)
    assert plan.query == "has:attachment" and plan.max_results == 4


def test_gmail_plan_clamps_and_sanitises_values():
    assert parse_gmail_plan('{"query": "x", "max_results": 999}').max_results == 20
    assert parse_gmail_plan('{"query": "x", "max_results": 0}').max_results == 1
    assert parse_gmail_plan('{"query": "x", "max_results": "many"}').max_results == 5
    assert parse_gmail_plan('{"query": "a\\nb", "max_results": 2}').query == ""
    assert parse_gmail_plan('{"query": "\\"from:x\\"", "max_results": 2}').query == "from:x"
    assert len(parse_gmail_plan(json.dumps({"query": "q" * 1000, "max_results": 2})).query) == 200
    assert parse_gmail_plan(None) == GmailSearchPlan()
    assert parse_gmail_plan("no json here") == GmailSearchPlan()


def test_build_gmail_query_never_raises():
    class Failing:
        async def agenerate_text(self, **kw):
            raise RuntimeError("provider down")

    class Working:
        async def agenerate_text(self, **kw):
            return '{"query": "newer_than:1d", "max_results": 3}'

    assert asyncio.run(build_gmail_query(Failing(), "mail today")) == GmailSearchPlan()
    assert asyncio.run(build_gmail_query(None, "mail today")) == GmailSearchPlan()
    assert asyncio.run(build_gmail_query(Working(), "mail today")) == GmailSearchPlan(query="newer_than:1d", max_results=3)


def test_deadline_parsing():
    now = datetime(2026, 6, 1, 10, 0)
    assert parse_deadline("2026-06-25 17:00") == (datetime(2026, 6, 25, 17, 0), True)
    assert parse_deadline("2026-06-25") == (datetime(2026, 6, 25), False)
    assert parse_deadline("tomorrow", now) == (datetime(2026, 6, 2), False)
    assert parse_deadline("in 3 days", now) == (datetime(2026, 6, 4), False)
    assert parse_deadline("بكرة", now) == (datetime(2026, 6, 2), False)
    for unresolvable in (None, "", "next week", "soonish"):
        assert parse_deadline(unresolvable, now) is None


# ── 2.4 source selection and structured answers ──────────────────────────────
def test_sources_are_chosen_from_the_question_not_fanned_out():
    assert choose_sources("what does the contract say about payment") == ["knowledge_base"]
    assert choose_sources("which tasks mention the report") == ["knowledge_base", "tasks"]
    assert set(choose_sources("do I have a meeting and a reminder")) == {"knowledge_base", "reminders", "calendar"}
    assert "gmail" not in choose_sources("emails from ahmed about my tasks")


def test_structured_kb_answer_parsing():
    found = parse_kb_answer('{"found": true, "answer": "Net 30 days."}')
    assert found.found and found.answer == "Net 30 days."
    assert parse_kb_answer('{"found": false, "answer": ""}').found is False
    assert parse_kb_answer("__NO_RELEVANT_CONTEXT__") is None
    assert parse_kb_answer('{"answer": "missing found flag"}') is None


class FakeNLP:
    results = []

    def __init__(self, **kw):
        pass

    def search_vector_db_collection(self, project, text, limit):
        return FakeNLP.results


def _kb_state():
    return {"query": "what is the payment term", "user_id": "u1", "project_id": "default", "metadata": {}, "chat_history": []}


def _run_kb(monkeypatch, generation_client, results):
    FakeNLP.results = results
    monkeypatch.setattr(agent_graph, "NLPController", FakeNLP)

    def forbidden(*a, **k):
        raise AssertionError("Gmail/Calendar must not be touched for a knowledge-base question")

    monkeypatch.setattr(agent_graph, "_gmail_for_state", forbidden)
    monkeypatch.setattr(agent_graph, "_calendar_for_state", forbidden)
    return asyncio.run(agent_graph.search_knowledge_base_node(
        _kb_state(), vectordb_client=object(), embedding_client=object(),
        generation_client=generation_client, template_parser=None, db_client=None,
    ))


class CountingLLM:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, 0

    async def agenerate_text(self, **kw):
        self.calls += 1
        if self.error:
            raise self.error
        return self.reply


PASSAGE = [SimpleNamespace(text="Payment is due within 30 days.", score=0.9)]


def test_kb_question_makes_one_llm_call_and_returns_the_answer(monkeypatch):
    llm = CountingLLM('{"found": true, "answer": "Payment is due in 30 days."}')
    state = _run_kb(monkeypatch, llm, PASSAGE)

    assert llm.calls == 1
    assert state["status"] == "success" and "30 days" in state["result"]
    assert state["metadata"]["sources_searched"] == ["knowledge_base"]
    assert state["emails"] == []


def test_found_false_means_no_results_without_any_sentinel_string(monkeypatch):
    state = _run_kb(monkeypatch, CountingLLM('{"found": false, "answer": ""}'), PASSAGE)
    assert state["status"] == "no_results"
    assert "__NO_RELEVANT_CONTEXT__" not in state["result"] and state["result"]


def test_unstructured_reply_is_still_used_and_llm_failure_falls_back_to_the_passage(monkeypatch):
    plain = _run_kb(monkeypatch, CountingLLM("It is 30 days."), PASSAGE)
    assert plain["status"] == "success" and "It is 30 days." in plain["result"]

    failed = _run_kb(monkeypatch, CountingLLM(error=RuntimeError("boom")), PASSAGE)
    assert failed["status"] == "success" and "Payment is due within 30 days." in failed["result"]


def test_no_search_results_skip_the_llm_entirely(monkeypatch):
    llm = CountingLLM("should not be called")
    state = _run_kb(monkeypatch, llm, [])
    assert llm.calls == 0 and state["status"] == "no_results"


def test_graph_has_no_retrieval_to_gmail_to_web_cascade():
    edges = agent_graph.build_agent_graph().get_graph().edges
    pairs = {(e.source, e.target) for e in edges}
    assert ("search_knowledge_base", "format_output") in pairs
    assert ("search_knowledge_base", "search_gmail") not in pairs
    assert ("search_gmail", "web_search") not in pairs
    assert not hasattr(agent_graph, "route_kb_results") and not hasattr(agent_graph, "route_gmail_results")


# ── 2.5 Gmail node ───────────────────────────────────────────────────────────
EMAILS = [
    {"message_id": "m1", "sender": "boss@example.com", "subject": "Report", "date": "", "body": "Please send the report", "snippet": ""},
    {"message_id": "m2", "sender": "hr@example.com", "subject": "Form", "date": "", "body": "Fill the form", "snippet": ""},
]


class FakeGmail:
    def __init__(self):
        self.calls = []

    def search_emails(self, query, max_results):
        self.calls.append((query, max_results))
        return EMAILS[:max_results]


class GmailLLM:
    """Plan, summary and task extraction all go through one fake provider."""
    enums = SimpleNamespace(SYSTEM=SimpleNamespace(value="system"), USER=SimpleNamespace(value="user"))

    def __init__(self, deadline="2026-12-01 10:00"):
        self.deadline = deadline
        self.extraction_calls = 0

    def construct_prompt(self, prompt, role):
        return {"role": role, "content": prompt}

    async def agenerate_text(self, prompt, **kw):
        return '{"query": "from:boss", "max_results": 2}'

    def generate_text(self, prompt, chat_history=None, **kw):
        if "Text to extract from" in prompt:
            self.extraction_calls += 1
            return json.dumps([{"description": "Send the report", "deadline": self.deadline, "priority": "high"}])
        return json.dumps({"summary": "A request", "action_items": [], "urgency": "medium"})


def test_gmail_node_honours_the_requested_count_and_query(monkeypatch):
    gmail = FakeGmail()
    monkeypatch.setattr(agent_graph, "_gmail_for_state", lambda state=None: gmail)
    state = {"query": "last 2 mails from my boss", "user_id": "u1", "project_id": "default", "metadata": {}, "chat_history": []}

    result = asyncio.run(agent_graph.search_gmail_node(state, generation_client=GmailLLM(), db_client=None))

    assert gmail.calls == [("from:boss", 2)]
    assert result["status"] == "success" and len(result["emails"]) == 2


def test_gmail_node_without_a_google_account_asks_to_connect(monkeypatch):
    from helpers.exceptions import GoogleAuthRequiredException

    def no_token(state=None):
        raise GoogleAuthRequiredException("none")

    monkeypatch.setattr(agent_graph, "_gmail_for_state", no_token)
    state = {"query": "mail", "user_id": "u1", "metadata": {}, "chat_history": []}
    result = asyncio.run(agent_graph.search_gmail_node(state, generation_client=GmailLLM(), db_client=None))
    assert result["status"] == "error" and "connect your Google account" in result["result"]


def _mongo_available() -> bool:
    import pymongo

    try:
        pymongo.MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1000).server_info()
        return True
    except Exception:
        return False


needs_mongo = pytest.mark.skipif(not _mongo_available(), reason="MongoDB is not reachable")


def _with_db(coro_fn):
    from motor.motor_asyncio import AsyncIOMotorClient

    async def runner():
        client = AsyncIOMotorClient("mongodb://localhost:27017/")
        name = f"test_tasks_{uuid.uuid4().hex[:8]}"
        try:
            return await coro_fn(client[name])
        finally:
            await client.drop_database(name)
            client.close()

    return asyncio.run(runner())


@needs_mongo
def test_repeating_the_same_request_does_not_duplicate_tasks_or_reminders(monkeypatch):
    monkeypatch.setattr(agent_graph, "_gmail_for_state", lambda state=None: FakeGmail())
    llm = GmailLLM()

    async def scenario(db):
        for _ in range(3):
            state = {"query": "my latest mails", "user_id": "u1", "project_id": "default", "metadata": {}, "chat_history": []}
            await agent_graph.search_gmail_node(state, generation_client=llm, db_client=db)

        assert await db.tasks.count_documents({"user_id": "u1"}) == 1
        assert await db.reminders.count_documents({"user_id": "u1"}) == 1
        reminder = await db.reminders.find_one({"user_id": "u1"})
        # The deadline is split into proper date/time fields, not stored as one raw string.
        assert reminder["date"] == "2026-12-01" and reminder["time"] == "10:00"
        # Emails already mined are not sent to the extractor again.
        assert llm.extraction_calls == 1

    _with_db(scenario)


@needs_mongo
def test_same_task_from_a_new_email_is_not_duplicated_and_users_stay_separate(monkeypatch):
    llm = GmailLLM(deadline="tomorrow")

    async def scenario(db):
        first, second = FakeGmail(), FakeGmail()
        second.search_emails = lambda query, max_results: [{**EMAILS[0], "message_id": "m9"}]

        async def run(user, gmail):
            monkeypatch.setattr(agent_graph, "_gmail_for_state", lambda state=None: gmail)
            state = {"query": "mails", "user_id": user, "project_id": "default", "metadata": {}, "chat_history": []}
            await agent_graph.search_gmail_node(state, generation_client=llm, db_client=db)

        await run("u1", first)
        await run("u1", second)  # new message, same task text
        await run("u2", first)   # another user gets their own copy

        assert await db.tasks.count_documents({"user_id": "u1"}) == 1
        assert await db.tasks.count_documents({"user_id": "u2"}) == 1
        # "tomorrow" was resolved to a real date instead of being dropped.
        task = await db.tasks.find_one({"user_id": "u1"})
        assert task["deadline"] is not None

    _with_db(scenario)
