import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pymongo.errors import PyMongoError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes.agent import ThreadStateLoadError, _get_resume_lock, _load_thread_state, _validate_resume_state


class DummyCheckpointer:
    def __init__(self):
        self.deleted = []

    async def adelete_thread(self, thread_id):
        self.deleted.append(thread_id)


class DummyGraph:
    def __init__(self, snapshot=None, error=None):
        self._snapshot = snapshot
        self._error = error
        self.checkpointer = DummyCheckpointer()

    async def aget_state(self, config):
        if self._error is not None:
            raise self._error
        return self._snapshot


CONFIG = {"configurable": {"thread_id": "t"}}


def _run(coro):
    return asyncio.run(coro)


def test_new_thread_without_history_is_treated_as_fresh_state():
    graph = DummyGraph(snapshot=SimpleNamespace(values={}, next=()))

    assert _run(_load_thread_state(graph, CONFIG, "t")) is None
    assert graph.checkpointer.deleted == []


def test_existing_thread_returns_its_snapshot():
    snapshot = SimpleNamespace(values={"chat_history": [1]}, next=())
    graph = DummyGraph(snapshot=snapshot)

    assert _run(_load_thread_state(graph, CONFIG, "t")) is snapshot


def test_missing_thread_with_require_existing_is_invalid():
    graph = DummyGraph(snapshot=SimpleNamespace(values={}, next=()))

    with pytest.raises(ThreadStateLoadError) as exc_info:
        _run(_load_thread_state(graph, CONFIG, "t", require_existing=True))

    assert exc_info.value.code == "invalid_thread"


def test_unreadable_checkpoint_is_deleted_and_reported():
    graph = DummyGraph(error=RuntimeError("cannot deserialize"))

    with pytest.raises(ThreadStateLoadError) as exc_info:
        _run(_load_thread_state(graph, CONFIG, "corrupt-thread"))

    assert exc_info.value.code == "invalid_thread"
    assert graph.checkpointer.deleted == ["corrupt-thread"]


def test_storage_outage_does_not_delete_the_thread():
    graph = DummyGraph(error=PyMongoError("connection lost"))

    with pytest.raises(PyMongoError):
        _run(_load_thread_state(graph, CONFIG, "t"))

    assert graph.checkpointer.deleted == []


def test_completed_workflow_blocks_resume():
    state = SimpleNamespace(next=None, values={"status": "success", "metadata": {}})

    with pytest.raises(ThreadStateLoadError) as exc_info:
        _validate_resume_state(state, "done-thread")

    assert exc_info.value.code == "completed"


def test_cancelled_workflow_blocks_resume():
    state = SimpleNamespace(next=["send_email"], values={"status": "rejected", "metadata": {}})

    with pytest.raises(ThreadStateLoadError) as exc_info:
        _validate_resume_state(state, "cancelled-thread")

    assert exc_info.value.code == "cancelled"


def test_stale_approval_rejected_when_token_mismatch():
    state = SimpleNamespace(next=["send_email"], values={"status": "pending", "metadata": {"resume_token": "old-token"}})

    with pytest.raises(ThreadStateLoadError) as exc_info:
        _validate_resume_state(state, "stale-thread", resume_token="new-token")

    assert exc_info.value.code == "stale_approval"


def test_resume_lock_is_per_thread_and_reused():
    first = _get_resume_lock("thread-a")
    second = _get_resume_lock("thread-a")
    third = _get_resume_lock("thread-b")

    assert first is second
    assert first is not third
