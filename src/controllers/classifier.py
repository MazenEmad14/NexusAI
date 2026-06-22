from .BaseController import BaseController
import json
import logging

logger = logging.getLogger('uvicorn.error')

class ClassifierController(BaseController):
    """
    Uses LLM to classify user queries into:
    - retrieval (RAG/Gmail search)
    - action:send_email
    - action:calendar
    - action:reminder
    """

    def __init__(self, generation_client):
        super().__init__()
        self.generation_client = generation_client

    def classify_query(self, query: str, chat_history: list = None) -> dict:
        """
        Classify a user query into request_type and action_type.
        Returns: {"request_type": "retrieval"|"action", "action_type": "send_email"|"calendar"|"reminder"|""}
        """

        system_prompt = """You are a query classifier for an AI secretary system.
Classify the user's query into one of these categories:

1. "retrieval" — The user wants to FIND, SEARCH, or READ specific information.
   * Examples: "What is the latest update about Project X?", "Find me the report about sales"

2. "retrieval:summary" — The user wants a DAILY SUMMARY of their agenda, emails, and tasks.
   * Key words: "summarize my day", "daily summary", "what's on my schedule today", "brief me"
   * Examples: "Summarize my day", "Give me a daily briefing"

3. "action:send_email" — The user wants to SEND, WRITE, COMPOSE, or MODIFY an email.
   * Examples: "Send an email to Ahmed", "Email the team about the meeting", "Change the subject to Urgent"

4. "action:calendar" — The user wants to CREATE, MODIFY, DELAY, or UPDATE a calendar event or meeting.
   * Examples: "Schedule a meeting tomorrow at 5 PM", "Book a call with the team", "Delay it by 2 hours", "Make it at 6 PM instead"

5. "action:reminder" — The user wants to SET or UPDATE a reminder.
   * Examples: "Remind me to follow up with the client", "Change the reminder to tomorrow"

Respond with ONLY a raw, valid JSON object matching this exact schema. DO NOT include any conversational text, explanations, or markdown code blocks:
{
  "request_type": "retrieval",
  "action_type": "summary"
}
"""

        messages = [
            self.generation_client.construct_prompt(
                prompt=system_prompt,
                role=self.generation_client.enums.SYSTEM.value,
            )
        ]

        if chat_history:
            for turn in chat_history[:-1]:
                role = self.generation_client.enums.USER.value if turn["role"] == "user" else self.generation_client.enums.ASSISTANT.value
                messages.append(self.generation_client.construct_prompt(prompt=turn["content"], role=role))

        try:
            response = self.generation_client.generate_text(
                prompt=f"Classify this query: \"{query}\"",
                chat_history=messages,
                max_output_tokens=100,
                temperature=0.0
            )

            if not response:
                logger.error("Empty response from LLM classifier")
                return {"request_type": "retrieval", "action_type": ""}

            response_text = response.lower()
            
            # Robust substring matching to handle conversational LLMs
            if "retrieval:summary" in response_text or '"action_type": "summary"' in response_text or '"action_type":"summary"' in response_text:
                return {"request_type": "retrieval", "action_type": "summary"}
            elif "action:calendar" in response_text:
                return {"request_type": "action", "action_type": "calendar"}
            elif "action:send_email" in response_text:
                return {"request_type": "action", "action_type": "send_email"}
            elif "action:reminder" in response_text:
                return {"request_type": "action", "action_type": "reminder"}
            else:
                return {"request_type": "retrieval", "action_type": ""}

        except Exception as e:
            logger.error(f"Error in query classification: {e}")
            return {"request_type": "retrieval", "action_type": ""}

    def extract_action_details(self, query: str, action_type: str, chat_history: list = None) -> dict:
        """
        Extract structured details from the query based on the action type.
        """

        if action_type == "send_email":
            return self._extract_email_details(query, chat_history)
        elif action_type == "calendar":
            return self._extract_meeting_details(query, chat_history)
        elif action_type == "reminder":
            return self._extract_reminder_details(query, chat_history)
        return {}

    def _extract_email_details(self, query: str, chat_history: list = None) -> dict:
        system_prompt = """Extract email details from the user's request. Resolve any pronouns using the conversation history.
Return ONLY a valid JSON object:
{"recipient": "email or name", "subject": "subject line", "body": "email body content"}
If any field is unclear, use a reasonable default. If only a name is given, use it as recipient."""

        return self._extract_with_llm(query, system_prompt, chat_history)

    def _extract_meeting_details(self, query: str, chat_history: list = None) -> dict:
        import datetime
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        system_prompt = f"""Extract meeting/calendar event details from the user's request. 
Resolve any pronouns or references ("it", "tomorrow") using the conversation history.
If the user asks to modify a previous meeting (e.g., "delay it 2 hours"), calculate the new time based on the history.

Current Date and Time: {now}

Return ONLY a valid JSON object:
{{"title": "meeting title", "date": "YYYY-MM-DD", "time": "HH:MM", "attendees": ["name1", "name2"]}}
If a field is completely unknown or not specified, use null instead of a default value."""

        return self._extract_with_llm(query, system_prompt, chat_history)

    def _extract_reminder_details(self, query: str, chat_history: list = None) -> dict:
        system_prompt = """Extract reminder details from the user's request. Resolve references from the conversation history.
Return ONLY a valid JSON object:
{"task": "what to remember", "date": "YYYY-MM-DD", "notes": "additional notes"}
If date is unclear, leave it as empty string."""

        return self._extract_with_llm(query, system_prompt, chat_history)

    def _extract_with_llm(self, query: str, system_prompt: str, chat_history: list = None) -> dict:
        messages = [
            self.generation_client.construct_prompt(
                prompt=system_prompt,
                role=self.generation_client.enums.SYSTEM.value,
            )
        ]
        
        if chat_history:
            for turn in chat_history[:-1]:
                role = self.generation_client.enums.USER.value if turn["role"] == "user" else self.generation_client.enums.ASSISTANT.value
                messages.append(self.generation_client.construct_prompt(prompt=turn["content"], role=role))

        try:
            response = self.generation_client.generate_text(
                prompt=f"User request: \"{query}\"",
                chat_history=messages,
                max_output_tokens=200,
                temperature=0.0
            )

            if not response:
                return {}

            response_text = response.strip()
            
            # Extract JSON block between the first '{' and the last '}'
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')
            
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx+1]
                return json.loads(json_str)
            else:
                logger.error(f"No JSON object found in response: {response_text}")
                return {}

        except Exception as e:
            logger.error(f"Error extracting action details: {e}")
            return {}
