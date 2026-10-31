"""First steps of every request: record the query, then classify it."""

import logging
from agent.deps import AgentDeps
from agent.errors import fail
from helpers.exceptions import LLMUnavailable
from agent.history import append_chat_turn
from agent.state import AgentState
from controllers.classifier import ClassifierController
from helpers.project_scope import scoped_project_id
from models.db_schemes import Query
from services.retrieval import detect_priority
import time

logger = logging.getLogger('uvicorn.error')

PER_TURN_METADATA = ("error_code", "unavailable_sources", "sources_searched")


async def save_query_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Save the incoming query to MongoDB and fetch available files."""
    db_client = deps.db_client

    # Metadata is carried from turn to turn; these describe one turn only, so a new turn starts clean
    # (otherwise a successful answer would still carry the previous turn's error code).
    for key in PER_TURN_METADATA:
        state["metadata"].pop(key, None)

    if db_client is not None:
        from models.QueryModel import QueryModel
        query_model = await QueryModel.create_instance(db_client=db_client)
        query_record = Query(
            query_text=state["query"],
            user_id=state.get("user_id", ""),
            project_id=state.get("project_id", "default")
        )
        await query_model.create_query(query=query_record)

        # Fetch the user's own uploaded files so data analysis can pick one by name.
        try:
            from models.AssetModel import AssetModel
            from models.ProjectModel import ProjectModel
            project_model = await ProjectModel.create_instance(db_client)
            project = await project_model.get_project_or_create_one(
                scoped_project_id(state.get("user_id", ""), state.get("project_id", "default"))
            )
            asset_model = await AssetModel.create_instance(db_client)
            assets = await asset_model.get_all_project_assets(project.id, "file")
            if assets:
                state["metadata"]["available_files"] = "\n".join(f"{a.asset_name} -> {a.asset_name}" for a in assets)
        except Exception as e:
            logger.error(f"Failed to fetch available files: {e}")

    state["metadata"]["start_time"] = time.time()
    logger.info(f"Query saved: {state['query'][:50]}...")
    
    # Append user message to contextual memory
    append_chat_turn(state, "user", state["query"])
    return state


def detect_request_type_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Classify the query into retrieval or action."""
    generation_client = deps.generation_client

    classifier = ClassifierController(generation_client=generation_client)
    try:
        classification = classifier.classify_query(
            query=state["query"],
            chat_history=state.get("chat_history", [])
        )
    except LLMUnavailable as exc:
        logger.error(f"Cannot classify the request: {exc}")
        state["request_type"] = "error"  # routing then goes straight to the reply
        return fail(state, exc)

    state["request_type"] = classification["request_type"]
    state["action_type"] = classification.get("action_type", "")
    state["priority"] = classification.get("priority", detect_priority(state["query"]))
    state["metadata"]["priority"] = state["priority"]

    logger.info(f"Classified as: {state['request_type']} / {state['action_type']} [{state['priority']}]")
    return state
