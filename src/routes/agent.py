from fastapi import APIRouter, status, Request, File, UploadFile, Depends
from fastapi.responses import JSONResponse
import asyncio
import logging
import uuid
import json

from .schemes.agent import ChatRequest, ChatResponse, ResumeRequest, TTSRequest
from controllers.agent_graph import build_agent_graph, create_initial_state
from models.enums.ResponseEnums import ResponseSignal
from fastapi.responses import StreamingResponse
import io
import asyncio
from stores.llm.providers.OpenAIProvider import OpenAIProvider
from helpers.config import get_settings
from helpers.event_bus import proactive_messages_queue
from helpers.auth_utils import get_current_user

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
                logger.error(f"Actual exception during ainvoke: {exc}", exc_info=True)
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
async def chat_endpoint(request: Request, chat_request: ChatRequest, current_user: dict = Depends(get_current_user)):
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

        namespaced_thread = f"{str(current_user['_id'])}_{chat_request.thread_id}"
        config = {"configurable": {"thread_id": namespaced_thread}}

        # Preserve existing chat history if any
        try:
            state_snapshot = _load_thread_state(agent_graph, config, namespaced_thread)
        except ThreadStateLoadError as exc:
            logger.warning(f"Failed to load state for thread {namespaced_thread}: {exc}")
            return ChatResponse(
                response="Thread checkpoint invalid. Restarting session...",
                type="error",
                status="invalid_thread",
                thread_id=chat_request.thread_id
            )

        existing_history = state_snapshot.values.get("chat_history", []) if state_snapshot and state_snapshot.values else []
        if len(existing_history) > 10:
            existing_history = existing_history[-10:]

        initial_state = create_initial_state(
            query=chat_request.query,
            project_id=chat_request.project_id,
            file_path=chat_request.file_path,
            user_id=str(current_user["_id"])
        )
        initial_state["chat_history"] = existing_history
        if existing_history and state_snapshot and state_snapshot.values:
            metadata = dict(state_snapshot.values.get("metadata", {}) or {})
            initial_state["metadata"] = metadata

        logger.info(f"Starting agent workflow for query: {chat_request.query} [Thread: {namespaced_thread}]")

        final_state = await _invoke_agent_graph(agent_graph, initial_state, config, namespaced_thread)

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
            action_details={},
            chart_data=final_state.get("chart_data")
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
async def resume_endpoint(request: Request, resume_request: ResumeRequest, current_user: dict = Depends(get_current_user)):
    """
    Endpoint to resume or cancel an interrupted graph execution (HITL Approval).
    """
    try:
        agent_graph = request.app.agent_graph
        namespaced_thread = f"{str(current_user['_id'])}_{resume_request.thread_id}"
        config = {"configurable": {"thread_id": namespaced_thread}}
        lock = _get_resume_lock(namespaced_thread)

        async with lock:
            try:
                state_snapshot = _load_thread_state(agent_graph, config, namespaced_thread, require_existing=True)
            except ThreadStateLoadError as exc:
                logger.warning(f"Failed to load state for resume on thread {namespaced_thread}: {exc}")
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "signal": ResponseSignal.AGENT_RESPONSE_ERROR.value,
                        "error": "Invalid thread checkpoint. Cannot resume."
                    }
                )

            try:
                state_snapshot = _validate_resume_state(state_snapshot, namespaced_thread, resume_request.resume_token)
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
                final_state = await _invoke_agent_graph(agent_graph, None, config, namespaced_thread)

                return ChatResponse(
                    response="Action was cancelled by the user.",
                    type="action",
                    status="rejected",
                    thread_id=resume_request.thread_id
                )

            # User approved, resume graph execution
            logger.info(f"Resuming graph for thread {namespaced_thread} with approval: {resume_request.approve}")
            final_state = await _invoke_agent_graph(agent_graph, None, config, namespaced_thread)

        response_type = final_state.get("request_type", "")
        if final_state.get("action_type"):
            response_type = f"action:{final_state['action_type']}"

        return ChatResponse(
            response=final_state.get("result", ""),
            type=response_type,
            metadata=final_state.get("metadata", {}),
            status=final_state.get("status", "success"),
            thread_id=resume_request.thread_id,
            action_details={},
            chart_data=final_state.get("chart_data")
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

import tempfile
import os
from gtts import gTTS
import whisper
import os

try:
    import imageio_ffmpeg
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    os.environ["PATH"] += os.pathsep + os.path.dirname(ffmpeg_exe)
except ImportError:
    pass


whisper_model = None
try:
    whisper_model = whisper.load_model("base")
except Exception as e:
    logger.error(f"Failed to load whisper model: {e}")

@agent_router.post("/stt")
async def stt_endpoint(request: Request, file: UploadFile = File(...)):
    """
    Endpoint to convert speech to text using local Whisper.
    """
    if whisper_model is None:
        return JSONResponse(status_code=500, content={"error": "Whisper model not loaded"})
    
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name
            
        result = whisper_model.transcribe(tmp_path)
        os.remove(tmp_path)
        
        return {"text": result["text"]}
    except Exception as e:
        logger.error(f"Error in STT endpoint: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})

@agent_router.post("/tts")
async def tts_endpoint(request: Request, tts_request: TTSRequest):
    """
    Endpoint to convert text to speech using local gTTS.
    """
    try:
        tts = gTTS(text=tts_request.text, lang='en')
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return StreamingResponse(fp, media_type="audio/mpeg")
    except Exception as e:
        logger.error(f"Error in TTS endpoint: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": str(e)}
        )

@agent_router.get("/notifications")
async def notifications_endpoint(request: Request):
    """
    SSE endpoint for proactive agent notifications.
    """
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                message = await asyncio.wait_for(proactive_messages_queue.get(), timeout=2.0)
                yield f"data: {message}\n\n"
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error in SSE generator: {e}")
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@agent_router.post("/chat/stream")
async def chat_stream_endpoint(request: Request, chat_request: ChatRequest, current_user: dict = Depends(get_current_user)):
    """
    Streaming endpoint for the Secretary AI Agent using LangGraph astream_events.
    """
    if not hasattr(request.app, "agent_graph"):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Agent graph not initialized"}
        )

    agent_graph = request.app.agent_graph
    namespaced_thread = f"{str(current_user['_id'])}_{chat_request.thread_id}"
    config = {"configurable": {"thread_id": namespaced_thread}}

    try:
        state_snapshot = _load_thread_state(agent_graph, config, namespaced_thread)
    except ThreadStateLoadError as exc:
        logger.warning(f"Failed to load state for thread {namespaced_thread}: {exc}")
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'content': 'Thread checkpoint invalid'})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    existing_history = state_snapshot.values.get("chat_history", []) if state_snapshot and state_snapshot.values else []
    if len(existing_history) > 10:
        existing_history = existing_history[-10:]

    initial_state = create_initial_state(
        query=chat_request.query,
        project_id=chat_request.project_id,
        file_path=chat_request.file_path,
        user_id=str(current_user["_id"])
    )
    initial_state["chat_history"] = existing_history
    if existing_history and state_snapshot and state_snapshot.values:
        metadata = dict(state_snapshot.values.get("metadata", {}) or {})
        initial_state["metadata"] = metadata

    async def stream_graph_execution():
        try:
            async for event in agent_graph.astream_events(initial_state, config, version="v2"):
                if event["event"] == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    if chunk and hasattr(chunk, "content"):
                        token = chunk.content
                        if token:
                            yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

            # Execution finished or interrupted
            state = agent_graph.get_state(config)
            
            if state and state.next:
                # HITL interrupt
                final_vals = state.values
                action_type = final_vals.get("action_type", "")
                action_details = final_vals.get("action_details", {})
                metadata = dict(final_vals.get("metadata", {}) or {})
                if not metadata.get("resume_token"):
                    token = uuid.uuid4().hex
                    metadata["resume_token"] = token
                    _pending_resume_tokens[namespaced_thread] = token
                    agent_graph.update_state(config, {"metadata": metadata})

                yield f"data: {json.dumps({'type': 'pending_action', 'action_type': action_type, 'action_details': action_details, 'metadata': metadata})}\n\n"
            else:
                final_vals = state.values if state else {}
                metadata = final_vals.get("metadata", {})
                chart_data = final_vals.get("chart_data", None)
                result_text = final_vals.get("result", "")
                
                if result_text:
                    yield f"data: {json.dumps({'type': 'token', 'content': result_text})}\n\n"
                    
                yield f"data: {json.dumps({'type': 'metadata', 'metadata': metadata, 'chart_data': chart_data, 'status': final_vals.get('status'), 'action_type': final_vals.get('action_type'), 'request_type': final_vals.get('request_type')})}\n\n"

        except Exception as e:
            logger.error(f"Error in stream_graph_execution: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'content': 'Error during execution'})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(stream_graph_execution(), media_type="text/event-stream")
