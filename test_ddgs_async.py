import asyncio
from ddgs import DDGS
import time

async def main():
    def _do_ddg_search():
        with DDGS() as ddgs:
            return [r for r in ddgs.text("search the web for Egypt and Argentina match in world cup 2026 and give me the full status", max_results=3)]

    print("Starting asyncio thread...")
    results = await asyncio.to_thread(_do_ddg_search)
    print(f"Results length: {len(results)}")
    for r in results:
        print(r['title'])

if __name__ == "__main__":
    asyncio.run(main())
