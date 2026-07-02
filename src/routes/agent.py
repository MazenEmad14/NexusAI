from fastapi import APIRouter, status, Request
from fastapi.responses import JSONResponse
import asyncio
import logging
import uuid

from .schemes.agent import ChatRequest, ChatResponse, ResumeRequest
from controllers.agent_graph import build_agent_graph, create_initial_state
from models.enums.ResponseEnums import ResponseSignal

logger = logging.getLogger('uvicorn.error')


class ThreadStateLoadError(RuntimeError):
    """Raised when a thread checkpoint exists but cannot be loaded safely."""

    def __init__(self, message: str, code: str = "invalid_thread"):
        super().__init__(message)
        self.code = code


_resume_locks: dict[str, asyncio.Lock] = {}
_pending_resume_tokens: dict[str, str] = {}


def _get_resume_lock(thread_id: str) -> asyncio.Lock:
    lock = _resume_locks.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _resume_locks[thread_id] = lock
    return lock


def _clear_thread_checkpoints(agent_graph, thread_id: str) -> None:
    """Completely remove all stored checkpoint entries, writes, and blobs for the provided thread."""
    checkpointer = getattr(agent_graph, "checkpointer", None)
    if not checkpointer:
        return

    # Modern LangGraph provides a built-in delete_thread method
    if hasattr(checkpointer, "delete_thread"):
        try:
            checkpointer.delete_thread(thread_id)
            return
        except Exception as e:
            logger.warning(f"Error calling delete_thread: {e}")

    # Fallback for older versions or custom checkpointers
    if hasattr(checkpointer, "storage") and isinstance(checkpointer.storage, dict):
        if thread_id in checkpointer.storage:
            del checkpointer.storage[thread_id]
        else:
            keys_to_delete = [k for k in checkpointer.storage.keys() if (isinstance(k, tuple) and k[0] == thread_id) or k == thread_id]
            for key in keys_to_delete:
                checkpointer.storage.pop(key, None)

    if hasattr(checkpointer, "writes") and isinstance(checkpointer.writes, dict):
        keys_to_delete = [k for k in checkpointer.writes.keys() if isinstance(k, tuple) and len(k) > 0 and k[0] == thread_id]
        for key in keys_to_delete:
            checkpointer.writes.pop(key, None)
            
    if hasattr(checkpointer, "blobs") and isinstance(checkpointer.blobs, dict):
        keys_to_delete = [k for k in checkpointer.blobs.keys() if isinstance(k, tuple) and len(k) > 0 and k[0] == thread_id]
        for key in keys_to_delete:
            checkpointer.blobs.pop(key, None)


def _has_real_checkpoint(storage, thread_id: str) -> bool:
    """Return True only when a thread has actual checkpoint data, not just an empty container."""
    if storage is None:
        return False

    if thread_id in storage:
        return _contains_checkpoint_payload(storage[thread_id])

    for key in storage.keys():
        if isinstance(key, tuple) and len(key) > 0 and key[0] == thread_id:
            return _contains_checkpoint_payload(storage[key])

    return False


def _contains_checkpoint_payload(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, bytes, int, float, bool)):
        return True
    if isinstance(value, (list, tuple, set)):
        return any(_contains_checkpoint_payload(item) for item in value)
    if isinstance(value, dict):
        if not value:
            return False
        return any(_contains_checkpoint_payload(item) for item in value.values())
    return bool(value)


