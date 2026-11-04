"""The classifier is told which documents the user uploaded, so a question about them is not sent to the web."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.nodes.intake import _document_names
from controllers.classifier import ClassifierController


class Recorder:
    enums = SimpleNamespace(SYSTEM=SimpleNamespace(value="system"), USER=SimpleNamespace(value="user"),
                            ASSISTANT=SimpleNamespace(value="assistant"))

    def __init__(self):
        self.prompts = []

    def construct_prompt(self, prompt, role):
        return {"role": role, "content": prompt}

    def generate_text(self, prompt, chat_history=None, **kwargs):
        self.prompts.append(prompt)
        return '{"request_type": "retrieval", "action_type": "", "priority": "low"}'


def test_only_documents_for_the_knowledge_base_are_named_not_spreadsheets():
    state = {"metadata": {"available_files": "falcon.txt -> falcon.txt\nsales.csv -> sales.csv\nplan.pdf -> plan.pdf"}}
    assert _document_names(state) == "falcon.txt, plan.pdf"
    assert _document_names({"metadata": {}}) == ""
    many = {"metadata": {"available_files": "\n".join(f"d{i}.txt -> d{i}.txt" for i in range(30))}}
    assert len(_document_names(many).split(", ")) == 10


def test_the_classifier_prompt_mentions_uploaded_documents_only_when_there_are_some():
    llm = Recorder()
    controller = ClassifierController(generation_client=llm)

    controller.classify_query("Who leads Project Falcon?", uploaded_documents="falcon.txt, plan.pdf")
    assert "falcon.txt, plan.pdf" in llm.prompts[-1] and "retrieval" in llm.prompts[-1]

    controller.classify_query("Who leads Project Falcon?")
    assert llm.prompts[-1] == 'Classify this query: "Who leads Project Falcon?"'
