"""The shared way of getting validated JSON from a model (plan 3.3)."""
import asyncio
import sys
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.exceptions import LLMOutputError
from helpers.llm_json import extract_json, llm_json, llm_json_sync, parse_llm_json
from stores.llm.LLMInterface import LLMInterface
from stores.llm.providers.GroqProvider import GroqProvider
from stores.llm.providers.OpenAIProvider import OpenAIProvider


class Verdict(BaseModel):
    ok: bool
    note: str = ""
    level: Literal["low", "high"] = "low"


# ── extracting the JSON ──────────────────────────────────────────────────────
@pytest.mark.parametrize("reply, expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Sure! Here is the result:\n{"a": 1}\nHope that helps.', {"a": 1}),
    ('Sure [see below]: {"a": 1}', {"a": 1}),                      # a bracket in the prose is skipped
    ('{"a": 1, "b": [1, 2,],}', {"a": 1, "b": [1, 2]}),            # trailing commas
    ('{"a": 1, // the first value\n "b": 2}', {"a": 1, "b": 2}),   # line comment
    ('{"a": /* inline */ 1}', {"a": 1}),                            # block comment
    ('[{"a": 1}, {"a": 2}]', [{"a": 1}, {"a": 2}]),
    ("{'a': 1, 'ok': True, 'x': None}", {"a": 1, "ok": True, "x": None}),   # a Python-style dict
    ('{"text": "line one\nline two"}', {"text": "line one\nline two"}),     # raw newline inside a string
])
def test_extract_json_handles_the_ways_models_wrap_json(reply, expected):
    assert extract_json(reply) == expected


def test_comment_markers_and_commas_inside_strings_are_left_alone():
    reply = '{"url": "https://example.com/a//b", "text": "a, } b", "note": "/* not a comment */"}'
    assert extract_json(reply) == {
        "url": "https://example.com/a//b", "text": "a, } b", "note": "/* not a comment */",
    }


def test_arabic_text_survives():
    assert extract_json('{"title": "اجتماع الفريق", "time": "17:00"}') == {"title": "اجتماع الفريق", "time": "17:00"}


@pytest.mark.parametrize("reply", [None, "", "no json here", "{broken", "[1, 2", "{'a': eval('1')}"])
def test_extract_json_returns_none_for_unusable_replies(reply):
    assert extract_json(reply) is None  # and nothing is ever executed


def test_extract_json_gives_up_on_pathological_replies_quickly():
    assert extract_json("[" * 5000) is None


# ── validating ───────────────────────────────────────────────────────────────
def test_valid_reply_is_returned_as_the_schema():
    verdict = parse_llm_json('```json\n{"ok": true, "level": "high"}\n```', Verdict)
    assert verdict == Verdict(ok=True, level="high")


def test_invalid_replies_raise_with_the_raw_text_and_a_readable_reason():
    for reply, fragment in (("nothing useful", "no JSON"), ('{"note": "x"}', "ok"), ('{"ok": true, "level": "extreme"}', "level")):
        with pytest.raises(LLMOutputError) as exc:
            parse_llm_json(reply, Verdict)
        assert fragment in str(exc.value) and exc.value.raw == reply


def test_a_list_schema_is_supported():
    assert parse_llm_json('[{"ok": true}, {"ok": false}]', list[Verdict]) == [Verdict(ok=True), Verdict(ok=False)]


