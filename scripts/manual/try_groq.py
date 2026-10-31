import os, json, re, requests
from dotenv import load_dotenv

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

def generate_text(prompt):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.1-8b-instant", # or whatever the default is, doesn't matter much
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 150
    }
    resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
    if resp.status_code == 200:
        return resp.json()["choices"][0]["message"]["content"]
    else:
        print("Error:", resp.text)
        return ""

prompt_template = """Convert the user's natural language query into a Gmail search operator string and extract the desired result limit.
Return ONLY a valid JSON object with EXACTLY two keys: "query" (string) and "max_results" (integer). Do not include markdown formatting or explanations.

Rules for "query":
- If the user asks for the latest emails with no specific filter (e.g. "last 5 mails", "اخر 3 رسايل"), leave "query" as an empty string "".
- Do NOT use newer_than / older_than to fake a result count limit. Use them ONLY if the user specifies a real time range (e.g., "yesterday" -> newer_than:2d older_than:1d).
- If unsure, return "" for the query.

Rules for "max_results":
- Extract the exact number of emails requested (e.g. "last 3 mails" -> 3).
- If no number is requested, default to 5.

Examples:
- "emails from ahmed" -> {{"query": "from:ahmed", "max_results": 5}}
- "what is my last 3 mails" -> {{"query": "", "max_results": 3}}
- "اخر 10 ايميلات امبارح" -> {{"query": "newer_than:2d older_than:1d", "max_results": 10}}
- "رسايل فيها مرفقات من جوجل" -> {{"query": "has:attachment from:google", "max_results": 5}}

User Query: "{query}"
"""

queries = ["last 3 mails", "only last mail", "last 5 mails from ahmed"]
for q in queries:
    print(f"\\n--- Query: {q} ---")
    r = generate_text(prompt_template.format(query=q))
    print("RAW LLM:", r)
    match = re.search(r'\{.*\}', r, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            print('JSON PARSED:', parsed)
            print('max_results:', int(parsed.get('max_results', 5)))
        except Exception as e:
            print('JSON Decode Error:', e)
    else:
        print('NO JSON FOUND')
