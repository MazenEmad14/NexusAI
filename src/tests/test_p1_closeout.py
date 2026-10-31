"""Checks for the P1 items that were fixed along the way and had no test of their own (plan 2.1, 2.14,
2.15, 2.17, 2.19). MongoDB-backed checks use a throwaway database and are skipped without one."""
import asyncio
import base64
import email
import email.policy
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import AgentDeps
from agent.nodes import actions, intake
from stores.gmail.GmailService import GmailService

SRC = Path(__file__).resolve().parents[1]


def _mongo_available() -> bool:
    import pymongo

    try:
        pymongo.MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1000).server_info()
        return True
    except Exception:
        return False


needs_mongo = pytest.mark.skipif(not _mongo_available(), reason="MongoDB is not reachable")


# ── 2.14 sending mail ────────────────────────────────────────────────────────
class FakeSendService:
    """Records the raw message Gmail would receive; can be told to fail."""

    def __init__(self, fail=False):
        self.fail, self.calls, self.raw = fail, 0, None
        outer = self

        class Messages:
            def send(self, userId, body):
                outer.raw = body["raw"]

                def execute():
                    outer.calls += 1
                    if outer.fail:
                        raise TimeoutError("gateway timeout after Gmail accepted the message")
                    return {"id": "msg-1"}

                return SimpleNamespace(execute=execute)

        self.users = lambda: SimpleNamespace(messages=lambda: Messages())


def _gmail(service):
    gmail = GmailService()
    gmail.service = service
    return gmail


def test_arabic_subject_and_body_survive_sending():
    service = FakeSendService()
    result = _gmail(service).send_email("ahmed@example.com", "اجتماع الغد", "مرحبا أحمد، الاجتماع الساعة العاشرة.")

    assert result["status"] == "sent" and result["message_id"] == "msg-1"
    parsed = email.message_from_bytes(base64.urlsafe_b64decode(service.raw), policy=email.policy.default)
    assert parsed["To"] == "ahmed@example.com"
    assert parsed["Subject"] == "اجتماع الغد"
    assert "الاجتماع الساعة العاشرة" in parsed.get_content()


def test_a_failed_send_is_not_retried_so_it_cannot_deliver_twice():
    service = FakeSendService(fail=True)
    assert _gmail(service).send_email("a@example.com", "hi", "body") is None
    assert service.calls == 1  # the old code tried three times, sleeping in between


def test_recipients_are_cleaned_and_limited():
    service = FakeSendService()
    _gmail(service).send_email("a@example.com; b@example.com", "hi", "body")
    parsed = email.message_from_bytes(base64.urlsafe_b64decode(service.raw), policy=email.policy.default)
    assert parsed["To"] == "a@example.com, b@example.com"

    too_many = ", ".join(f"u{i}@example.com" for i in range(30))
    assert _gmail(FakeSendService()).send_email(too_many, "hi", "body") is None


# ── 2.15 / 2.17 / 2.19 ───────────────────────────────────────────────────────
def test_the_dead_report_email_node_is_gone():
    assert not hasattr(actions, "send_report_email_node")
    for path in (SRC / "agent").rglob("*.py"):
        assert "send_report_email" not in path.read_text(encoding="utf-8"), path.name


def test_the_nlp_routes_import_asyncio():
    source = (SRC / "routes" / "nlp.py").read_text(encoding="utf-8")
    assert "\nimport asyncio\n" in "\n" + source and "asyncio.to_thread" in source


def test_no_module_builds_its_own_llm_client_or_hard_codes_a_model():
    forbidden = ("langchain_groq", "langchain_openai", "ChatGroq", "ChatOpenAI", "gemini-2.5", "gpt-4o")
    for path in SRC.rglob("*.py"):
        parts = set(path.relative_to(SRC).parts)
        if parts & {"tests", "__pycache__", "alembic"} or path.name.startswith("test_"):
            continue
        if "providers" in parts:
            continue  # a provider class is where a default model id belongs
        text = path.read_text(encoding="utf-8")
        for name in forbidden:
            assert name not in text, f"{path.relative_to(SRC)} mentions {name}"


# ── 2.1 files offered to data analysis ───────────────────────────────────────
@needs_mongo
def test_available_files_are_the_callers_own_uploads_found_by_scoped_project():
    from motor.motor_asyncio import AsyncIOMotorClient
    from helpers.project_scope import scoped_project_id
    from models.AssetModel import AssetModel
    from models.ProjectModel import ProjectModel
    from models.db_schemes import Asset

    async def scenario():
        client = AsyncIOMotorClient("mongodb://localhost:27017/")
        name = f"test_files_{uuid.uuid4().hex[:8]}"
        db = client[name]
        try:
            projects, assets = await ProjectModel.create_instance(db), await AssetModel.create_instance(db)
            for user, file_name in (("alice", "aaa_sales.csv"), ("bob", "bbb_secret.csv")):
                project = await projects.get_project_or_create_one(scoped_project_id(user, "default"))
                await assets.create_asset(Asset(asset_project_id=project.id, asset_type="file", asset_name=file_name, asset_size=10))

            state = {"query": "analyse my file", "user_id": "alice", "project_id": "default", "metadata": {}, "chat_history": []}
            result = await intake.save_query_node(state, AgentDeps(db_client=db))
            return result["metadata"]["available_files"]
        finally:
            await client.drop_database(name)
            client.close()

    listing = asyncio.run(scenario())
    assert "aaa_sales.csv" in listing and "bbb_secret.csv" not in listing
    assert "\\" not in listing and "/" not in listing  # names only, never server paths
