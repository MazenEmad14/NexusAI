from fastapi import APIRouter, status, Request, File, UploadFile, Depends
from fastapi.responses import JSONResponse
import asyncio
import logging
import os
import uuid
import json

from .schemes.agent import ChatRequest, ChatResponse, ResumeRequest, TTSRequest
from agent import create_initial_state
from models.enums.ResponseEnums import ResponseSignal
from fastapi.responses import StreamingResponse
import io
import hmac
from helpers.config import get_settings
from helpers.services import services_of
from helpers.event_bus import get_user_queue, issue_ticket, redeem_ticket
from helpers.auth_utils import get_current_user
from models.UserModel import UserModel
from helpers.error_handlers import public_message_for
from helpers.google_context import set_google_token
from pymongo.errors import PyMongoError

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


async def _load_thread_state(agent_graph, config: dict, thread_id: str, *, require_existing: bool = False):
    """Load a thread's saved state. Returns None for a thread that has no history yet."""
    try:
        snapshot = await agent_graph.aget_state(config)
    except PyMongoError:
        # A storage outage says nothing about the thread itself; do not touch its data.
        raise
    except Exception as exc:
        # The checkpoint exists but cannot be read (for example after a state schema change).
        logger.error(f"Unreadable checkpoint for thread {thread_id}; deleting it", exc_info=True)
        await agent_graph.checkpointer.adelete_thread(thread_id)
        raise ThreadStateLoadError(f"Checkpoint for thread {thread_id} is invalid", code="invalid_thread") from exc

    if snapshot is None or not (snapshot.values or snapshot.next):
        if require_existing:
            raise ThreadStateLoadError(f"Thread {thread_id} no longer exists", code="invalid_thread")
        return None
    return snapshot


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
        timezone_name=current_user.get("timezone") or "",
    )
    initial_state["chat_history"] = existing_history
    if existing_history and state_snapshot and state_snapshot.values:
        metadata = dict(state_snapshot.values.get("metadata", {}) or {})
        # An approval token only ever belongs to the interrupt that created it.
        metadata.pop("resume_token", None)
        initial_state["metadata"] = metadata

    initial_state["user_email"] = current_user.get("email") or ""
    return initial_state


async def _bind_google_token(request: Request, current_user: dict):
    """Load this user's Google token and bind it to the request context for the graph nodes.

    Returns (holder, original_token); the holder carries any token the nodes refreshed. A database
    failure propagates (503) instead of looking like "no Google account connected".
    """
    token = None
    if current_user.get("email"):
        user_model = await UserModel.create_instance(services_of(request).mongo_db)
        token = await user_model.get_google_token(current_user["email"])
    return set_google_token(token), token


async def _persist_refreshed_token(request: Request, current_user: dict, original_token, new_token):
    """Save a Google token the graph refreshed, so the next request does not refresh again."""
    if not new_token or new_token == original_token or not current_user.get("email"):
        return
    try:
        user_model = await UserModel.create_instance(services_of(request).mongo_db)
        await user_model.update_google_token(current_user["email"], new_token)
    except Exception as exc:
        logger.warning(f"Could not persist refreshed Google token: {exc}")


async def _new_resume_token(agent_graph, config: dict, values: dict) -> dict:
    """Attach a fresh single-use approval token to the paused thread; returns the new metadata."""
    metadata = dict(values.get("metadata", {}) or {})
    metadata["resume_token"] = uuid.uuid4().hex
    await agent_graph.aupdate_state(config, {"metadata": metadata})
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
        agent_graph = services_of(request).agent_graph

        namespaced_thread = f"{str(current_user['_id'])}_{chat_request.thread_id}"
        config = {"configurable": {"thread_id": namespaced_thread}}

        # Preserve existing chat history if any
        try:
            state_snapshot = await _load_thread_state(agent_graph, config, namespaced_thread)
        except ThreadStateLoadError as exc:
            logger.warning(f"Failed to load state for thread {namespaced_thread}: {exc}")
            return ChatResponse(
                response="Thread checkpoint invalid. Restarting session...",
                type="error",
                status="invalid_thread",
                thread_id=chat_request.thread_id
            )

        initial_state = await _build_initial_state(request, chat_request, current_user, state_snapshot)
        holder, original_token = await _bind_google_token(request, current_user)

        logger.info(f"Starting agent workflow [Thread: {namespaced_thread}]")

        final_state = await agent_graph.ainvoke(initial_state, config=config)

        await _persist_refreshed_token(request, current_user, original_token, holder["token"])

        # Check if the graph is paused for human-in-the-loop approval
        state_snapshot = await agent_graph.aget_state(config)

        if state_snapshot.next:
            action_type = final_state.get("action_type", "")
            action_details = final_state.get("action_details", {})
            metadata = await _new_resume_token(agent_graph, config, final_state)
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