def _load_thread_state(agent_graph, config: dict, thread_id: str, *, require_existing: bool = False):
    """Load thread state while distinguishing fresh threads from corrupt checkpoints."""
    def _clear_empty_placeholder_if_needed() -> None:
        if not hasattr(agent_graph, "checkpointer") or not hasattr(agent_graph.checkpointer, "storage"):
            return

        storage = agent_graph.checkpointer.storage
        if not _has_real_checkpoint(storage, thread_id):
            _clear_thread_checkpoints(agent_graph, thread_id)
            logger.info(f"Cleared empty checkpoint placeholder for thread {thread_id}")

    _clear_empty_placeholder_if_needed()

    try:
        return agent_graph.get_state(config)
    except KeyError as exc:
        message = str(exc)
        _clear_empty_placeholder_if_needed()

        if not require_existing and message and "**start**" in message:
            logger.info(f"No checkpoint found for new thread {thread_id}; starting fresh")
            return None

        if hasattr(agent_graph, "checkpointer") and hasattr(agent_graph.checkpointer, "storage"):
            storage = agent_graph.checkpointer.storage
            if _has_real_checkpoint(storage, thread_id):
                _clear_thread_checkpoints(agent_graph, thread_id)
                logger.warning(f"Detected corrupted checkpoint for thread {thread_id}; cleared it")
                raise ThreadStateLoadError(f"Checkpoint for thread {thread_id} is invalid", code="invalid_thread") from exc

        if require_existing:
            raise ThreadStateLoadError(f"Thread {thread_id} no longer exists", code="invalid_thread")

        logger.info(f"No checkpoint found for thread {thread_id}; starting fresh")
        return None
    except Exception as exc:
        _clear_empty_placeholder_if_needed()

        if hasattr(agent_graph, "checkpointer") and hasattr(agent_graph.checkpointer, "storage"):
            storage = agent_graph.checkpointer.storage
            if _has_real_checkpoint(storage, thread_id):
                _clear_thread_checkpoints(agent_graph, thread_id)
                logger.warning(f"Detected corrupted checkpoint for thread {thread_id}; cleared it")
                raise ThreadStateLoadError(f"Checkpoint for thread {thread_id} is invalid", code="invalid_thread") from exc

        if require_existing:
            raise ThreadStateLoadError(f"Thread {thread_id} no longer exists", code="invalid_thread")

        logger.info(f"No checkpoint found for thread {thread_id}; starting fresh")
        return None


def _is_start_checkpoint_error(exc: Exception) -> bool:
    message = str(exc)
    return isinstance(exc, KeyError) and message and "**start**" in message


async def _invoke_agent_graph(agent_graph, input_data, config: dict, thread_id: str):
    """Run LangGraph ainvoke while converting startup checkpoint failures into safe thread-state errors."""
    storage = getattr(getattr(agent_graph, "checkpointer", None), "storage", None)
    had_real_checkpoint_before_attempt = bool(storage and _has_real_checkpoint(storage, thread_id))

    try:
        return await agent_graph.ainvoke(input_data, config=config)
    except KeyError as exc:
        if _is_start_checkpoint_error(exc):
            if had_real_checkpoint_before_attempt:
                _clear_thread_checkpoints(agent_graph, thread_id)
                logger.warning(f"Detected corrupted checkpoint during ainvoke for thread {thread_id}; cleared it")
                raise ThreadStateLoadError("Session expired or invalid.", code="invalid_thread") from exc

            _clear_thread_checkpoints(agent_graph, thread_id)
            logger.info(f"Fresh thread startup checkpoint issue during ainvoke for thread {thread_id}; retrying")
            try:
                return await agent_graph.ainvoke(input_data, config=config)
            except KeyError as retry_exc:
                if _is_start_checkpoint_error(retry_exc):
                    if had_real_checkpoint_before_attempt:
                        _clear_thread_checkpoints(agent_graph, thread_id)
                        logger.warning(f"Detected corrupted checkpoint during ainvoke for thread {thread_id}; cleared it")
                        raise ThreadStateLoadError("Session expired or invalid.", code="invalid_thread") from retry_exc
                    logger.warning(f"Startup checkpoint issue persisted for thread {thread_id}; treating as fresh start")
                    return await agent_graph.ainvoke(input_data, config=config)
                raise
        raise
    except Exception as exc:
        if hasattr(agent_graph, "checkpointer") and hasattr(agent_graph.checkpointer, "storage"):
            storage = agent_graph.checkpointer.storage
            if _has_real_checkpoint(storage, thread_id):
                _clear_thread_checkpoints(agent_graph, thread_id)
                logger.warning(f"Detected corrupted checkpoint during ainvoke for thread {thread_id}; cleared it")
                raise ThreadStateLoadError("Session expired or invalid.", code="invalid_thread") from exc
        raise


