import asyncio
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
import os
from dotenv import load_dotenv

load_dotenv('src/.env')

async def main():
    llm = ChatOpenAI(temperature=0, model="gpt-4o-mini", api_key=os.getenv("OPENAI_API_KEY"))
    
    context_text = "Web Result (https://aljazeera.com/match): Argentina vs Egypt 3-2: FIFA World Cup 2026 last 16 - as it happened - 14 hours ago ... Argentina's forward #10 Lionel Messi is lifted by teammates as they celebrate after winning the 2026 World Cup Round of 16 football match ..."
    
    enhanced_prompt =f"""You are a Secretary AI. The context below contains retrieved information from the user's account (Emails, Calendar, Tasks) AND/OR Web Search results. Answer using ONLY what's in the context.

Mandatory Response Rules:
1. Be human, warm, and natural — talk like a sharp assistant who knows the person.
2. Be concise, matching the answer's length to the question's complexity. Optimized for voice.
3. NEVER use emojis.
4. Don't repeat or restate the user's question before answering.
5. If the context has a clear answer, give it directly and confidently — don't hedge with "based on the retrieved data" or similar meta-commentary.
6. If an action was taken, confirm it in one plain sentence.
7. If the context is empty or has nothing relevant to the query, say so plainly (e.g. "I couldn't find any information about that") — don't guess or fabricate an answer.
8. If the information comes from a Web Result, just state the facts naturally without emphasizing that you searched the web.
9. Always respond in the exact language the user used (Arabic or English).

Context:
{context_text}"""

    messages = [
        SystemMessage(content=enhanced_prompt),
        HumanMessage(content="search the web for Egypt and Argentina match in world cup 2026 and give me the full status")
    ]
    
    response = await llm.ainvoke(messages)
    print("LLM Response:", response.content)

if __name__ == "__main__":
    asyncio.run(main())
