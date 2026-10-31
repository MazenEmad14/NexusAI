"""Chat history kept in the agent state."""

from agent.state import AgentState


def ensure_chat_history(state: AgentState) -> list:
    history = state.get("chat_history") or []
    if not isinstance(history, list):
        history = []
    state["chat_history"] = history
    return history


def append_chat_turn(state: AgentState, role: str, content: str) -> None:
    history = ensure_chat_history(state)
    if history and history[-1].get("role") == role and history[-1].get("content") == content:
        return
    # Create a NEW list to ensure LangGraph checkpointer detects the state change
    new_history = list(history)
    new_history.append({"role": role, "content": content})
    if len(new_history) > 10:
        new_history = new_history[-10:]
    state["chat_history"] = new_history
