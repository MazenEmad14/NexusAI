"""Builds the LangGraph workflow of the secretary agent.

Pipeline: save query -> classify -> (conversation | knowledge base | Gmail | web search | daily
summary | data analysis | approved actions) -> format the reply.
"""
import logging
from functools import partial

from langgraph.graph import END, StateGraph

from agent import routing
from agent.deps import AgentDeps
from agent.nodes import actions, conversational, data_analysis, gmail, intake, output, retrieval, summary, web
from agent.state import AgentState

logger = logging.getLogger('uvicorn.error')

# Actions that change something outside the app pause for the user's approval first.
APPROVAL_REQUIRED = ["send_email", "create_calendar_event", "save_reminder"]


def build_agent_graph(deps: AgentDeps = None, checkpointer=None):
    """
    Build and compile the Secretary AI LangGraph workflow.

    Returns a compiled graph that can be invoked with:
        result = await graph.ainvoke(initial_state, config={"configurable": {"thread_id": ...}})
    """
    deps = deps or AgentDeps()

    def node(function):
        """Give a node its services; the graph then calls it with the state only."""
        return partial(function, deps=deps)

    graph = StateGraph(AgentState)

    graph.add_node("save_query", node(intake.save_query_node))
    graph.add_node("detect_request_type", node(intake.detect_request_type_node))
    graph.add_node("conversational_node", node(conversational.conversational_node))
    graph.add_node("search_knowledge_base", node(retrieval.search_knowledge_base_node))
    graph.add_node("search_gmail", node(gmail.search_gmail_node))
    graph.add_node("extract_action_details", node(actions.extract_action_details_node))
    graph.add_node("send_email", node(actions.send_email_node))
    graph.add_node("create_calendar_event", node(actions.create_calendar_event_node))
    graph.add_node("save_reminder", node(actions.save_reminder_node))
    graph.add_node("daily_summary", node(summary.daily_summary_node))
    graph.add_node("data_analysis", node(data_analysis.data_analysis_node))
    graph.add_node("web_search", node(web.web_search_node))
    graph.add_node("format_output", node(output.format_output_node))

    graph.set_entry_point("save_query")
    graph.add_edge("save_query", "detect_request_type")

    # Conditional: retrieval vs action
    graph.add_conditional_edges(
        "detect_request_type",
        routing.route_request_type,
        {
            "conversational_node": "conversational_node",
            "search_knowledge_base": "search_knowledge_base",
            "daily_summary": "daily_summary",
            "extract_action_details": "extract_action_details",
            "data_analysis": "data_analysis",
            "web_search": "web_search",
            "search_gmail": "search_gmail",
        }
    )

    # Each answer comes from one source; there is no cascade from one source to the next.
    graph.add_edge("conversational_node", "format_output")
    graph.add_edge("search_knowledge_base", "format_output")
    graph.add_edge("search_gmail", "format_output")
    graph.add_edge("web_search", "format_output")
    graph.add_edge("daily_summary", "format_output")

    # Conditional: action type routing
    graph.add_conditional_edges(
        "extract_action_details",
        routing.route_action_type,
        {
            "send_email": "send_email",
            "create_calendar_event": "create_calendar_event",
            "save_reminder": "save_reminder",
            "data_analysis": "data_analysis",
            "search_gmail": "search_gmail",
            "search_knowledge_base": "search_knowledge_base",
            "format_output": "format_output",
        }
    )

    # Action results -> format output
    graph.add_edge("send_email", "format_output")
    graph.add_edge("create_calendar_event", "format_output")
    graph.add_edge("save_reminder", "format_output")
    graph.add_edge("data_analysis", "format_output")

    graph.add_edge("format_output", END)

    compiled_graph = graph.compile(checkpointer=checkpointer, interrupt_before=APPROVAL_REQUIRED)
    logger.info("Aria Agent graph compiled successfully with persistent checkpointer and HITL")
    return compiled_graph


def create_initial_state(query: str, project_id: str = "default", file_path: str = None, user_id: str = "",
                         timezone_name: str = "") -> AgentState:
    """Create the initial state for a new agent invocation."""
    return AgentState(
        query=query,
        user_id=user_id,
        user_email="",
        user_timezone=timezone_name or "",
        request_type="",
        action_type="",
        priority="medium",
        knowledge_results=[],
        emails=[],
        action_details={},
        result="",
        status="pending",
        metadata={},
        project_id=project_id,
        chat_history=[],
        file_path=file_path,
        chart_data=None,
    )
