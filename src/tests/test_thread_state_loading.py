import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes.agent import ThreadStateLoadError, _get_resume_lock, _invoke_agent_graph, _load_thread_state, _validate_resume_state


class DummyGraph:
    def __init__(self, state=None, error=None, storage=None):
        self._state = state
        self._error = error
        self.checkpointer = SimpleNamespace(storage=storage or {})

    def get_state(self, config):
        if self._error is not None:
            raise self._error
        return self._state


def test_new_thread_without_checkpoint_is_treated_as_fresh_state():
    graph = DummyGraph(error=KeyError("checkpoint for '**start**' missing"), storage={})

    state = _load_thread_state(graph, {"configurable": {"thread_id": "new-thread"}}, "new-thread")

    assert state is None
    assert graph.checkpointer.storage == {}


def test_corrupt_checkpoint_triggers_cleanup_and_error():
    storage = {("corrupt-thread",): {"bad": True}}
    graph = DummyGraph(error=RuntimeError("checkpoint malformed"), storage=storage)

    with pytest.raises(ThreadStateLoadError):
        _load_thread_state(graph, {"configurable": {"thread_id": "corrupt-thread"}}, "corrupt-thread")

    assert graph.checkpointer.storage == {}


def test_new_thread_with_empty_storage_entry_is_treated_as_fresh_state():
    class EmptyEntryGraph(DummyGraph):
        def get_state(self, config):
            self.checkpointer.storage[config["configurable"]["thread_id"]] = {"": {}}
            raise KeyError("checkpoint for '**start**' missing")

    graph = EmptyEntryGraph(error=None, storage={})

    state = _load_thread_state(graph, {"configurable": {"thread_id": "empty-thread"}}, "empty-thread")

    assert state is None
    assert graph.checkpointer.storage == {}


def test_expired_thread_requires_existing_checkpoint():
    graph = DummyGraph(error=KeyError("checkpoint for '**start**' missing"), storage={})

    with pytest.raises(ThreadStateLoadError) as exc_info:
        _load_thread_state(graph, {"configurable": {"thread_id": "expired-thread"}}, "expired-thread", require_existing=True)

    assert exc_info.value.code == "invalid_thread"


def test_ainvoke_with_start_error_for_new_thread_retries_and_succeeds():
    class AsyncInvokeGraph(DummyGraph):
        def __init__(self):
            super().__init__(state=None, error=None, storage={})
            self.calls = 0

        async def ainvoke(self, input_data, config=None):
            self.calls += 1
            if self.calls == 1:
                raise KeyError("checkpoint for '**start**' missing")
            return {"status": "success"}

    graph = AsyncInvokeGraph()

    async def run():
        return await _invoke_agent_graph(graph, {"query": "hello"}, {"configurable": {"thread_id": "fresh-thread"}}, "fresh-thread")

    result = asyncio.run(run())

    assert result == {"status": "success"}
    assert graph.calls == 2


def test_ainvoke_with_start_error_for_corrupt_checkpoint_raises_invalid_thread():
    class CorruptAsyncInvokeGraph(DummyGraph):
        async def ainvoke(self, input_data, config=None):
            raise KeyError("checkpoint for '**start**' missing")

    graph = CorruptAsyncInvokeGraph(state=None, error=None, storage={("corrupt-thread",): {"bad": True}})

    async def run():
        await _invoke_agent_graph(graph, {"query": "hello"}, {"configurable": {"thread_id": "corrupt-thread"}}, "corrupt-thread")

    with pytest.raises(ThreadStateLoadError) as exc_info:
        asyncio.run(run())

    assert exc_info.value.code == "invalid_thread"


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
