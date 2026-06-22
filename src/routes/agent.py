from fastapi import APIRouter, status, Request
from fastapi.responses import JSONResponse
import logging

from .schemes.agent import ChatRequest, ChatResponse, ResumeRequest
from controllers.agent_graph import build_agent_graph, create_initial_state
from models.enums.ResponseEnums import ResponseSignal

logger = logging.getLogger('uvicorn.error')

agent_router = APIRouter(
    prefix="/api/v1/agent",
    tags=["api_v1", "agent"]
)


@agent_router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: Request, chat_request: ChatRequest):
    """
    Main endpoint for the Secretary AI Agent.
    Takes a natural language query and orchestrates the full LangGraph workflow.
    """
    try:
        if not hasattr(request.app, "agent_graph"):
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "signal": ResponseSignal.AGENT_RESPONSE_ERROR.value,
                    "error": "Agent graph not initialized"
                }
            )

        agent_graph = request.app.agent_graph

        config = {"configurable": {"thread_id": chat_request.thread_id}}

        # Preserve existing chat history if any
        state_snapshot = agent_graph.get_state(config)
        existing_history = state_snapshot.values.get("chat_history", []) if state_snapshot and state_snapshot.values else []

        initial_state = create_initial_state(
            query=chat_request.query,
            project_id=chat_request.project_id
        )
        initial_state["chat_history"] = existing_history

        logger.info(f"Starting agent workflow for query: {chat_request.query} [Thread: {chat_request.thread_id}]")
        final_state = await agent_graph.ainvoke(initial_state, config=config)

        # Check if the graph is paused for human-in-the-loop approval
        state_snapshot = agent_graph.get_state(config)
        
        if state_snapshot.next:
            action_type = final_state.get("action_type", "")
            action_details = final_state.get("action_details", {})
            return ChatResponse(
                response=f"Pending approval to execute action: {action_type}",
                type=f"action:{action_type}",
                metadata=final_state.get("metadata", {}),
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
            action_details={}
        )

    except Exception as e:
        logger.error(f"Error in agent chat endpoint: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "signal": ResponseSignal.AGENT_RESPONSE_ERROR.value,
                "error": str(e)
            }
        )

@agent_router.post("/resume", response_model=ChatResponse)
async def resume_endpoint(request: Request, resume_request: ResumeRequest):
    """
    Endpoint to resume or cancel an interrupted graph execution (HITL Approval).
    """
    try:
        agent_graph = request.app.agent_graph
        config = {"configurable": {"thread_id": resume_request.thread_id}}

        state_snapshot = agent_graph.get_state(config)
        if not state_snapshot.next:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "signal": ResponseSignal.AGENT_RESPONSE_ERROR.value,
                    "error": "No pending action found for this thread."
                }
            )

        if not resume_request.approve:
            # User rejected the action, update state as the paused node to skip it
            paused_node = state_snapshot.next[0]
            
            # Manually append the rejection to chat history so the LLM knows it was cancelled
            existing_history = state_snapshot.values.get("chat_history", [])
            existing_history.append({"role": "assistant", "content": "Action rejected by user."})

            agent_graph.update_state(
                config, 
                {
                    "status": "rejected", 
                    "result": "Action rejected by user.",
                    "chat_history": existing_history
                }, 
                as_node=paused_node
            )
            
            # We must resume the graph so it can finish (e.g. format_output_node)
            final_state = await agent_graph.ainvoke(None, config=config)
            
            return ChatResponse(
                response="Action was cancelled by the user.",
                type="action",
                status="rejected",
                thread_id=resume_request.thread_id
            )

        # User approved, resume graph execution
        logger.info(f"Resuming graph execution for thread {resume_request.thread_id}")
        final_state = await agent_graph.ainvoke(None, config=config)

        response_type = final_state.get("request_type", "")
        if final_state.get("action_type"):
            response_type = f"action:{final_state['action_type']}"

        return ChatResponse(
            response=final_state.get("result", ""),
            type=response_type,
            metadata=final_state.get("metadata", {}),
            status=final_state.get("status", "success"),
            thread_id=resume_request.thread_id,
            action_details={}
        )

    except Exception as e:
        logger.error(f"Error resuming graph execution: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "signal": ResponseSignal.AGENT_RESPONSE_ERROR.value,
                "error": str(e)
            }
        )
