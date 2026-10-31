"""Answer from the user's documents (plus tasks, reminders or calendar when the question asks)."""

import logging
from agent.deps import AgentDeps
from agent.errors import fail
from helpers.exceptions import DomainError, ExternalServiceError, LLMOutputError
from helpers.llm_json import llm_json
from agent.google_clients import calendar_for_request
from agent.state import AgentState
from controllers.NLPController import NLPController
from controllers.formatter import ResponseFormatter
from helpers.exceptions import GoogleAuthRequiredException
from helpers.project_scope import scoped_project_id
from services.retrieval import KBAnswer
from helpers.prompts import render
from services.retrieval import choose_sources
from services.retrieval import detect_priority
from services.retrieval import rerank_results
import asyncio

logger = logging.getLogger('uvicorn.error')


async def search_knowledge_base_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Answer from the user's documents, plus tasks/reminders/calendar when the question asks for them."""
    vectordb_client = deps.vectordb_client
    generation_client = deps.generation_client
    embedding_client = deps.embedding_client
    template_parser = deps.template_parser
    db_client = deps.db_client

    nlp_controller = NLPController(
        vectordb_client=vectordb_client,
        generation_client=generation_client,
        embedding_client=embedding_client,
        template_parser=template_parser,
    )

    from models.db_schemes import Project
    try:
        project = Project(project_id=scoped_project_id(state.get("user_id", ""), state.get("project_id", "default")))
    except ValueError:
        project = None

    failed: dict = {}  # source name -> the error, for sources that could not be searched

    async def _kb_search() -> list:
        if not (vectordb_client and embedding_client and project):
            return []
        try:
            results = await asyncio.to_thread(
                nlp_controller.search_vector_db_collection,
                project,
                state["query"],
                5,
            )
            if results and results is not False:
                return [{
                    "text": getattr(r, "text", ""),
                    "score": getattr(r, "score", 0.0),
                    "source": "knowledge_base",
                    "source_confidence": 0.95,
                    "relevance": getattr(r, "score", 0.0),
                    "date": None,
                } for r in results]
        except Exception as exc:
            logger.error(f"KB search failed: {exc}")
            failed["knowledge_base"] = exc
        return []

    async def _task_search() -> list:
        if db_client is None:
            return []
        try:
            from models.TaskModel import TaskModel
            task_model = await TaskModel.create_instance(db_client)
            tasks = await task_model.search_tasks(state.get("project_id", "default"), state.get("user_id", ""), state["query"], limit=3)
            return [{
                "text": f"Task: {task.description} (deadline: {task.deadline or 'None'}, priority: {getattr(task, 'priority', 'medium')})",
                "score": 0.9,
                "source": "tasks",
                "source_confidence": 0.95,
                "relevance": 0.95,
                "date": task.deadline,
                "priority": getattr(task, "priority", state.get("priority", "medium")),
            } for task in tasks]
        except Exception as exc:
            logger.error(f"Task search failed: {exc}")
            failed["tasks"] = exc
        return []

    async def _calendar_search() -> list:
        try:
            calendar_service = await asyncio.to_thread(calendar_for_request)
        except GoogleAuthRequiredException:
            return []  # no Google account: there is simply no calendar to search
        except Exception as exc:
            logger.error(f"Calendar client setup failed: {exc}")
            failed["calendar"] = exc
            return []
        try:
            events = await asyncio.to_thread(
                calendar_service.list_events,
                max_results=3,
                days_ahead=7,
            )
            return [{
                "text": f"Calendar: {event.get('title', 'Event')} at {event.get('start', '')}",
                "score": 0.9,
                "source": "calendar",
                "source_confidence": 0.95,
                "relevance": 0.9,
                "date": event.get("start"),
            } for event in events]
        except GoogleAuthRequiredException:
            return []
        except Exception as exc:
            logger.error(f"Calendar search failed: {exc}")
            failed["calendar"] = exc
        return []

    async def _reminder_search() -> list:
        if db_client is None:
            return []
        try:
            from models.ReminderModel import ReminderModel
            reminder_model = await ReminderModel.create_instance(db_client)
            reminders = await reminder_model.get_active_reminders(state.get("project_id", "default"), state.get("user_id", ""), limit=3)
            return [{
                "text": f"Reminder: {reminder.task} on {reminder.date} {reminder.time}",
                "score": 0.85,
                "source": "reminders",
                "source_confidence": 0.9,
                "relevance": 0.85,
                "date": reminder.date,
                "priority": getattr(reminder, "priority", state.get("priority", "medium")),
            } for reminder in reminders]
        except Exception as exc:
            logger.error(f"Reminder search failed: {exc}")
            failed["reminders"] = exc
        return []

    searches = {
        "knowledge_base": _kb_search,
        "tasks": _task_search,
        "calendar": _calendar_search,
        "reminders": _reminder_search,
    }
    sources = choose_sources(state["query"])
    state["metadata"]["sources_searched"] = sources
    results_per_source = await asyncio.gather(*(searches[name]() for name in sources))
    aggregated_results = [item for results in results_per_source for item in results]

    state["emails"] = []
    if failed:
        state["metadata"]["unavailable_sources"] = sorted(failed)
    if not aggregated_results and failed:
        # Nothing found AND something could not be searched: "no information found" would be a lie.
        state["knowledge_results"] = []
        logger.error(f"Retrieval could not search {sorted(failed)}")
        reason = next((e for e in failed.values() if isinstance(e, DomainError)),
                      ExternalServiceError(f"Could not search {sorted(failed)}", service="search"))
        return fail(state, reason)
    if not aggregated_results:
        state["knowledge_results"] = []
        state["result"] = ResponseFormatter.format_no_results()
        state["status"] = "no_results"
        state["metadata"]["source"] = "none"
        logger.info(f"Retrieval searched {sources}: no results")
        return state

    ranked_results = rerank_results(aggregated_results, limit=7)
    state["knowledge_results"] = ranked_results

    # One LLM call: the model says whether the context answers the question and gives the answer.
    answer = None
    if generation_client:
        context_text = "\n\n".join(item.get("text", "") for item in ranked_results)
        try:
            answer = await llm_json(
                generation_client,
                render("agent", "kb_answer", context=context_text, question=state["query"]),
                KBAnswer,
                chat_history=state.get("chat_history", []),
                max_output_tokens=400,
                temperature=0.2,
                json_mode=True,
            )
        except LLMOutputError as exc:
            # Not usable JSON even after a retry: use the text as the answer rather than dropping it.
            if exc.raw.strip():
                answer = KBAnswer(found=True, answer=exc.raw.strip())
        except Exception as exc:
            logger.error(f"LLM answer generation failed: {exc}")

    if answer is None:
        # No LLM answer available: fall back to the best matching passage.
        answer = KBAnswer(found=True, answer=ranked_results[0].get("text", ""))

    if not answer.found or not answer.answer.strip():
        state["result"] = ResponseFormatter.format_no_results()
        state["status"] = "no_results"
        state["metadata"]["source"] = "none"
    else:
        single_source = len({item.get("source") for item in ranked_results}) == 1
        state["result"] = ResponseFormatter.format_retrieval_answer(
            answer.answer.strip(), source="knowledge_base" if single_source and sources == ["knowledge_base"] else "multi_source"
        )
        state["status"] = "success"
        state["metadata"]["source"] = "knowledge_base" if sources == ["knowledge_base"] else "multi_source"
        state["metadata"]["priority"] = state.get("priority", detect_priority(state["query"]))

    logger.info(f"Retrieval searched {sources}: {len(ranked_results)} ranked results, status={state['status']}")
    return state
