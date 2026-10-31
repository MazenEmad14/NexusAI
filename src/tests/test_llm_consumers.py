"""Everything that reads JSON from the model now goes through helpers/llm_json (plan 3.3). These tests
feed each consumer the messy replies real models give and check what comes out."""
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controllers.classifier import ClassifierController
from controllers.email_summarizer import EmailSummarizerController
from controllers.llm_schemas import Classification, EmailSummary, MeetingDetails, ReminderDetails, UrgentVerdict
from controllers.task_extractor import TaskExtractorController


class ScriptedSyncLLM:
    """A provider stand-in: returns the scripted replies in order and records each call."""

    enums = SimpleNamespace(
        SYSTEM=SimpleNamespace(value="system"), USER=SimpleNamespace(value="user"), ASSISTANT=SimpleNamespace(value="assistant")
    )

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def construct_prompt(self, prompt, role):
        return {"role": role, "content": prompt}

    def generate_text(self, prompt, chat_history=None, **kwargs):
        self.calls.append({"prompt": prompt, "history": chat_history, **kwargs})
        return self.replies.pop(0) if self.replies else None


def classify(*replies, query="what is on my plate"):
    llm = ScriptedSyncLLM(*replies)
    return ClassifierController(generation_client=llm).classify_query(query), llm


# ── classification ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw, expected", [
    ({"request_type": "retrieval", "action_type": "", "priority": "HIGH"}, ("retrieval", "", "high")),
    ({"request_type": "action", "action_type": "gmail", "priority": "low"}, ("action", "gmail", "low")),
    ({"category": "web_search", "subcategory": None}, ("web_search", "", None)),           # other key names
    ({"request_type": "retrieval", "action_type": "calendar"}, ("action", "calendar", None)),  # an action forces "action"
    ({"request_type": "action", "action_type": "do the send_email thing"}, ("action", "send_email", None)),  # found in a phrase
    ({"request_type": "banana"}, ("retrieval", "", None)),                                   # nonsense falls back
    ({"request_type": "something web_search related"}, ("web_search", "", None)),
    ({"request_type": "action", "action_type": "x", "priority": "urgent!!"}, ("action", "", None)),  # bad priority is dropped
])
def test_classification_schema_normalises_what_models_actually_say(raw, expected):
    result = Classification.model_validate(raw)
    assert (result.request_type, result.action_type, result.priority) == expected


def test_classifier_handles_fenced_commented_and_python_style_replies():
    for reply in (
        '```json\n{"request_type": "action", "action_type": "gmail", "priority": "medium"}\n```',
        '{"request_type": "action", "action_type": "gmail", "priority": "medium",}',
        "{'request_type': 'action', 'action_type': 'gmail', 'priority': 'medium'}",
        'Sure!\n{"request_type": "action", // reading mail\n "action_type": "gmail"}',
    ):
        result, llm = classify(reply)
        assert (result["request_type"], result["action_type"]) == ("action", "gmail"), reply
        assert len(llm.calls) == 1


def test_classifier_asks_for_json_mode_and_fills_in_a_missing_priority():
    result, llm = classify('{"request_type": "retrieval", "action_type": ""}', query="this is urgent please")
    assert llm.calls[0]["json_mode"] is True
    assert result["priority"] == "critical"  # worked out from the query when the model gave none


def test_an_unusable_first_reply_is_retried_before_falling_back():
    result, llm = classify("hmm, hard to say", '{"request_type": "web_search", "action_type": ""}')
    assert result["request_type"] == "web_search" and len(llm.calls) == 2
    assert "could not be used" in llm.calls[1]["prompt"]


def test_when_no_reply_has_json_the_keywords_in_it_decide():
    result, llm = classify("I would say this is about gmail", "still just words about gmail")
    assert (result["request_type"], result["action_type"]) == ("action", "gmail") and len(llm.calls) == 2

    for words, expected in (("probably web_search", "web_search"), ("just chatting, conversational", "conversational"), ("no idea", "retrieval")):
        assert classify(words, words)[0]["request_type"] == expected


