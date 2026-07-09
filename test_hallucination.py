import asyncio
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

load_dotenv('src/.env')

async def main():
    llm = ChatOpenAI(temperature=0, model="gpt-4o-mini", api_key=os.getenv("OPENAI_API_KEY"))
    response = await llm.ainvoke([HumanMessage(content="What is the full status of Egypt Versus Argentina match in world cup 2026?")])
    print(response.content)

if __name__ == "__main__":
    asyncio.run(main())
