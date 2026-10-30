import os
import logging
import asyncio
from models.agent_state import AgentState
from controllers.formatter import ResponseFormatter

logger = logging.getLogger('uvicorn.error')

async def web_search_node(state: AgentState, **kwargs) -> AgentState:
    """Execute a web search using Google Custom Search API or fallback to DDGS."""
    generation_client = kwargs.get("generation_client")
    query = state["query"]
    
    api_key = os.getenv("GOOGLE_SEARCH_API_KEY")
    cx = os.getenv("GOOGLE_SEARCH_CX")
    
    results = []
    
    if api_key and cx:
        logger.info(f"Using Google Custom Search API for query: {query}")
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
    else:
        logger.info(f"Using DDGS fallback for query: {query}")
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
            
    if not results:
        logger.warning("No web search results found. Proceeding with empty context for conversational fallback.")
        snippets_text = "No web search results found. If the user is just saying hello or asking a general conversational question, respond naturally. Otherwise, say you couldn't find the information."
    else:
        # Format the snippets for the LLM
        snippets_text = "\n\n".join([f"Source: {r.get('href', 'Unknown URL')}\nTitle: {r.get('title', '')}\nSnippet: {r.get('body', '')}" for r in results])
    
    answer = ""
    if generation_client:
        prompt = f"""You are a live web search agent. Use the provided search snippets to answer the user's query. Provide a concise, factual answer. Always cite your sources using the links provided in the snippets.

Search Snippets:
{snippets_text}

User Query: {query}"""
        
        try:
            answer = await asyncio.to_thread(
                generation_client.generate_text,
                prompt=prompt,
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