def _validate_resume_state(state_snapshot, thread_id: str, resume_token: str | None = None):
    """Validate the current state before allowing a resume to proceed."""
    if not state_snapshot:
        raise ThreadStateLoadError(f"Thread {thread_id} no longer exists", code="invalid_thread")

    values = getattr(state_snapshot, "values", None) or {}
    if not isinstance(values, dict):
        raise ThreadStateLoadError(f"Thread {thread_id} state is invalid", code="invalid_thread")

    status = values.get("status", "")
    if status in {"rejected", "cancelled"}:
        raise ThreadStateLoadError("Workflow already cancelled", code="cancelled")

    if not state_snapshot.next:
        raise ThreadStateLoadError("Workflow already completed", code="completed")

    current_nodes = state_snapshot.next
    if isinstance(current_nodes, (list, tuple, set)):
        current_nodes = list(current_nodes)
    else:
        current_nodes = [current_nodes]

    interruptable_nodes = {"send_email", "create_calendar_event", "save_reminder"}
    if not any(node in interruptable_nodes for node in current_nodes):
        raise ThreadStateLoadError("Approval is stale or not interruptible", code="stale_approval")

    metadata = values.get("metadata", {}) or {}
    expected_token = metadata.get("resume_token") or _pending_resume_tokens.get(thread_id)
    if resume_token is not None and expected_token and resume_token != expected_token:
        raise ThreadStateLoadError("Approval token is stale", code="stale_approval")

    return state_snapshot


agent_router = APIRouter(
    prefix="/api/v1/agent",
    tags=["api_v1", "agent"]
)


@agent_router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: Request, chat_request: ChatRequest):
    """
    Main endpoint for the Secretary AI Agent.
    Takes a natural language query and orchestrates the full LangGraph workflow.
    """
    try:
        if not hasattr(request.app, "agent_graph"):
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "signal": ResponseSignal.AGENT_RESPONSE_ERROR.value,
                    "error": "Agent graph not initialized"
                }
            )

        agent_graph = request.app.agent_graph

        config = {"configurable": {"thread_id": chat_request.thread_id}}

        # Preserve existing chat history if any
        try:
            state_snapshot = _load_thread_state(agent_graph, config, chat_request.thread_id)
        except ThreadStateLoadError as exc:
            logger.warning(f"Failed to load state for thread {chat_request.thread_id}: {exc}")
            return ChatResponse(
                response="Thread checkpoint invalid. Restarting session...",
                type="error",
                status="invalid_thread",
                thread_id=chat_request.thread_id
            )

        existing_history = state_snapshot.values.get("chat_history", []) if state_snapshot and state_snapshot.values else []

        initial_state = create_initial_state(
            query=chat_request.query,
            project_id=chat_request.project_id
        )
        initial_state["chat_history"] = existing_history
        if existing_history and state_snapshot and state_snapshot.values:
            metadata = dict(state_snapshot.values.get("metadata", {}) or {})
            initial_state["metadata"] = metadata

        logger.info(f"Starting agent workflow for query: {chat_request.query} [Thread: {chat_request.thread_id}]")
        final_state = await _invoke_agent_graph(agent_graph, initial_state, config, chat_request.thread_id)

        # Check if the graph is paused for human-in-the-loop approval
        try:
            state_snapshot = agent_graph.get_state(config)
        except Exception as exc:
            if _is_start_checkpoint_error(exc):
                logger.warning(f"Checkpoint issue while reading post-invoke state for thread {chat_request.thread_id}")
                return ChatResponse(
                    response="Session expired or invalid.",
                    type="error",
                    status="invalid_thread",
                    thread_id=chat_request.thread_id
                )
            raise
        
        if state_snapshot.next:
            action_type = final_state.get("action_type", "")
            action_details = final_state.get("action_details", {})
            metadata = dict(final_state.get("metadata", {}) or {})
            if not metadata.get("resume_token"):
                token = uuid.uuid4().hex
                metadata["resume_token"] = token
                _pending_resume_tokens[chat_request.thread_id] = token
                try:
                    agent_graph.update_state(config, {"metadata": metadata})
                except Exception as update_exc:
                    logger.warning(f"Could not persist resume token for thread {chat_request.thread_id}: {update_exc}")
            return ChatResponse(
                response=f"Pending approval to execute action: {action_type}",
                type=f"action:{action_type}",
                metadata=metadata,
                status="pending_approval",
                thread_id=chat_request.thread_id,
                action_details=action_details
            )

        response_type = final_state.get("request_type", "")
        if final_state.get("action_type"):
            response_type = f"action:{final_state['action_type']}"

        return ChatResponse(
            response=final_state.get("result", ""),
            type=response_type,
            metadata=final_state.get("metadata", {}),
            status=final_state.get("status", "success"),
            thread_id=chat_request.thread_id,
            action_details={}
        )

    except ThreadStateLoadError as exc:
        logger.warning(f"Thread state error in chat endpoint: {exc}")
        return ChatResponse(
            response="Session expired or invalid.",
            type="error",
            status="invalid_thread",
            thread_id=chat_request.thread_id
        )
    except Exception as e:
        logger.error(f"Error in agent chat endpoint: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "signal": ResponseSignal.AGENT_RESPONSE_ERROR.value,
                "error": "Session expired or invalid."
            }
        )

