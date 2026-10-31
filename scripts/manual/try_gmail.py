import asyncio, json, re
from controllers.generation import GenerationClient
from helpers.config import get_settings

async def main():
    s = get_settings()
    c = GenerationClient(s.GROQ_API_KEY, s.GENERATION_MODEL_ID)
    prompt = """Convert the user's natural language query into a Gmail search operator string and extract the desired result limit.
Return ONLY a valid JSON object with EXACTLY two keys: "query" (string) and "max_results" (integer). Do not include markdown formatting or explanations.

Rules for "query":
- If the user asks for the latest emails with no specific filter (e.g. "last 5 mails", "اخر 3 رسايل"), leave "query" as an empty string "".
- Do NOT use newer_than / older_than to fake a result count limit. Use them ONLY if the user specifies a real time range (e.g., "yesterday" -> newer_than:2d older_than:1d).
- If unsure, return "" for the query.

Rules for "max_results":
- Extract the exact number of emails requested (e.g. "last 3 mails" -> 3).
- If no number is requested, default to 5.

Examples:
- "emails from ahmed" -> {"query": "from:ahmed", "max_results": 5}
- "what is my last 3 mails" -> {"query": "", "max_results": 3}
- "اخر 10 ايميلات امبارح" -> {"query": "newer_than:2d older_than:1d", "max_results": 10}
- "رسايل فيها مرفقات من جوجل" -> {"query": "has:attachment from:google", "max_results": 5}

User Query: "{query}"
"""
    queries = ["last 3 mails", "only last mail", "last 5 mails from ahmed"]
    for q in queries:
        r = await asyncio.to_thread(c.generate_text, prompt=prompt.format(query=q), chat_history=[], max_output_tokens=150, temperature=0.0)
        print(f'\\nQuery: {q}')
        print(f'RAW LLM: {r}')
        match = re.search(r'\{.*\}', r, re.DOTALL)
        if match:
            print('JSON PARSED:', json.loads(match.group(0)))
        else:
            print('NO JSON FOUND')

if __name__ == "__main__":
    asyncio.run(main())
