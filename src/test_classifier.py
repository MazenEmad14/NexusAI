import sys
import asyncio
from pathlib import Path

# Add src to sys.path
src_path = str(Path(__file__).resolve().parent)
sys.path.insert(0, src_path)

from helpers.config import get_settings
from stores.llm.LLMProviderFactory import LLMProviderFactory
from controllers.classifier import ClassifierController

settings = get_settings()
llm_provider_factory = LLMProviderFactory(settings)
generation_client = llm_provider_factory.create(provider=settings.GENERATION_BACKEND)
generation_client.set_generation_model(model_id=settings.GENERATION_MODEL_ID)

classifier = ClassifierController(generation_client)

query = "Schedule a meeting with the development team tomorrow at 4 PM to review the AI Agent logic."
print("Testing classifier on query:", query)

# Directly print what the LLM returns
chat_history = [
    generation_client.construct_prompt(
        prompt="""You are a query classifier for an AI secretary system.
Classify the user's query into one of these categories:

1. "retrieval" — The user wants to FIND, SEARCH, or READ information (from documents, emails, or the knowledge base).
   * Key words: "find", "search", "is there", "what is", "did anyone send"
   * Examples: "What is the latest update about Project X?", "Is there any email from Ahmed?", "Find me the report about sales"
   * STRICT RULE: If the user asks if an email exists, or wants to read an email, it is always "retrieval".

2. "action:send_email" — The user wants to SEND, WRITE, or COMPOSE a new email to someone.
   * Key words: "send", "write", "email [person]"
   * Examples: "Send an email to Ahmed", "Email the team about the meeting"
   * STRICT RULE: Do NOT classify searches as sending.

3. "action:calendar" — The user wants to CREATE a calendar event or meeting.
   * Examples: "Schedule a meeting tomorrow at 5 PM", "Book a call with the team"

4. "action:reminder" — The user wants to SET a reminder.
   * Examples: "Remind me to follow up with the client"

Respond with ONLY a valid JSON object, no extra text:
{"request_type": "retrieval" or "action", "action_type": "" or "send_email" or "calendar" or "reminder"}
""",
        role=generation_client.enums.SYSTEM.value,
    )
]

try:
    response = generation_client.generate_text(
        prompt=f"Classify this query: \"{query}\"",
        chat_history=chat_history,
        max_output_tokens=100,
        temperature=0.0
    )
    print("LLM RAW RESPONSE:")
    print(repr(response))
except Exception as e:
    print("ERROR:", e)
