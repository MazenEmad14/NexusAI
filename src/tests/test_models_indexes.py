"""Index creation and task search (plan 2.9, 2.10). Uses a throwaway database on the local
MongoDB and is skipped when none is reachable."""
import asyncio
import sys
import uuid
from pathlib import Path

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import monitoring

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.AssetModel import AssetModel
from models.ChunkModel import ChunkModel
from models.ProjectModel import ProjectModel
from models.QueryModel import QueryModel
from models.ReminderModel import ReminderModel
from models.TaskModel import TaskModel
from models.UserModel import UserModel
from models.db_schemes.task import Task
from models.db_schemes.user import User
from models.indexes import ensure_all_indexes

MONGO_URL = "mongodb://localhost:27017/"


def _mongo_available() -> bool:
    import pymongo

    try:
        pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=1000).server_info()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mongo_available(), reason="MongoDB is not reachable")


class CommandCounter(monitoring.CommandListener):
    def __init__(self):
        self.names = []

    def started(self, event):
        self.names.append(event.command_name)

    def succeeded(self, event): ...
    def failed(self, event): ...


def _with_db(coro_fn, listeners=None):
    async def runner():
        client = AsyncIOMotorClient(MONGO_URL, event_listeners=listeners or [])
        name = f"test_models_{uuid.uuid4().hex[:8]}"
        try:
            return await coro_fn(client[name])
        finally:
            await client.drop_database(name)
            client.close()

    return asyncio.run(runner())


async def _index_names(db, collection):
    return {name async for name in _names(db[collection])}


async def _names(collection):
    async for index in collection.list_indexes():
        yield index["name"]


# ── 2.10 indexes are created once, not per request ───────────────────────────
def test_create_instance_makes_no_database_calls():
    counter = CommandCounter()

    async def scenario(db):
        counter.names.clear()
        for _ in range(5):
            for model in (TaskModel, UserModel, ProjectModel, AssetModel, ChunkModel, QueryModel, ReminderModel):
                await model.create_instance(db)
        assert counter.names == []  # no listCollections, no createIndexes, nothing

    _with_db(scenario, listeners=[counter])


def test_ensure_all_indexes_creates_what_the_app_relies_on_and_is_idempotent():
    async def scenario(db):
        await ensure_all_indexes(db)
        await ensure_all_indexes(db)  # running again on every startup must be harmless

        users = {i["name"]: i async for i in db.users.list_indexes()}
        assert users["email_1"]["unique"] is True

        assert "task_description_text" in await _index_names(db, "tasks")
        scan = [i async for i in db.task_email_scans.list_indexes() if i["name"] != "_id_"]
        assert scan and scan[0]["unique"] is True

        assets = {i["name"]: i async for i in db.assets.list_indexes()}
        assert assets["asset_project_id_name_index_1"]["unique"] is True
        projects = {i["name"]: i async for i in db.projects.list_indexes()}
        assert projects["project_id_index_1"]["unique"] is True

    _with_db(scenario)


def test_indexes_missing_from_an_existing_collection_are_added():
    """Previously indexes were only created when the collection did not exist yet."""

    async def scenario(db):
        await db.queries.insert_one({"query_text": "old data", "project_id": "p", "user_id": "u"})  # collection exists
        await ensure_all_indexes(db)
        assert "query_project_id_index" in await _index_names(db, "queries")

    _with_db(scenario)


def test_a_conflicting_existing_index_does_not_stop_startup(caplog):
    async def scenario(db):
        # Same keys as the model's index but a different name: MongoDB refuses to create a duplicate.
        await db.projects.create_index([("project_id", 1)], name="legacy_name", unique=True)
        await ensure_all_indexes(db)  # must not raise
        assert "email_1" in await _index_names(db, "users")  # later models still got their indexes

    with caplog.at_level("ERROR", logger="uvicorn.error"):
        _with_db(scenario)
    assert any("Could not create index" in r.message for r in caplog.records)


def test_user_updates_report_success_when_nothing_changed():
    async def scenario(db):
        model = await UserModel.create_instance(db)
        await model.create_user(User(name="A", email="a@example.com", password_hash="x"))

        assert await model.update_google_auth_status("a@example.com", False) is True  # already False
        assert await model.update_google_token("a@example.com", {"refresh_token": "r"}) is True
        assert await model.update_google_token("a@example.com", {"refresh_token": "r"}) is True  # same value again
        assert await model.update_google_token("nobody@example.com", {"refresh_token": "r"}) is False

    _with_db(scenario)


# ── 2.9 task search ──────────────────────────────────────────────────────────
async def _seed(db):
    model = await TaskModel.create_instance(db)
    tasks = [
        ("alice", "Submit the quarterly report", "pending"),
        ("alice", "Book flights for the conference", "pending"),
        ("alice", "Old report archive", "completed"),
        ("bob", "Review report draft for the board", "pending"),
    ]
    for user, description, status in tasks:
        await model.create_task(Task(user_id=user, project_id="default", description=description, status=status))
    return model


def _descriptions(tasks):
    return sorted(t.description for t in tasks)


def test_text_search_is_scoped_to_the_user_and_ignores_stop_words():
    async def scenario(db):
        await ensure_all_indexes(db)
        model = await _seed(db)

        found = await model.search_tasks("default", "alice", "what are my tasks about the report", limit=5)
        assert _descriptions(found) == ["Submit the quarterly report"]  # not bob's, not the completed one

        assert _descriptions(await model.search_tasks("default", "bob", "report", limit=5)) == ["Review report draft for the board"]
        assert await model.search_tasks("default", "carol", "report", limit=5) == []

    _with_db(scenario)


def test_fallback_without_a_text_index_is_still_scoped_to_the_user():
    """The old fallback dropped the user filter, so it returned other users' tasks."""

    async def scenario(db):
        model = await _seed(db)  # note: no ensure_all_indexes, so no text index exists
        assert "task_description_text" not in await _index_names(db, "tasks")

        found = await model.search_tasks("default", "alice", "report", limit=5)
        assert _descriptions(found) == ["Submit the quarterly report"]
        assert await model.search_tasks("default", "carol", "report", limit=5) == []

    _with_db(scenario)


def test_fallback_treats_user_text_as_plain_words_not_a_regex():
    async def scenario(db):
        model = await _seed(db)

        # Would match every task if interpreted as a regex, or raise on the unbalanced bracket.
        for hostile in (".*", "(", "[a-z]+", "report(", "a.*"):
            result = await model.search_tasks("default", "alice", hostile, limit=5)
            assert len(result) <= 1, hostile
        assert _descriptions(await model.search_tasks("default", "alice", "report (draft", limit=5)) == ["Submit the quarterly report"]

    _with_db(scenario)