@agent_router.post("/resume", response_model=ChatResponse)
async def resume_endpoint(request: Request, resume_request: ResumeRequest, current_user: dict = Depends(get_current_user)):
    """
    Endpoint to resume or cancel an interrupted graph execution (HITL Approval).
    """
    try:
        agent_graph = services_of(request).agent_graph
        namespaced_thread = f"{str(current_user['_id'])}_{resume_request.thread_id}"
        config = {"configurable": {"thread_id": namespaced_thread}}
        lock = _get_resume_lock(namespaced_thread)

        async with lock:
            try:
                state_snapshot = await _load_thread_state(agent_graph, config, namespaced_thread, require_existing=True)
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

                await agent_graph.aupdate_state(
                    config,
                    {
                        "status": "rejected",
                        "result": "Action rejected by user.",
                        "chat_history": existing_history
                    },
                    as_node=paused_node
                )

                # We must resume the graph so it can finish (e.g. format_output_node)
                await agent_graph.ainvoke(None, config=config)

                return ChatResponse(
                    response="Action was cancelled by the user.",
                    type="action",
                    status="rejected",
                    thread_id=resume_request.thread_id
                )

            # User approved, resume graph execution
            logger.info(f"Resuming graph for thread {namespaced_thread} with approval: {resume_request.approve}")
            holder, original_token = await _bind_google_token(request, current_user)
            final_state = await agent_graph.ainvoke(None, config=config)
            await _persist_refreshed_token(request, current_user, original_token, holder["token"])

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

import tempfile
import threading
from gtts import gTTS

try:  # speech-to-text is an optional extra (requirements-voice.txt): it pulls in PyTorch
    import whisper
except ImportError:
    whisper = None


MAX_STT_BYTES = 25 * 1024 * 1024
MAX_TTS_CHARS = 5000

_whisper_model = None
_whisper_lock = threading.Lock()


def _get_whisper_model():
    """Load the Whisper model on first use, not at import time (it is slow and ~150 MB)."""
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            try:  # the bundled ffmpeg lets Whisper decode audio without a system install
                import imageio_ffmpeg
                os.environ["PATH"] += os.pathsep + os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
            except ImportError:
                pass
            _whisper_model = whisper.load_model(get_settings().WHISPER_MODEL)
        return _whisper_model


def _transcribe(path: str) -> str:
    return _get_whisper_model().transcribe(path)["text"]


@agent_router.post("/stt")
async def stt_endpoint(request: Request, file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    """
    Endpoint to convert speech to text using local Whisper.
    """
    if whisper is None:
        return JSONResponse(status_code=503, content={"error": "Speech-to-text is not installed on this server"})

    try:
        audio = await file.read(MAX_STT_BYTES + 1)
        if len(audio) > MAX_STT_BYTES:
            return JSONResponse(status_code=413, content={"error": "Audio file too large"})

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp.write(audio)
            tmp_path = tmp.name

        try:
            text = await asyncio.to_thread(_transcribe, tmp_path)
        finally:
            os.remove(tmp_path)

        return {"text": text}
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
    agent_graph = services_of(request).agent_graph
    namespaced_thread = f"{str(current_user['_id'])}_{chat_request.thread_id}"
    config = {"configurable": {"thread_id": namespaced_thread}}

    try:
        state_snapshot = await _load_thread_state(agent_graph, config, namespaced_thread)
    except ThreadStateLoadError as exc:
        logger.warning(f"Failed to load state for thread {namespaced_thread}: {exc}")
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'content': 'Thread checkpoint invalid'})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    initial_state = await _build_initial_state(request, chat_request, current_user, state_snapshot)

    async def stream_graph_execution():
        try:
            # Bound here, inside the generator, so the token is in the context the graph runs in.
            holder, original_token = await _bind_google_token(request, current_user)
            async for event in agent_graph.astream_events(initial_state, config, version="v2"):
                if event["event"] == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    if chunk and hasattr(chunk, "content"):
                        token = chunk.content
                        if token:
                            yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

            # Execution finished or interrupted
            state = await agent_graph.aget_state(config)
            await _persist_refreshed_token(request, current_user, original_token, holder["token"])
            
            if state and state.next:
                # HITL interrupt
                final_vals = state.values
                action_type = final_vals.get("action_type", "")
                action_details = final_vals.get("action_details", {})
                metadata = await _new_resume_token(agent_graph, config, final_vals)

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
            yield f"data: {json.dumps({'type': 'error', 'content': public_message_for(e), 'code': getattr(e, 'code', 'internal_error')})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(stream_graph_execution(), media_type="text/event-stream")
