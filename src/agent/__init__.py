"""The secretary agent: a LangGraph workflow made of small nodes.

    agent/state.py          the state every node reads and writes
    agent/deps.py           the services the nodes use
    agent/graph.py          wiring: nodes and edges
    agent/routing.py        which node runs next
    agent/nodes/            one module per kind of step
    agent/google_clients.py the caller's own Gmail/Calendar clients
"""
from agent.deps import AgentDeps
from agent.graph import build_agent_graph, create_initial_state
from agent.state import AgentState

__all__ = ["AgentDeps", "AgentState", "build_agent_graph", "create_initial_state"]
