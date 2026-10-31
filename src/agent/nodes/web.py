import logging
import asyncio
from agent.deps import AgentDeps
from helpers.config import get_settings
from agent.errors import fail
from helpers.exceptions import ExternalServiceError
from agent.state import AgentState
from helpers.prompts import render

logger = logging.getLogger('uvicorn.error')

async def web_search_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Execute a web search using Google Custom Search API or fallback to DDGS."""
    generation_client = deps.generation_client
    query = state["query"]
    
    settings = get_settings()
    api_key = settings.GOOGLE_SEARCH_API_KEY
    cx = settings.GOOGLE_SEARCH_CX
    
    results = []
    search_error = None

    if api_key and cx:
        logger.info("Web search through Google Custom Search")
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params={"key": api_key, "cx": cx, "q": query, "num": 3}
                )
                response.raise_for_status()
                data = response.json()
                items = data.get("items", [])
                for item in items:
                    results.append({
                        "title": item.get("title", ""),
                        "href": item.get("link", ""),
                        "body": item.get("snippet", "")
                    })
        except Exception as e:
            logger.error(f"Google Custom Search API failed: {e}")
            search_error = e
    else:
        logger.info("Web search through DuckDuckGo")
        try:
            def _do_ddg_search():
                from ddgs import DDGS
                with DDGS() as ddgs:
                    return [r for r in ddgs.text(query, max_results=3)]
                    
            ddgs_results = await asyncio.to_thread(_do_ddg_search)
            if ddgs_results:
                results = ddgs_results
        except ImportError as e:
            logger.warning(f"DDGS import failed: {e}")
        except Exception as exc:
            logger.error(f"DDGS web search failed: {exc}")
            search_error = exc
            
    if not results and search_error is not None:
        # The search itself failed (not "no hits"): say so instead of letting the model claim nothing exists.
        return fail(state, ExternalServiceError(f"Web search failed: {search_error}", service="Web search"))

    if not results:
        logger.warning("No web search results found. Proceeding with empty context for conversational fallback.")
        snippets_text = render("agent", "web_no_results")
    else:
        # Format the snippets for the LLM
        snippets_text = "\n\n".join([f"Source: {r.get('href', 'Unknown URL')}\nTitle: {r.get('title', '')}\nSnippet: {r.get('body', '')}" for r in results])
    
    answer = ""
    if generation_client:
        prompt = render("agent", "web_answer", snippets=snippets_text, query=query)
        
        try:
            answer = await generation_client.agenerate_text(prompt=prompt,
                chat_history=state.get("chat_history", []),
                max_output_tokens=300,
                temperature=0.1
            )
        except Exception as e:
            logger.error(f"LLM web search generation failed: {e}")
            
    if not answer:
        answer = snippets_text

    state["result"] = answer
    state["status"] = "success"
    state["metadata"]["source"] = "web_search"
    
    return state
