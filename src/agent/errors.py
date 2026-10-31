"""How a node reports a failure it can explain: the reply says what went wrong and the run ends there."""
from agent.state import AgentState
from controllers.formatter import ResponseFormatter
from helpers.exceptions import DomainError


def fail(state: AgentState, error: DomainError) -> AgentState:
    state["result"] = ResponseFormatter.format_error(error.public_message)
    state["status"] = "error"
    state["metadata"]["error_code"] = error.code
    return state