@agent_router.post("/resume", response_model=ChatResponse)
async def resume_endpoint(request: Request, resume_request: ResumeRequest):
    """
    Endpoint to resume or cancel an interrupted graph execution (HITL Approval).
    """
    try:
        agent_graph = request.app.agent_graph
        config = {"configurable": {"thread_id": resume_request.thread_id}}
        lock = _get_resume_lock(resume_request.thread_id)

        async with lock:
            try:
                state_snapshot = _load_thread_state(agent_graph, config, resume_request.thread_id, require_existing=True)
            except ThreadStateLoadError as exc:
                logger.warning(f"Failed to load state for resume on thread {resume_request.thread_id}: {exc}")
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "signal": ResponseSignal.AGENT_RESPONSE_ERROR.value,
                        "error": "Invalid thread checkpoint. Cannot resume."
                    }
                )

            try:
                state_snapshot = _validate_resume_state(state_snapshot, resume_request.thread_id, resume_request.resume_token)
            except ThreadStateLoadError as exc:
                if exc.code == "cancelled":
                    return JSONResponse(
                        status_code=status.HTTP_409_CONFLICT,
                        content={
                            "signal": ResponseSignal.AGENT_RESPONSE_ERROR.value,
                            "error": "Workflow already cancelled."
                        }
                    )
                if exc.code == "completed":
                    return JSONResponse(
                        status_code=status.HTTP_409_CONFLICT,
                        content={
                            "signal": ResponseSignal.AGENT_RESPONSE_ERROR.value,
                            "error": "Workflow already completed."
                        }
                    )
                if exc.code == "stale_approval":
                    return JSONResponse(
                        status_code=status.HTTP_409_CONFLICT,
                        content={
                            "signal": ResponseSignal.AGENT_RESPONSE_ERROR.value,
                            "error": "Approval is stale or no longer valid."
                        }
                    )
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "signal": ResponseSignal.AGENT_RESPONSE_ERROR.value,
                        "error": "Invalid thread checkpoint. Cannot resume."
                    }
                )

            if not resume_request.approve:
                # User rejected the action, update state as the paused node to skip it
                paused_node = state_snapshot.next[0]

                # Manually append the rejection to chat history so the LLM knows it was cancelled
                existing_history = state_snapshot.values.get("chat_history", [])
                existing_history.append({"role": "assistant", "content": "Action rejected by user."})

                try:
                    agent_graph.update_state(
                        config,
                        {
                            "status": "rejected",
                            "result": "Action rejected by user.",
                            "chat_history": existing_history
                        },
                        as_node=paused_node
                    )
                except Exception as exc:
                    if _is_start_checkpoint_error(exc):
                        logger.warning(f"Checkpoint issue while updating state for rejected resume on thread {resume_request.thread_id}")
                        return ChatResponse(
                            response="Session expired or invalid.",
                            type="error",
                            status="invalid_thread",
                            thread_id=resume_request.thread_id
                        )
                    raise

                # We must resume the graph so it can finish (e.g. format_output_node)
                final_state = await _invoke_agent_graph(agent_graph, None, config, resume_request.thread_id)

                return ChatResponse(
                    response="Action was cancelled by the user.",
                    type="action",
                    status="rejected",
                    thread_id=resume_request.thread_id
                )

            # User approved, resume graph execution
            logger.info(f"Resuming graph execution for thread {resume_request.thread_id}")
            final_state = await _invoke_agent_graph(agent_graph, None, config, resume_request.thread_id)

        response_type = final_state.get("request_type", "")
        if final_state.get("action_type"):
            response_type = f"action:{final_state['action_type']}"

        return ChatResponse(
            response=final_state.get("result", ""),
            type=response_type,
            metadata=final_state.get("metadata", {}),
            status=final_state.get("status", "success"),
            thread_id=resume_request.thread_id,
            action_details={}
        )

    except ThreadStateLoadError as exc:
        logger.warning(f"Thread state error in resume endpoint: {exc}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "signal": ResponseSignal.AGENT_RESPONSE_ERROR.value,
                "error": "Session expired or invalid."
            }
        )
    except Exception as e:
        logger.error(f"Error resuming graph execution: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "signal": ResponseSignal.AGENT_RESPONSE_ERROR.value,
                "error": "Session expired or invalid."
            }
        )
