"""Google Drive: find and read the user's files, and (after approval) create documents and upload files."""

import asyncio
import logging
import mimetypes
import os

from agent.deps import AgentDeps
from agent.errors import fail
from agent.google_clients import GOOGLE_CONNECT_MESSAGE, drive_for_request
from agent.state import AgentState
from controllers.formatter import ResponseFormatter
from controllers.llm_schemas import DrivePlan
from helpers.exceptions import DomainError, GoogleAuthRequiredException, LLMOutputError, LLMUnavailable, ValidationFailed
from helpers.llm_json import llm_json
from helpers.project_files import resolve_project_file
from helpers.prompts import render

logger = logging.getLogger('uvicorn.error')


async def _plan(generation_client, state: AgentState) -> DrivePlan:
    """The search plan for the request. A reply that is not usable becomes a plain search for the user's words."""
    query = state["query"]
    try:
        return await llm_json(
            generation_client,
            render("drive", "plan", user_query=query.replace('"', "'")),
            DrivePlan,
            chat_history=state.get("chat_history", []),
            max_output_tokens=200,
            temperature=0.0,
            json_mode=True,
        )
    except LLMOutputError as exc:
        logger.warning(f"Drive plan reply unusable ({exc}); searching for the request text")
        return DrivePlan(text=query[:100])


async def drive_search_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Find files in the user's Drive; when they asked for the content, read the best match and answer from it."""
    generation_client = deps.generation_client

    try:
        drive = await asyncio.to_thread(drive_for_request)
    except GoogleAuthRequiredException as exc:
        state["result"] = str(exc) if str(exc) and "No Google account" not in str(exc) else GOOGLE_CONNECT_MESSAGE
        state["status"] = "error"
        return state
    except DomainError as exc:
        return fail(state, exc)

    try:
        plan = await _plan(generation_client, state)
    except LLMUnavailable as exc:
        return fail(state, exc)

    logger.info(f"Drive search: operation={plan.operation} kind={plan.kind} "
                f"has_name={bool(plan.name)} has_text={bool(plan.text)} days={plan.modified_days}")

    try:
        files = await asyncio.to_thread(
            drive.search_files, text=plan.text, name=plan.name, kind=plan.kind,
            modified_days=plan.modified_days, max_results=plan.max_results)
    except DomainError as exc:
        logger.error(f"Drive search failed: {exc}")
        return fail(state, exc)

    if not files:
        state["result"] = ResponseFormatter.format_no_results()
        state["status"] = "no_results"
        state["metadata"]["source"] = "drive"
        return state

    state["metadata"]["source"] = "drive"

    if plan.operation != "read":
        state["result"] = ResponseFormatter.format_drive_results(files)
        state["status"] = "success"
        return state

    best = files[0]
    try:
        document = await asyncio.to_thread(drive.read_file_text, best["file_id"])
    except ValidationFailed as exc:
        # The best match has no readable text (an image, say): still show what was found.
        state["result"] = f"{exc.public_message}\n\n{ResponseFormatter.format_drive_results(files)}"
        state["status"] = "success"
        return state
    except DomainError as exc:
        return fail(state, exc)

    if not document["text"]:
        state["result"] = f"I found '{best['name']}', but it has no text to read.\n\n{best.get('link', '')}".strip()
        state["status"] = "success"
        return state

    if generation_client is None:
        return fail(state, LLMUnavailable("No language model is configured."))
    try:
        answer = await generation_client.agenerate_text(
            prompt=render("drive", "answer", query=state["query"], name=document["name"], content=document["text"]),
            chat_history=state.get("chat_history", []),
            max_output_tokens=600,
            temperature=0.2,
        )
    except LLMUnavailable as exc:
        return fail(state, exc)

    if not answer:
        state["result"] = ResponseFormatter.format_error("I couldn't summarise that file right now. Please try again.")
        state["status"] = "error"
        return state

    state["result"] = ResponseFormatter.format_drive_answer(document, answer.strip(), document["truncated"])
    state["status"] = "success"
    state["metadata"]["file_name"] = document["name"]
    return state


async def drive_save_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Create a Google Doc or upload one of the user's files to Drive (runs only after the user approves)."""
    details = state.get("action_details", {})
    operation = details.get("operation") or "create_document"

    try:
        drive = await asyncio.to_thread(drive_for_request)
    except GoogleAuthRequiredException as exc:
        state["result"] = str(exc) if str(exc) and "No Google account" not in str(exc) else GOOGLE_CONNECT_MESSAGE
        state["status"] = "error"
        return state
    except DomainError as exc:
        return fail(state, exc)

    try:
        if operation == "upload_file":
            path = resolve_project_file(state.get("user_id", ""), state.get("project_id", "default"), details.get("file_name", ""))
            if path is None:
                state["result"] = ResponseFormatter.format_error(
                    "I couldn't find that file among your uploads, so nothing was saved to Drive.")
                state["status"] = "error"
                return state
            data = await asyncio.to_thread(_read_bytes, path)
            name = os.path.basename(path)
            mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            result = await asyncio.to_thread(drive.upload_file, name, data, mime_type)
        else:
            if not (details.get("content") or "").strip():
                state["result"] = ResponseFormatter.format_error(
                    "I didn't have any text to put in the document, so nothing was created.")
                state["status"] = "error"
                return state
            result = await asyncio.to_thread(
                drive.create_document, details.get("title") or "Untitled note", details.get("content", ""))
    except DomainError as exc:
        return fail(state, exc)

    state["result"] = ResponseFormatter.format_drive_saved(result, operation)
    state["status"] = "success"
    state["metadata"]["source"] = "drive_save"
    state["metadata"]["file_id"] = result.get("file_id", "")
    return state


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()
