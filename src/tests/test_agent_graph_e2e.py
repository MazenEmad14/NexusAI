"""The real compiled agent graph, run end to end with a fake model (plan 3.1/3.2).

These runs go through StateGraph, the checkpointer and the nodes' injected services, so they catch
wiring mistakes (a node missing its services, a renamed edge) that tests of single nodes cannot."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import MemorySaver

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import AgentDeps, build_agent_graph, create_initial_state
from agent.nodes import actions
from helpers.google_context import set_google_token


class FakeLLM:
    """Stands in for a provider: classification by keyword bypass, canned text and JSON otherwise."""

    enums = SimpleNamespace(
        SYSTEM=SimpleNamespace(value="system"), USER=SimpleNamespace(value="user"), ASSISTANT=SimpleNamespace(value="assistant")
    )

    def __init__(self, classification=None):
        self.classification = classification
        self.prompts = []

    def construct_prompt(self, prompt, role):
        return {"role": role, "content": prompt}

    async def agenerate_text(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return "Hello there, how can I help?"

    def generate_text(self, prompt, chat_history=None, **kwargs):
        self.prompts.append(prompt)
        if self.classification and "Classify this query" in prompt:
            return json.dumps(self.classification)
        return json.dumps({"recipient": "ahmed@example.com", "subject": "Running late", "body": "Hi Ahmed, I will be late."})


def _run(coro):
    return asyncio.run(coro)


def _config(thread="t1"):
    return {"configurable": {"thread_id": thread}}


def test_a_greeting_runs_through_the_whole_graph():
    llm = FakeLLM()
    graph = build_agent_graph(AgentDeps(generation_client=llm), checkpointer=MemorySaver())

    final = _run(graph.ainvoke(create_initial_state("hi", user_id="u1"), config=_config()))

    assert final["status"] == "success" and final["result"] == "Hello there, how can I help?"
    assert final["request_type"] == "conversational"
    assert final["metadata"]["source"] == "conversational"
    # format_output recorded both sides of the exchange in the history
    assert [turn["role"] for turn in final["chat_history"]] == ["user", "assistant"]
    assert "processing_time" in final["metadata"]


def test_a_node_without_its_service_fails_loudly_instead_of_carrying_on():
    """With **kwargs a misspelt or missing service was silently None; the typed deps cannot be misspelt."""
    assert not hasattr(AgentDeps(), "generation_clent")
    with pytest.raises(TypeError):
        AgentDeps(generation_clent=object())  # a typo in the field name is an error at construction


def test_sending_an_email_pauses_for_approval_and_resumes_with_the_extracted_details(monkeypatch):
    sent = {}

    class FakeGmail:
        def send_email(self, to, subject, body, html=False):
            sent.update(to=to, subject=subject, body=body)
            return {"message_id": "m1", "to": to, "subject": subject, "status": "sent"}

    monkeypatch.setattr(actions, "gmail_for_request", lambda: FakeGmail())
    llm = FakeLLM()
    graph = build_agent_graph(AgentDeps(generation_client=llm), checkpointer=MemorySaver())
    config = _config("approval")

    async def scenario():
        await graph.ainvoke(create_initial_state("send an email to ahmed@example.com that I will be late", user_id="u1"), config=config)
        paused = await graph.aget_state(config)
        assert paused.next == ("send_email",)  # stopped before acting
        assert sent == {}  # nothing was sent while waiting for approval
        assert paused.values["action_details"]["recipient"] == "ahmed@example.com"

        final = await graph.ainvoke(None, config=config)  # the user approves
        return final

    final = _run(scenario())
    assert sent == {"to": "ahmed@example.com", "subject": "Running late", "body": "Hi Ahmed, I will be late."}
    assert final["status"] == "success"


def test_without_a_google_account_the_approved_email_asks_to_connect():
    set_google_token(None)
    graph = build_agent_graph(AgentDeps(generation_client=FakeLLM()), checkpointer=MemorySaver())
    config = _config("no-google")

    async def scenario():
        await graph.ainvoke(create_initial_state("send an email to ahmed@example.com that I will be late", user_id="u1"), config=config)
        return await graph.ainvoke(None, config=config)

    final = _run(scenario())
    assert final["status"] == "error" and "connect your Google account" in final["result"]


def test_the_daily_summary_route_runs_with_no_google_account_or_database():
    llm = FakeLLM(classification={"request_type": "action", "action_type": "summary", "priority": "low"})
    graph = build_agent_graph(AgentDeps(generation_client=llm), checkpointer=MemorySaver())
    set_google_token(None)

    final = _run(graph.ainvoke(create_initial_state("what does my day look like", user_id="u1"), config=_config("summary")))

    assert final["action_type"] == "summary" and final["status"] == "success"
    assert final["metadata"]["source"] == "daily_summary"
