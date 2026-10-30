"""Integration tests for the MongoDB checkpointer. They use a throwaway database on
the local MongoDB and are skipped when none is reachable."""
import asyncio
import sys
import uuid
from pathlib import Path
from typing import TypedDict

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from langgraph.graph import END, StateGraph

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.google_context import get_google_token, set_google_token
from stores.checkpoint.MongoCheckpointer import MongoCheckpointer

MONGO_URL = "mongodb://localhost:27017/"


def _mongo_available() -> bool:
    import pymongo

    try:
        pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=1000).server_info()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mongo_available(), reason="MongoDB is not reachable")


class State(TypedDict):
    history: list
    seen_token: str


def _build_graph(checkpointer):
    def prepare(state):
        return {"history": state["history"] + ["prepared"]}

    def act(state):  # sync node, like send_email_node: must still see the request context
        token = get_google_token() or {}
        return {"history": state["history"] + ["acted"], "seen_token": token.get("user", "")}

    graph = StateGraph(State)
    graph.add_node("prepare", prepare)
    graph.add_node("act", act)
    graph.set_entry_point("prepare")
    graph.add_edge("prepare", "act")
    graph.add_edge("act", END)
    return graph.compile(checkpointer=checkpointer, interrupt_before=["act"])


def _with_db(coro_fn):
    """Run coro_fn(db) against a throwaway database inside one event loop, then drop it."""

    async def runner():
        client = AsyncIOMotorClient(MONGO_URL)
        name = f"test_ckpt_{uuid.uuid4().hex[:8]}"
        try:
            return await coro_fn(client[name])
        finally:
            await client.drop_database(name)
            client.close()

    return asyncio.run(runner())


def test_pending_approval_survives_a_restart_and_can_be_resumed():
    async def scenario(db):
        config = {"configurable": {"thread_id": "user1_thread1"}}

        # "Process 1": run until the approval interrupt.
        first = MongoCheckpointer(db)
        await first.setup()
        graph1 = _build_graph(first)
        await graph1.ainvoke({"history": [], "seen_token": ""}, config=config)
        paused = await graph1.aget_state(config)
        assert paused.next == ("act",)

        # "Process 2": a brand new checkpointer and graph, as after a server restart.
        graph2 = _build_graph(MongoCheckpointer(db))
        restored = await graph2.aget_state(config)
        assert restored.next == ("act",)
        assert restored.values["history"] == ["prepared"]

        set_google_token({"user": "alice"})
        final = await graph2.ainvoke(None, config=config)
        assert final["history"] == ["prepared", "acted"]
        # The request-scoped token reached the sync node without being stored in state.
        assert final["seen_token"] == "alice"
        assert "refresh_token" not in str(await db["lg_checkpoints"].find_one({}))

        done = await graph2.aget_state(config)
        assert done.next == ()

    _with_db(scenario)


def test_state_updates_and_unknown_threads():
    async def scenario(db):
        saver = MongoCheckpointer(db)
        await saver.setup()
        graph = _build_graph(saver)
        config = {"configurable": {"thread_id": "t1"}}

        empty = await graph.aget_state({"configurable": {"thread_id": "never-used"}})
        assert not empty.values and not empty.next

        await graph.ainvoke({"history": [], "seen_token": ""}, config=config)
        await graph.aupdate_state(config, {"history": ["prepared", "edited"]})
        assert (await graph.aget_state(config)).values["history"] == ["prepared", "edited"]

    _with_db(scenario)


def test_delete_thread_and_purge_stale_threads():
    async def scenario(db):
        saver = MongoCheckpointer(db)
        await saver.setup()
        graph = _build_graph(saver)
        for thread in ("keep", "drop", "old"):
            await graph.ainvoke({"history": [], "seen_token": ""}, config={"configurable": {"thread_id": thread}})

        await saver.adelete_thread("drop")
        assert not (await graph.aget_state({"configurable": {"thread_id": "drop"}})).values
        assert await db["lg_checkpoint_blobs"].count_documents({"thread_id": "drop"}) == 0
        assert await db["lg_checkpoint_writes"].count_documents({"thread_id": "drop"}) == 0

        # Age one thread, then purge anything idle for more than 30 days.
        from datetime import datetime, timedelta, timezone

        await db["lg_checkpoints"].update_many(
            {"thread_id": "old"}, {"$set": {"updated_at": datetime.now(timezone.utc) - timedelta(days=45)}}
        )
        assert await saver.purge_stale_threads(30) == 1
        assert (await graph.aget_state({"configurable": {"thread_id": "keep"}})).values
        assert not (await graph.aget_state({"configurable": {"thread_id": "old"}})).values

    _with_db(scenario)


def test_threads_are_isolated_from_each_other():
    async def scenario(db):
        saver = MongoCheckpointer(db)
        await saver.setup()
        graph = _build_graph(saver)
        await graph.ainvoke({"history": ["a"], "seen_token": ""}, config={"configurable": {"thread_id": "alice_t"}})
        await graph.ainvoke({"history": ["b"], "seen_token": ""}, config={"configurable": {"thread_id": "bob_t"}})

        alice = await graph.aget_state({"configurable": {"thread_id": "alice_t"}})
        bob = await graph.aget_state({"configurable": {"thread_id": "bob_t"}})
        assert alice.values["history"] == ["a", "prepared"]
        assert bob.values["history"] == ["b", "prepared"]

    _with_db(scenario)