def test_a_provider_failure_still_falls_back_to_retrieval():
    class Broken(ScriptedSyncLLM):
        def generate_text(self, prompt, chat_history=None, **kwargs):
            raise ConnectionError("model not found (404)")

    result = ClassifierController(generation_client=Broken()).classify_query("what is on my plate")
    assert result["request_type"] == "retrieval"


# ── action details ───────────────────────────────────────────────────────────
def extract(action, *replies, **kwargs):
    llm = ScriptedSyncLLM(*replies)
    details = ClassifierController(generation_client=llm).extract_action_details("do it", action, **kwargs)
    return details, llm


def test_email_details_are_returned_as_strings_even_when_fields_are_null():
    details, llm = extract("send_email", '```json\n{"recipient": "ahmed@example.com", "subject": null, "body": "Hi"}\n```')
    assert details == {"recipient": "ahmed@example.com", "subject": "", "body": "Hi"}
    assert llm.calls[0]["json_mode"] is True


def test_meeting_details_with_nulls_get_sensible_defaults():
    reply = '{"action": "create", "title": null, "date": null, "time": null, "duration_minutes": null, "attendees": "ahmed@example.com", "is_modification": "false"}'
    details, _ = extract("calendar", reply)
    # A null title used to reach the calendar as None, which the create call then used as the title.
    assert details["title"] == "Meeting" and details["date"] == "" and details["time"] == ""
    assert details["duration_minutes"] == 60 and details["attendees"] == ["ahmed@example.com"] and details["is_modification"] is False


def test_meeting_schema_keeps_arabic_and_cleans_attendees():
    meeting = MeetingDetails.model_validate({"title": "اجتماع الفريق", "attendees": ["a@x.com", "", None, " b@x.com "], "duration_minutes": "90"})
    assert meeting.title == "اجتماع الفريق" and meeting.attendees == ["a@x.com", "b@x.com"]  # blanks and nulls are dropped
    assert meeting.duration_minutes == 90


def test_reminder_prompt_now_tells_the_model_todays_date_in_the_users_zone(monkeypatch):
    import helpers.timezones as tz

    fixed = datetime(2026, 7, 8, 23, 30, tzinfo=ZoneInfo("Pacific/Auckland"))
    monkeypatch.setattr(tz, "local_now", lambda zone=None: fixed)
    details, llm = extract("reminder", '{"task": "Call the dentist", "date": "2026-07-09", "time": "09:00", "priority": "HIGH"}',
                           timezone_name="Pacific/Auckland")

    system_prompt = llm.calls[0]["history"][0]["content"]
    assert "2026-07-08" in system_prompt and "Wednesday" in system_prompt and "Pacific/Auckland" in system_prompt
    assert "{current_date}" not in system_prompt and "{current_weekday}" not in system_prompt  # they were never filled in before
    assert "{{" not in system_prompt and '"task": "string' in system_prompt  # the template's braces render as JSON
    assert details["priority"] == "high" and details["task"] == "Call the dentist"


def test_reminder_priority_is_clamped():
    assert ReminderDetails.model_validate({"task": "x", "priority": "whenever"}).priority == "medium"


def test_unusable_details_give_an_empty_dict_after_one_retry():
    details, llm = extract("send_email", "I could not find a recipient", "sorry, still no JSON")
    assert details == {} and len(llm.calls) == 2


# ── emails ───────────────────────────────────────────────────────────────────
EMAIL = {"sender": "boss@example.com", "subject": "Report", "date": "Mon", "body": "Please send the report", "snippet": "Please send"}


def test_summaries_are_bounded_even_if_the_model_misbehaves():
    huge = '{"summary": "' + "x" * 5000 + '", "action_items": ["' + "y" * 1000 + '", 1, 2], "urgency": "OMG"}'
    summary = EmailSummarizerController(ScriptedSyncLLM(huge)).summarize_single_email(EMAIL)
    assert len(summary["summary"]) == 1000 and len(summary["action_items"][0]) == 300 and summary["urgency"] == "medium"
    assert summary["sender"] == "boss@example.com" and summary["subject"] == "Report"  # always from the real email