# ── asking the model ─────────────────────────────────────────────────────────
class ScriptedLLM:
    """Replies from a script; records every call."""

    def __init__(self, *replies, fail_json_mode=False):
        self.replies, self.calls, self.fail_json_mode = list(replies), [], fail_json_mode

    def _next(self, prompt, kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        if kwargs.get("json_mode") and self.fail_json_mode:
            raise RuntimeError("Error code: 400 - 'response_format' of type 'json_object' is not supported with this model")
        return self.replies.pop(0)

    async def agenerate_text(self, prompt, **kwargs):
        return self._next(prompt, kwargs)

    def generate_text(self, prompt, **kwargs):
        return self._next(prompt, kwargs)


def ask(llm, **kwargs):
    return asyncio.run(llm_json(llm, "Is it ok? Reply as JSON.", Verdict, **kwargs))


def test_a_good_first_reply_costs_one_call():
    llm = ScriptedLLM('{"ok": true}')
    assert ask(llm) == Verdict(ok=True)
    assert len(llm.calls) == 1


def test_an_unusable_reply_is_retried_once_with_the_problem_explained():
    llm = ScriptedLLM("I think it is fine.", '{"ok": true}')
    assert ask(llm) == Verdict(ok=True)

    first, second = llm.calls
    assert second["prompt"].startswith(first["prompt"])  # the original request is repeated
    assert "no JSON" in second["prompt"] and "I think it is fine." in second["prompt"]  # with what was wrong
    assert "ONLY the corrected JSON" in second["prompt"]


def test_a_reply_that_is_still_unusable_raises_with_the_last_reply():
    llm = ScriptedLLM("first try", '{"ok": "maybe"}')
    with pytest.raises(LLMOutputError) as exc:
        ask(llm)
    assert exc.value.raw == '{"ok": "maybe"}' and len(llm.calls) == 2  # one retry, not a loop


def test_retries_can_be_turned_off_or_raised():
    assert len(ScriptedLLM("x").calls) == 0
    llm = ScriptedLLM("bad")
    with pytest.raises(LLMOutputError):
        ask(llm, retries=0)
    assert len(llm.calls) == 1

    llm = ScriptedLLM("bad", "worse", '{"ok": true}')
    assert ask(llm, retries=2) == Verdict(ok=True) and len(llm.calls) == 3


def test_provider_errors_are_not_swallowed():
    class Down:
        async def agenerate_text(self, prompt, **kwargs):
            raise ConnectionError("provider unreachable")

    with pytest.raises(ConnectionError):
        asyncio.run(llm_json(Down(), "p", Verdict))


def test_json_mode_is_requested_only_when_asked_for():
    plain, strict = ScriptedLLM('{"ok": true}'), ScriptedLLM('{"ok": true}')
    ask(plain)
    ask(strict, json_mode=True)
    assert "json_mode" not in plain.calls[0] and strict.calls[0]["json_mode"] is True


def test_a_model_that_rejects_json_mode_is_asked_again_without_it():
    llm = ScriptedLLM('{"ok": true}', fail_json_mode=True)
    assert ask(llm, json_mode=True) == Verdict(ok=True)
    assert [("json_mode" in call) for call in llm.calls] == [True, False]


def test_the_synchronous_version_behaves_the_same():
    llm = ScriptedLLM("nope", '{"ok": true}')
    assert llm_json_sync(llm, "p", Verdict) == Verdict(ok=True) and len(llm.calls) == 2

    llm = ScriptedLLM('{"ok": true}', fail_json_mode=True)
    assert llm_json_sync(llm, "p", Verdict, json_mode=True) == Verdict(ok=True)
    assert [("json_mode" in call) for call in llm.calls] == [True, False]


# ── providers ────────────────────────────────────────────────────────────────
def _sdk_recorder():
    from types import SimpleNamespace

    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


@pytest.mark.parametrize("make", [lambda: OpenAIProvider(api_key="x"), lambda: GroqProvider(api_key="x")])
def test_openai_and_groq_send_response_format_only_in_json_mode(make):
    provider = make()
    provider.set_generation_model("m")
    provider.client, calls = _sdk_recorder()

    provider.generate_text("hi")
    provider.generate_text("hi", json_mode=True)

    assert "response_format" not in calls[0]
    assert calls[1]["response_format"] == {"type": "json_object"}


def test_providers_written_before_json_mode_still_work_through_the_async_default():
    class OldStyle(LLMInterface):
        default_generation_max_output_tokens = 10
        default_generation_temperature = 0.0

        def set_generation_model(self, model_id): ...
        def set_embedding_model(self, model_id, embedding_size): ...
        def embed_text(self, text, document_type=None): ...
        def embed_texts(self, texts, document_type=None): ...
        def construct_prompt(self, prompt, role): ...

        def generate_text(self, prompt, chat_history=None, max_output_tokens=None, temperature=None):  # no json_mode
            return '{"ok": true}'

    assert asyncio.run(OldStyle().agenerate_text("p")) == '{"ok": true}'
    assert asyncio.run(llm_json(OldStyle(), "p", Verdict)) == Verdict(ok=True)


# ── no new ad-hoc parsers ────────────────────────────────────────────────────
def test_only_the_shared_helper_parses_model_output():
    """Model JSON is parsed in one place. A new parser elsewhere would bring back the old inconsistency."""
    src = Path(__file__).resolve().parents[1]
    offenders = []
    for path in src.rglob("*.py"):
        parts = set(path.relative_to(src).parts)
        if parts & {"tests", "__pycache__", "alembic"} or path.name.startswith("test_") or path.name == "llm_json.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "literal_eval" in text or ".find('{')" in text or '.find("{")' in text or 'r"\{.*\}"' in text:
            offenders.append(str(path.relative_to(src)))
    assert not offenders, f"parse model JSON with helpers.llm_json instead: {offenders}"
