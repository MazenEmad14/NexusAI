"""Last step: add timing and record the reply in the chat history."""

import logging
from agent.deps import AgentDeps
from agent.history import append_chat_turn
from agent.state import AgentState
from controllers.formatter import ResponseFormatter
import time

logger = logging.getLogger('uvicorn.error')


def format_output_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Final formatting — add processing time."""
    start_time = state.get("metadata", {}).get("start_time", 0)
    if start_time:
        processing_time = round(time.time() - start_time, 2)
        state["metadata"]["processing_time"] = f"{processing_time}s"

    if not state.get("result"):
        state["result"] = ResponseFormatter.format_no_results()
        state["status"] = "no_results"

    content_for_history = state["result"]
    chart_data = state.get("chart_data")
    if chart_data:
        import json
        content_for_history += f"\n[Chart Data]: {json.dumps(chart_data)}"

    append_chat_turn(state, "assistant", content_for_history)

    return state
