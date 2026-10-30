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
import hmac
from stores.llm.providers.OpenAIProvider import OpenAIProvider
from helpers.config import get_settings
from helpers.event_bus import get_user_queue, issue_ticket, redeem_ticket
from helpers.auth_utils import get_current_user
from models.UserModel import UserModel

logger = logging.getLogger('uvicorn.error')


class ThreadStateLoadError(RuntimeError):
    """Raised when a thread checkpoint exists but cannot be loaded safely."""

    def __init__(self, message: str, code: str = "invalid_thread"):
        super().__init__(message)
        self.code = code


_resume_locks: dict[str, asyncio.Lock] = {}


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
    expected_token = metadata.get("resume_token")
    if not expected_token or not resume_token or not hmac.compare_digest(str(resume_token), str(expected_token)):
        raise ThreadStateLoadError("Approval token is missing or stale", code="stale_approval")

    return state_snapshot


async def _build_initial_state(request: Request, chat_request: ChatRequest, current_user: dict, state_snapshot):
    """Initial graph state for a chat turn: carried-over history plus this user's Google token."""
    existing_history = state_snapshot.values.get("chat_history", []) if state_snapshot and state_snapshot.values else []
    if len(existing_history) > 10:
        existing_history = existing_history[-10:]

    initial_state = create_initial_state(
        query=chat_request.query,
        project_id=chat_request.project_id,
        file_path=chat_request.file_path,
        user_id=str(current_user["_id"]),
    )
    initial_state["chat_history"] = existing_history
    if existing_history and state_snapshot and state_snapshot.values:
        metadata = dict(state_snapshot.values.get("metadata", {}) or {})
        # An approval token only ever belongs to the interrupt that created it.
        metadata.pop("resume_token", None)
        initial_state["metadata"] = metadata

    user_email = current_user.get("email")
    if user_email:
        try:
            user_model = await UserModel.create_instance(request.app.mongo_db)
            google_token = await user_model.get_google_token(user_email)
            if google_token:
                initial_state["google_token"] = google_token
                initial_state["user_email"] = user_email
        except Exception as exc:
            logger.warning(f"Could not load per-user Google token: {exc}")
    return initial_state


async def _persist_refreshed_token(request: Request, current_user: dict, original_token, new_token):
    """Save a Google token the graph refreshed, so the next request does not refresh again."""
    if not new_token or new_token == original_token or not current_user.get("email"):
        return
    try:
        user_model = await UserModel.create_instance(request.app.mongo_db)
        await user_model.update_google_token(current_user["email"], new_token)
    except Exception as exc:
        logger.warning(f"Could not persist refreshed Google token: {exc}")


def _new_resume_token(agent_graph, config: dict, values: dict) -> dict:
    """Attach a fresh single-use approval token to the paused thread; returns the new metadata."""
    metadata = dict(values.get("metadata", {}) or {})
    metadata["resume_token"] = uuid.uuid4().hex
    agent_graph.update_state(config, {"metadata": metadata})
    return metadata


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

        initial_state = await _build_initial_state(request, chat_request, current_user, state_snapshot)
        original_token = initial_state.get("google_token")

        logger.info(f"Starting agent workflow [Thread: {namespaced_thread}]")

        final_state = await _invoke_agent_graph(agent_graph, initial_state, config, namespaced_thread)

        await _persist_refreshed_token(
            request, current_user, original_token, final_state.get("google_token") if final_state else None
        )

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
            metadata = _new_resume_token(agent_graph, config, final_state)
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
                existing_history = list(state_snapshot.values.get("chat_history", []))
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
            original_token = state_snapshot.values.get("google_token")
            final_state = await _invoke_agent_graph(agent_graph, None, config, namespaced_thread)
            await _persist_refreshed_token(request, current_user, original_token, final_state.get("google_token"))

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


MAX_STT_BYTES = 25 * 1024 * 1024
MAX_TTS_CHARS = 5000

whisper_model = None
try:
    whisper_model = whisper.load_model("base")
except Exception as e:
    logger.error(f"Failed to load whisper model: {e}")

@agent_router.post("/stt")
async def stt_endpoint(request: Request, file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    """
    Endpoint to convert speech to text using local Whisper.
    """
    if whisper_model is None:
        return JSONResponse(status_code=500, content={"error": "Whisper model not loaded"})
    
    try:
        audio = await file.read(MAX_STT_BYTES + 1)
        if len(audio) > MAX_STT_BYTES:
            return JSONResponse(status_code=413, content={"error": "Audio file too large"})

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp.write(audio)
            tmp_path = tmp.name

        try:
            result = await asyncio.to_thread(whisper_model.transcribe, tmp_path)
        finally:
            os.remove(tmp_path)

        return {"text": result["text"]}
    except Exception:
        logger.error("Error in STT endpoint", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "Speech transcription failed"})

@agent_router.post("/tts")
async def tts_endpoint(request: Request, tts_request: TTSRequest, current_user: dict = Depends(get_current_user)):
    """
    Endpoint to convert text to speech using local gTTS.
    """
    try:
        tts = gTTS(text=tts_request.text[:MAX_TTS_CHARS], lang='en')
        fp = io.BytesIO()
        await asyncio.to_thread(tts.write_to_fp, fp)
        fp.seek(0)
        return StreamingResponse(fp, media_type="audio/mpeg")
    except Exception:
        logger.error("Error in TTS endpoint", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Text-to-speech failed"}
        )

@agent_router.post("/notifications/ticket")
async def notifications_ticket(current_user: dict = Depends(get_current_user)):
    """Issue a short-lived one-time ticket for opening the notifications stream.

    The browser's EventSource cannot send an Authorization header, so it passes this
    ticket as a query parameter instead of the long-lived JWT.
    """
    return {"ticket": issue_ticket(str(current_user["_id"]))}


@agent_router.get("/notifications")
async def notifications_endpoint(request: Request, ticket: str):
    """
    SSE endpoint for the caller's own proactive agent notifications.
    """
    user_id = redeem_ticket(ticket)
    if user_id is None:
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"error": "Invalid or expired ticket"})

    queue = get_user_queue(user_id)

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                message = await asyncio.wait_for(queue.get(), timeout=2.0)
                # JSON-encode so newlines in the message cannot break SSE framing.
                yield f"data: {json.dumps(message, ensure_ascii=False)}\n\n"
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

    initial_state = await _build_initial_state(request, chat_request, current_user, state_snapshot)
    original_token = initial_state.get("google_token")

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
            await _persist_refreshed_token(
                request, current_user, original_token, state.values.get("google_token") if state else None
            )
            
            if state and state.next:
                # HITL interrupt
                final_vals = state.values
                action_type = final_vals.get("action_type", "")
                action_details = final_vals.get("action_details", {})
                metadata = _new_resume_token(agent_graph, config, final_vals)

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
