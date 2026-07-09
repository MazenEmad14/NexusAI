import asyncio
import json
import logging
from stores.gmail.GmailService import GmailService
from helpers.event_bus import proactive_messages_queue
from helpers.config import get_settings

logger = logging.getLogger("uvicorn.error")

async def evaluate_inbox_and_notify(app):
    try:
        gmail_service = getattr(app, "gmail_service", None)
        if not gmail_service or not gmail_service.is_authenticated():
            return
            
        emails = await asyncio.to_thread(
            gmail_service.search_emails,
            query="is:unread",
            max_results=5
        )
        
        if not emails:
            return

        app_settings = get_settings()

        try:
            from langchain_groq import ChatGroq
            llm = ChatGroq(
                api_key=app_settings.GROQ_API_KEY,
                model_name=app_settings.GENERATION_MODEL_ID,
                temperature=0.1
            )
        except ImportError:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                api_key=app_settings.GROQ_API_KEY,
                base_url="https://api.groq.com/openai/v1",
                model=app_settings.GENERATION_MODEL_ID,
                temperature=0.1
            )

        emails_text = ""
        for i, email in enumerate(emails):
            sender = email.get('sender') or email.get('from', 'Unknown')
            emails_text += f"Email {i+1}:\nSubject: {email.get('subject', '')}\nSnippet: {email.get('snippet', '')}\nFrom: {sender}\n\n"

        prompt = f"""You are Aria, an executive AI assistant reviewing the user's unread emails on their behalf.

Context:

Task:
Review the unread emails below. Flag them as urgent ONLY if they meet at least one of these criteria:
- Sent directly by the boss/manager listed above.
- Contains a hard deadline within the next 24 hours.
- Announces an urgent meeting change (time/cancellation) for a meeting happening today or tomorrow.

Do NOT flag as urgent: newsletters, FYI-only emails, marketing, routine updates, or anything without a clear time-sensitive action.

Output ONLY a valid JSON object, no markdown, no code fences, no explanation:
{{
  "is_urgent": true or false,
  "urgent_emails": [
    {{"sender": "...", "subject": "...", "reason": "short reason it's urgent"}}
  ],
  "message": "proactive notification for the user, empty string if is_urgent is false"
}}

Rules for 'urgent_emails': empty list [] if is_urgent is false.

Mandatory Rules for 'message' (only written if is_urgent is true):
1. Be human, natural, and warm — no scripted templates or robotic openers.
2. Be concise. If there's one urgent email, mention it directly. If there are multiple, briefly group them (don't read them like a list item by item).
3. NEVER use emojis.
4. Do not start with filler like "I reviewed your emails" — get straight to what matters.
5. State things calmly and directly; no over-apologizing, no excessive reassurance.
6. Write in the same language as the content of the urgent email(s) (Arabic or English) — if mixed, default to Arabic.
7. Suitable for voice/TTS: short sentences, no special characters, no bullet points inside the string.

If there are no unread emails to review, return:
{{"is_urgent": false, "urgent_emails": [], "message": ""}}

Unread Emails:
{emails_text}
"""

        response = await llm.ainvoke(prompt)
        content = response.content.strip()

        import re
        match = re.search(r'(\[.*?\]|\{.*?\})', content, re.DOTALL)
        if match:
            content = match.group(1)

        try:
            result = json.loads(content)
            if isinstance(result, list):
                # If it's a list, find the first urgent one
                for item in result:
                    if item.get("is_urgent") and item.get("message"):
                        await proactive_messages_queue.put(item.get("message"))
                        logger.info(f"Proactive message queued: {item.get('message')}")
                        break
            else:
                if result.get("is_urgent") and result.get("message"):
                    await proactive_messages_queue.put(result.get("message"))
                    logger.info(f"Proactive message queued: {result.get('message')}")
        except json.JSONDecodeError:
            logger.error(f"Failed to parse proactive agent JSON: {content}")

    except Exception as e:
        logger.error(f"Error in evaluate_inbox_and_notify: {e}", exc_info=True)
