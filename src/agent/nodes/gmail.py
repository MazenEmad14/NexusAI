"""Search the user's Gmail, summarise it and mine new emails for tasks."""

import logging
from agent.deps import AgentDeps
from agent.errors import fail
from helpers.exceptions import DomainError, LLMUnavailable
from agent.google_clients import GOOGLE_CONNECT_MESSAGE
from agent.google_clients import gmail_for_request
from agent.state import AgentState
from controllers.email_summarizer import EmailSummarizerController
from controllers.formatter import ResponseFormatter
from controllers.gmail_query import build_gmail_query
from helpers.exceptions import GoogleAuthRequiredException
from helpers.timezones import local_now
import asyncio

logger = logging.getLogger('uvicorn.error')


async def _store_tasks_from_emails(state: AgentState, db_client, generation_client, emails: list, summaries: list) -> int:
    """Mine new emails for tasks and reminders. Each email is mined once, and a task the user
    already has is not created again, so repeating a request never duplicates anything."""
    if db_client is None:
        return 0

    from controllers.task_extractor import TaskExtractorController, parse_deadline
    from models.ReminderModel import ReminderModel
    from models.TaskModel import TaskModel
    from models.db_schemes.reminder import Reminder
    from models.db_schemes.task import Task

    user_id = state.get("user_id", "")
    project_id = state.get("project_id", "default")
    task_model = await TaskModel.create_instance(db_client)

    message_ids = [email.get("message_id") for email in emails if email.get("message_id")]
    unscanned = set(await task_model.filter_unscanned_emails(user_id, message_ids))
    new_pairs = [(email, summary) for email, summary in zip(emails, summaries) if email.get("message_id") in unscanned]
    if not new_pairs:
        return 0

    extractor = TaskExtractorController(generation_client=generation_client)
    combined_text = "\n".join(summary.get("summary", "") for _, summary in new_pairs)
    extracted = await asyncio.to_thread(extractor.extract_tasks, combined_text)

    reminder_model = await ReminderModel.create_instance(db_client)
    only_email = new_pairs[0][0] if len(new_pairs) == 1 else None
    created = 0
    for data in extracted:
        task = Task(
            project_id=project_id,
            user_id=user_id,
            description=data["description"],
            priority=data.get("priority") or state.get("priority", "medium"),
            source_email_id=only_email.get("message_id") if only_email else None,
            source_subject=only_email.get("subject") if only_email else None,
        )
        # "tomorrow" means tomorrow where the user is, not where the server is.
        parsed = parse_deadline(data.get("deadline"), now=local_now(state.get("user_timezone")).replace(tzinfo=None))
        if parsed:
            task.deadline = parsed[0]

        if not await task_model.create_task_if_new(task):
            continue
        created += 1

        if parsed:
            deadline, has_time = parsed
            await reminder_model.create_reminder(Reminder(
                project_id=project_id,
                user_id=user_id,
                task=f"Deadline: {task.description}",
                date=deadline.strftime("%Y-%m-%d"),
                time=deadline.strftime("%H:%M") if has_time else "",
                priority=task.priority,
                notes="Auto-extracted from email.",
            ))

    await task_model.mark_emails_scanned(user_id, list(unscanned))
    logger.info(f"Extracted {len(extracted)} tasks from {len(new_pairs)} new emails; created {created}.")
    return created


async def search_gmail_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Search the user's Gmail, summarise the matches and extract tasks from new emails."""
    generation_client = deps.generation_client

    logger.info("Searching Gmail...")

    try:
        gmail_service = await asyncio.to_thread(gmail_for_request)
    except GoogleAuthRequiredException:
        state["result"] = GOOGLE_CONNECT_MESSAGE
        state["status"] = "error"
        return state

    try:
        plan = await build_gmail_query(generation_client, state["query"], state.get("chat_history", []))
    except LLMUnavailable as exc:
        return fail(state, exc)
    try:
        emails = await asyncio.to_thread(gmail_service.search_emails, query=plan.query, max_results=plan.max_results)
    except DomainError as exc:  # an expired Google connection and a Gmail outage are told apart
        logger.error(f"Gmail search failed: {exc}")
        return fail(state, exc)
    except Exception:
        logger.error("Gmail search failed", exc_info=True)
        state["result"] = ResponseFormatter.format_error("I couldn't reach Gmail right now. Please try again in a moment.")
        state["status"] = "error"
        return state
    state["emails"] = emails

    if not emails:
        state["result"] = ResponseFormatter.format_no_results()
        state["status"] = "no_results"
        state["metadata"]["source"] = "none"
        logger.info("Gmail search: 0 results")
        return state

    summarizer = EmailSummarizerController(generation_client=generation_client)
    summaries = await asyncio.to_thread(summarizer.summarize_emails, emails)

    state["result"] = ResponseFormatter.format_email_results(summaries)
    state["status"] = "success"
    state["metadata"]["source"] = "gmail"

    try:
        await _store_tasks_from_emails(state, deps.db_client, generation_client, emails, summaries)
    except Exception:
        # Task extraction is a bonus; it must never fail the user's email search.
        logger.error("Task extraction from emails failed", exc_info=True)

    logger.info(f"Gmail search: {len(emails)} results")
    return state
