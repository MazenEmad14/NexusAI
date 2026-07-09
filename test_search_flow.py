import asyncio
from ddgs import DDGS
from src.services.retrieval import detect_priority, rerank_results

print("--- Testing Web Search (DuckDuckGo) ---")
try:
    with DDGS() as ddgs:
        results = [r for r in ddgs.text("Egypt and Argentina match in world cup 2026", max_results=3)]
        print(f"Found {len(results)} results")
        for r in results:
            print(f"- {r.get('title')}: {r.get('body')[:100]}...")
except Exception as e:
    print(f"Web Search Failed: {e}")

print("\n--- Testing Reranker ---")
try:
    fake_results = [
        {"text": "Web result about Argentina", "source": "web", "relevance": 0.5},
        {"text": "Email about meeting", "source": "gmail", "relevance": 0.9},
        {"text": "Random vector result", "source": "knowledge_base", "relevance": 0.1}
    ]
    ranked = rerank_results(fake_results, limit=5)
    print("Reranked order:")
    for r in ranked:
        print(f"- {r['source']} (Relevance: {r['relevance']})")
except Exception as e:
    print(f"Reranker Failed: {e}")