def test_an_unusable_summary_falls_back_to_the_snippet():
    summary = EmailSummarizerController(ScriptedSyncLLM("no json", "none here either")).summarize_single_email(EMAIL)
    assert summary["summary"] == "Please send" and summary["action_items"] == []
    assert summary["sender"] == "boss@example.com"


def test_summary_schema_defaults():
    assert EmailSummary.model_validate({}).urgency == "medium"
    assert EmailSummary.model_validate({"action_items": "not a list"}).action_items == []


# ── tasks ────────────────────────────────────────────────────────────────────
def extract_tasks(*replies):
    return TaskExtractorController(ScriptedSyncLLM(*replies)).extract_tasks("some summaries")


def test_tasks_are_found_in_a_fenced_array_or_an_object_wrapper():
    task = '{"description": "Submit the report", "deadline": "2026-12-01", "priority": "high"}'
    assert extract_tasks(f"```json\n[{task}]\n```")[0]["description"] == "Submit the report"
    assert extract_tasks(f'{{"tasks": [{task}]}}')[0]["priority"] == "high"
    assert extract_tasks(f"Here you go:\n[{task},]")[0]["deadline"] == "2026-12-01"  # trailing comma


def test_task_replies_without_tasks_or_json_give_an_empty_list():
    assert extract_tasks("[]") == []
    assert extract_tasks("nothing to do", "really nothing") == []


# ── proactive agent verdict ──────────────────────────────────────────────────
def test_urgent_verdict_tolerates_sloppy_output():
    verdict = UrgentVerdict.model_validate({"is_urgent": "True", "urgent_emails": "none", "message": None})
    assert verdict.is_urgent is True and verdict.urgent_emails == [] and verdict.message == ""
    assert UrgentVerdict.model_validate({}).is_urgent is False


# ── data analysis plan ───────────────────────────────────────────────────────
def test_an_unsupported_analysis_operation_is_corrected_by_the_retry(tmp_path, monkeypatch):
    from agent import AgentDeps
    from agent.nodes import data_analysis as dan

    csv = tmp_path / "sales.csv"
    csv.write_text("Region,Sales\na,10\na,5\nb,3\n")
    monkeypatch.setattr(dan, "resolve_user_file", lambda state: str(csv))

    replies = iter([
        '{"op": "run_python", "code": "import os"}',                                             # not allowed -> retry
        '```json\n{"op": "aggregate", "column": "Sales", "agg": "sum", "group_by": "Region", "chart": "bar"}\n```',
        "Region a leads with 15.",
    ])
    prompts = []

    class Client:
        async def agenerate_text(self, prompt, **kwargs):
            prompts.append(prompt)
            return next(replies)

    result = asyncio.run(dan.data_analysis_node({"query": "sales by region", "metadata": {}}, AgentDeps(generation_client=Client())))

    assert result["status"] == "success" and result["chart_data"]["type"] == "bar"
    assert "op" in prompts[1] and "could not be used" in prompts[1]  # the model was told what was wrong


def test_the_knowledge_base_answer_costs_two_calls_when_the_first_reply_is_not_json(monkeypatch):
    from agent import AgentDeps
    from agent.nodes import retrieval as retrieval_node

    class FakeNLP:
        def __init__(self, **kw): ...

        def search_vector_db_collection(self, project, text, limit):
            return [SimpleNamespace(text="Payment is due within 30 days.", score=0.9)]

    monkeypatch.setattr(retrieval_node, "NLPController", FakeNLP)
    replies = iter(["It is 30 days.", '{"found": true, "answer": "Payment is due in 30 days."}'])

    class LLM:
        calls = 0

        async def agenerate_text(self, prompt, **kwargs):
            LLM.calls += 1
            return next(replies)

    state = {"query": "payment term", "user_id": "u1", "project_id": "default", "metadata": {}, "chat_history": []}
    result = asyncio.run(retrieval_node.search_knowledge_base_node(
        state, AgentDeps(vectordb_client=object(), embedding_client=object(), generation_client=LLM())))

    assert LLM.calls == 2 and "30 days" in result["result"] and result["status"] == "success"
