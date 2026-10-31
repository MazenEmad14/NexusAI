"""Every prompt the model is given lives in the locale templates (plan 3.4)."""
import ast
import importlib
import json
import sys
from pathlib import Path
from string import Template
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controllers.classifier import ClassifierController
from helpers.prompts import render
from stores.llm.templates.template_parser import TemplateParser

SRC = Path(__file__).resolve().parents[1]
LOCALES = SRC / "stores" / "llm" / "templates" / "locales"


def all_templates():
    for path in sorted((LOCALES / "en").glob("*.py")):
        if path.stem == "__init__":
            continue
        module = importlib.import_module(f"stores.llm.templates.locales.en.{path.stem}")
        for name, value in vars(module).items():
            if isinstance(value, Template):
                yield path.stem, name, value


def placeholders(template: Template) -> set:
    found = set()
    for match in template.pattern.finditer(template.template):
        assert match.group("invalid") is None, f"stray $ in {template.template[:60]!r}"
        name = match.group("braced") or match.group("named")
        if name:
            found.add(name)
    return found


@pytest.mark.parametrize("group, key, template", list(all_templates()), ids=lambda v: v if isinstance(v, str) else "")
def test_every_template_renders_and_leaves_nothing_behind(group, key, template):
    names = placeholders(template)
    text = render(group, key, **{name: f"<{name}>" for name in names})
    assert all(f"<{name}>" in text for name in names)
    assert "${" not in text


def test_the_templates_the_code_relies_on_all_exist():
    keys = {(group, key) for group, key, _ in all_templates()}
    used = set()
    for path in SRC.rglob("*.py"):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "render"
                    and len(node.args) >= 2 and all(isinstance(a, ast.Constant) for a in node.args[:2])):
                used.add((node.args[0].value, node.args[1].value))
    assert used, "no render() calls found"
    assert used <= keys, f"render() asks for templates that do not exist: {sorted(used - keys)}"
    assert {k for k in keys if k[0] != "rag"} <= used, "unused template: delete it or use it"


def test_values_containing_braces_and_dollars_are_inserted_literally():
    text = render("agent", "web_answer", snippets="$5 {not_a_placeholder} ${query}", query='say "hi" $$')
    assert "$5 {not_a_placeholder} ${query}" in text
    assert 'say "hi" $$' in text


def test_a_missing_value_is_an_error_not_a_blank():
    with pytest.raises(KeyError):
        render("agent", "web_answer", snippets="only one")


def test_an_unknown_template_is_an_error():
    with pytest.raises(LookupError):
        render("agent", "nope")


def test_language_falls_back_to_the_default_per_prompt():
    arabic = TemplateParser(language="ar", default_language="en")
    assert arabic.language == "ar"
    # Arabic has the RAG prompts, not the agent ones: those come from English.
    assert arabic.get("rag", "system_prompt") != TemplateParser("en").get("rag", "system_prompt")
    assert arabic.get("agent", "conversational_persona") == TemplateParser("en").get("agent", "conversational_persona")
    assert arabic.get("agent", "does_not_exist") is None


@pytest.mark.parametrize("language", [None, "", "xx", "../en"])
def test_unknown_or_missing_language_means_the_default(language):
    parser = TemplateParser(language=language, default_language="en")
    assert parser.language == "en"
    assert parser.get("agent", "conversational_persona")


def test_no_prompt_is_written_inline_in_the_code_again():
    """A long string literal in agent/, controllers/ or services/ is almost certainly a prompt: put it in a template."""
    offenders = []
    for folder in ("agent", "controllers", "services"):
        for path in (SRC / folder).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = {
                id(node.body[0].value) for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str)
            }
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
                        and len(node.value) > 250):
                    offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not offenders, f"long string literals (inline prompts?): {offenders}"


# ── what the model is actually sent ──────────────────────────────────────────
class RecordingLLM:
    enums = SimpleNamespace(SYSTEM=SimpleNamespace(value="system"), USER=SimpleNamespace(value="user"),
                            ASSISTANT=SimpleNamespace(value="assistant"))

    def __init__(self):
        self.calls = []

    def construct_prompt(self, prompt, role):
        return {"role": role, "content": prompt}

    def generate_text(self, prompt, chat_history=None, **kwargs):
        self.calls.append({"prompt": prompt, "system": chat_history[0]["content"] if chat_history else None})
        return json.dumps({})


def sent_to_model(method, *args, **kwargs):
    llm = RecordingLLM()
    getattr(ClassifierController(generation_client=llm), method)(*args, **kwargs)
    return llm.calls[0]


def test_reminder_prompt_carries_the_users_date_and_a_literal_json_example():
    call = sent_to_model("_extract_reminder_details", "remind me tomorrow", timezone_name="Africa/Cairo")
    assert "Today's date is 20" in call["system"] and "Africa/Cairo" in call["system"]
    assert '"task": "Submit the report"' in call["system"]       # braces of the JSON example survive
    assert "{current_date}" not in call["system"] and "${" not in call["system"]
    assert call["prompt"] == 'User request: "remind me tomorrow"'


def test_meeting_prompt_carries_the_users_time():
    call = sent_to_model("_extract_meeting_details", "book it", timezone_name="Africa/Cairo")
    assert "Current Date and Time: 20" in call["system"] and "(Africa/Cairo)" in call["system"]
    assert '"duration_minutes": 60' in call["system"]


def test_file_prompt_lists_files_or_says_there_are_none():
    with_files = sent_to_model("_extract_data_analysis_details", "sales", available_files="- sales.csv")
    assert "- sales.csv" in with_files["system"]
    none = sent_to_model("_extract_data_analysis_details", "sales", available_files="")
    assert "The user must upload a file first." in none["system"]


def test_classifier_prompt_is_the_template_text():
    call = sent_to_model("classify_query", "what is the capital of France?")
    assert call["system"] == render("classifier", "classify_system")
    assert call["prompt"] == 'Classify this query: "what is the capital of France?"'
