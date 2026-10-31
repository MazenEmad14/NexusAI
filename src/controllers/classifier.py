from .BaseController import BaseController
from .llm_schemas import Classification, EmailDetails, FileDetails, MeetingDetails, ReminderDetails
from helpers.exceptions import LLMOutputError, LLMUnavailable
from helpers.llm_json import llm_json_sync
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

        system_prompt = """You are a strict query classifier for an AI secretary.
You MUST respond with ONLY a raw, valid JSON object. No text before or after. No markdown. No code fences.

=== CLASSIFICATION RULES (read ALL before deciding) ===

RULE 0 — CONVERSATIONAL:
  If the user is greeting, saying goodbye, thanking, making casual small talk, or asking about your identity (e.g., "who are you?", "what is your name?") with NO request for information or action:
  -> {"request_type": "conversational", "action_type": ""}
  Examples: "hi", "how are you", "thanks", "bye", arabic greetings, "what's up", "who are you", "what is your name"

RULE 1 — SEND EMAIL (action_type: "send_email"):
  If the user wants to SEND, WRITE, COMPOSE, or DRAFT an email TO someone:
  -> {"request_type": "action", "action_type": "send_email"}
  Keywords: send, write, compose, draft, tell him/her via email, email to [name]
  DO NOT confuse with reading/checking emails (that is "gmail").

RULE 2 — READ/SEARCH EMAILS (action_type: "gmail"):
  If the user wants to READ, CHECK, SEARCH, or SUMMARIZE their INBOX or received emails:
  -> {"request_type": "action", "action_type": "gmail"}
  Keywords: my emails, my inbox, emails from X, what emails did I receive, latest email, unread emails
  DO NOT use "gmail" for sending. Use ONLY for reading/searching.

RULE 3 — CALENDAR (action_type: "calendar"):
  If the user wants to SCHEDULE, BOOK, CREATE, MODIFY, or CANCEL a meeting, appointment, or event:
  -> {"request_type": "action", "action_type": "calendar"}
  Keywords: book, schedule, meeting, appointment, event, reserve, cancel meeting, reschedule

RULE 4 — DATA ANALYSIS (action_type: "data_analysis"):
  If the user wants to ANALYZE, PLOT, CHART, or PROCESS uploaded data (CSV, Excel, file):
  -> {"request_type": "action", "action_type": "data_analysis"}
  Keywords: analyze, plot, chart, the file I uploaded, CSV, Excel, data analysis

RULE 5 — REMINDER (action_type: "reminder"):
  If the user wants to SET or UPDATE a reminder for themselves:
  -> {"request_type": "action", "action_type": "reminder"}

RULE 6 — DAILY SUMMARY (action_type: "summary"):
  If the user asks for a summary of their day, agenda, or tasks for today:
  -> {"request_type": "action", "action_type": "summary"}

RULE 7 — WEB SEARCH (request_type: "web_search"):
  For general knowledge, news, live prices, public facts, sports results, or internet searches:
  -> {"request_type": "web_search", "action_type": ""}
  CRITICAL: If the user explicitly says "search for [public topic]", "who is...", or "what is the result of...", you MUST use "web_search". Do NOT use "action" unless it's a personal email/calendar/reminder action.

RULE 8 — RETRIEVAL (request_type: "retrieval"):
  ONLY for searching personal uploaded documents, PDFs, or internal project knowledge base.
  -> {"request_type": "retrieval", "action_type": ""}

=== OUTPUT SCHEMA ===
{"request_type": "conversational"|"retrieval"|"action"|"web_search", "action_type": ""|"summary"|"send_email"|"gmail"|"calendar"|"reminder"|"data_analysis", "priority": "low"|"medium"|"high"|"critical"}

=== FEW-SHOT EXAMPLES ===

User: hi
{"request_type": "conversational", "action_type": "", "priority": "low"}

User: how are you doing today?
{"request_type": "conversational", "action_type": "", "priority": "low"}

User: ازيك عامل ايه
{"request_type": "conversational", "action_type": "", "priority": "low"}

User: send an email to Ahmed telling him I will be late
{"request_type": "action", "action_type": "send_email", "priority": "medium"}

User: write an email to the client about the proposal
{"request_type": "action", "action_type": "send_email", "priority": "medium"}

User: what emails did I receive today?
{"request_type": "action", "action_type": "gmail", "priority": "medium"}

User: search for emails from Mohamed
{"request_type": "action", "action_type": "gmail", "priority": "medium"}

User: search for Egypt vs Argentina match in world cup 2026 status
{"request_type": "web_search", "action_type": "", "priority": "medium"}

User: show me my inbox
{"request_type": "action", "action_type": "gmail", "priority": "medium"}

User: book a meeting for me tomorrow at 5pm
{"request_type": "action", "action_type": "calendar", "priority": "medium"}

User: schedule a call with the team next Thursday
{"request_type": "action", "action_type": "calendar", "priority": "medium"}

User: analyze the file I uploaded
{"request_type": "action", "action_type": "data_analysis", "priority": "medium"}

User: plot a chart from my CSV data
{"request_type": "action", "action_type": "data_analysis", "priority": "medium"}

User: what is the capital of France?
{"request_type": "web_search", "action_type": "", "priority": "low"}

User: remind me to call the dentist tomorrow at 9am
{"request_type": "action", "action_type": "reminder", "priority": "medium"}

RESPOND WITH JSON ONLY! NO TEXT!"""

        # Build structured message list: system prompt first, then optional prior turns
        # NOTE: We do NOT pass the current user query inside chat_history; it is sent as
        # the `prompt` argument so the LLM sees it as the latest user turn.
        prior_history_messages = []
        if chat_history:
            for turn in chat_history[:-1]:  # exclude last turn (current query already in `query`)
                role = self.generation_client.enums.USER.value if turn["role"] == "user" else self.generation_client.enums.ASSISTANT.value
                prior_history_messages.append(
                    self.generation_client.construct_prompt(prompt=turn["content"], role=role)
                )

        try:
            # ── Fast-path: conversational greetings (no LLM call needed) ────────
            _greeting_tokens = {
                "hi", "hello", "hey", "greetings", "howdy",
                "good morning", "good evening", "good afternoon", "good night",
                "thanks", "thank you", "thx", "ty",
                "bye", "goodbye", "see you", "later",
                "how are you", "how are u", "whats up", "what's up", "sup",
                # Arabic single-word greetings
                "مرحبا", "أهلا", "اهلا", "السلام عليكم", "هاي", "هلا",
                "صباح الخير", "مساء الخير", "شكرا", "شكراً", "مع السلامة", "باي",
                "كيفك", "ازيك", "إزيك", "كيف حالك", "كيف الحال",
                # Identity questions
                "who are you", "what is your name", "whats your name", "what's your name",
                "who am i talking to", "مين انت", "انت مين", "اسمك ايه", "انت اسمك ايه", "ما هو اسمك"
            }
            # Arabic multi-word casual phrases (exact match after normalisation)
            _arabic_chit_chat = {
                "ازيك عامل ايه", "إزيك عامل إيه", "ايه الأخبار", "كيف حالك انتا",
                "عامل إيه", "عامل ايه", "ايه اخبارك",
            }
            query_stripped = query.strip().lower().rstrip("?!.،, ")
            if (query_stripped in _greeting_tokens
                    or query_stripped in {f"{g}!" for g in _greeting_tokens}
                    or query_stripped in _arabic_chit_chat):
                return {"request_type": "conversational", "action_type": "", "priority": "low"}

            # ── Hardcoded bypass: Arabic + English data analysis keywords ───────
            _data_kw_ar = ["حلل", "ارسم", "مخطط", "بيانات", "رسم"]
            _data_kw_en = ["csv", "excel", "analyze the file", "analyse the file",
                           "plot a chart", "data analysis", "plot chart", "upload"]
            q_lower = query.lower()
            if any(w in q_lower for w in _data_kw_ar + _data_kw_en):
                return {"request_type": "action", "action_type": "data_analysis", "priority": "medium"}

            # ── Hardcoded bypass: Calendar keywords ────────────────────────────
            _cal_kw_ar = ["احجز", "حجز", "ميعاد", "موعد", "اجتماع"]
            _cal_kw_en = ["book a meeting", "book meeting", "schedule a meeting",
                          "schedule meeting", "create event", "create a meeting",
                          "set up a meeting", "book an appointment", "book a call",
                          "schedule a call"]
            if any(w in q_lower for w in _cal_kw_ar + _cal_kw_en):
                return {"request_type": "action", "action_type": "calendar", "priority": "medium"}

            # ── Hardcoded bypass: Send email keywords ──────────────────────────
            _email_send_kw_ar = ["ابعت ايميل", "ارسل ايميل", "ابعث ايميل"]
            _email_send_kw_en = ["send an email", "send email", "write an email",
                                 "write email", "compose an email", "compose email",
                                 "draft an email", "draft email",
                                 "email to ", "send to "]
            if any(w in q_lower for w in _email_send_kw_ar + _email_send_kw_en):
                return {"request_type": "action", "action_type": "send_email", "priority": "medium"}

            # Build the full message list: [system_prompt, ...prior_history...]
            # The current query is passed as `prompt` (latest user turn), NOT inside chat_history.
            messages = [
                self.generation_client.construct_prompt(
                    prompt=system_prompt,
                    role=self.generation_client.enums.SYSTEM.value,
                )
            ] + prior_history_messages

            try:
                result = llm_json_sync(
                    self.generation_client,
                    f"Classify this query: \"{query}\"",
                    Classification,
                    chat_history=messages,
                    max_output_tokens=100,
                    temperature=0.0,
                    json_mode=True,
                )
            except LLMOutputError as exc:
                # The model answered, but never with usable JSON: look for the keywords in what it said.
                logger.warning(f"Classifier reply unusable ({exc}); matching keywords in it")
                return self._classify_from_text(exc.raw, query)

            logger.info(f"Classified: {result.request_type} / {result.action_type}")
            return {
                "request_type": result.request_type,
                "action_type": result.action_type,
                "priority": result.priority or self._detect_priority(query),
            }

        except LLMUnavailable:
            raise  # the caller reports it; answering "retrieval" would show a misleading "nothing found"
        except Exception as e:
            logger.error(f"Error in query classification: {e}")
            return {"request_type": "retrieval", "action_type": "", "priority": self._detect_priority(query)}

    def _classify_from_text(self, raw: str, query: str) -> dict:
        """Last resort when the model's reply held no usable JSON: look for the category names in it."""
        text = (raw or "").lower()
        priority = self._detect_priority(query)
        for action_type in ("send_email", "calendar", "gmail", "data_analysis", "reminder"):
            if action_type in text:
                return {"request_type": "action", "action_type": action_type, "priority": priority}
        if "action + summary" in text or '"action_type": "summary"' in text:
            return {"request_type": "action", "action_type": "summary", "priority": priority}
        if "web_search" in text:
            return {"request_type": "web_search", "action_type": "", "priority": priority}
        if "conversational" in text:
            return {"request_type": "conversational", "action_type": "", "priority": priority}
        return {"request_type": "retrieval", "action_type": "", "priority": priority}

    def _detect_priority(self, text: str) -> str:
        from services.retrieval import detect_priority
        return detect_priority(text)

    def extract_action_details(self, query: str, action_type: str, chat_history: list = None, available_files: str = "",
                               timezone_name: str = None) -> dict:
        """
        Extract structured details from the query based on the action type.
        """

        if action_type == "send_email":
            return self._extract_email_details(query, chat_history)
        elif action_type == "calendar":
            return self._extract_meeting_details(query, chat_history, timezone_name)
        elif action_type == "reminder":
            return self._extract_reminder_details(query, chat_history, timezone_name)
        elif action_type == "data_analysis":
            return self._extract_data_analysis_details(query, chat_history, available_files)
        return {}

    def _extract_email_details(self, query: str, chat_history: list = None) -> dict:
        system_prompt = """Extract email details from the user's request. Resolve any pronouns using the conversation history.
Return ONLY a valid JSON object:
{"recipient": "email or name", "subject": "subject line", "body": "email body content"}

RULES:
1. MATCH TONE: Match the tone and length to what the user actually asked for.
2. EXPAND BODY: When the user gives brief points, expand them into a complete message.
3. COVER LETTER MODE: Write a professional letter if requested.
4. CRITICAL RECIPIENT RULE: You MUST search the user's request for an email address (e.g., name@example.com). If you see an email address anywhere near the recipient's name, you MUST put the FULL EMAIL ADDRESS in the "recipient" field. Never just put the name if the email address is provided! If no email is provided, you may use the name.
5. LANGUAGE: Write the email in the same language the user used to request it.

If any field is unclear, use a reasonable default."""

        return self._extract_with_llm(query, system_prompt, chat_history, EmailDetails)

    def _extract_meeting_details(self, query: str, chat_history: list = None, timezone_name: str = None) -> dict:
        from helpers.timezones import local_now, resolve_timezone
        # The current time in the user's zone, so "tomorrow" is the user's tomorrow, not the server's.
        now = f"{local_now(timezone_name).strftime('%Y-%m-%d %H:%M')} ({resolve_timezone(timezone_name).key})"
        system_prompt = f"""Extract meeting/calendar event details from the user's request.
Resolve any pronouns or references ("it", "tomorrow", "the meeting") using the conversation history.

Current Date and Time: {now}

Date/time resolution rules:
- Compute relative dates precisely against the Current Date and Time above (e.g. "next Tuesday", "in two weeks", "end of month").
- If the user is modifying a previously mentioned meeting (e.g. "delay it 2 hours", "move it to 6 instead"), find that meeting's original date/time in the conversation history and calculate the new value from it — don't recompute from "now".
- If no duration is mentioned, default to 60 minutes.

Return ONLY a valid JSON object:
{{
  "action": "create" | "modify" | "cancel",
  "title": "meeting title",
  "date": "YYYY-MM-DD",
  "time": "HH:MM",
  "duration_minutes": 60,
  "attendees": ["email or name", "email or name"],
  "is_modification": true or false,
  "reference_hint": "short phrase identifying which existing meeting this modifies, or null"
}}

Rules:
- attendees: if a full email address is given, keep it as-is (don't reduce "ahmed@company.com" to "ahmed"). If only a name is given, use the name.
- If a field is completely unknown or not specified, use null instead of guessing a default (except duration_minutes, which defaults to 60).
"""

        return self._extract_with_llm(query, system_prompt, chat_history, MeetingDetails)

    def _extract_reminder_details(self, query: str, chat_history: list = None, timezone_name: str = None) -> dict:
        from helpers.timezones import local_now, resolve_timezone
        today = local_now(timezone_name)
        system_prompt = """You are a reminder-extraction assistant. Extract reminder details from the user's latest message, resolving any references (e.g. "that meeting", "same time as before") using the conversation history provided.

Today's date is {current_date} ({current_weekday}). Use this to resolve relative dates like "tomorrow", "next Friday", or "in 3 days".

Return ONLY a valid JSON object with this exact schema, and nothing else (no markdown, no code fences, no explanation):
{{
  "task": "string - what to remember, concise",
  "date": "YYYY-MM-DD or empty string if unclear",
  "time": "HH:MM in 24h format, or empty string if unclear",
  "priority": "high | medium | low",
  "notes": "string - additional context, or empty string"
}}

Rules:
- If no explicit priority is mentioned, infer it from urgency words (e.g. "urgent", "important", "asap" → high). Default to "medium" if no signal exists.
- If the date is relative (e.g. "tomorrow"), calculate the actual date using today's date above.
- If no year is mentioned and the date has already passed this year, assume next year.
- Never invent a date or time that wasn't stated or implied.
- Keep "task" short and action-oriented (e.g. "Call the dentist", not the full sentence).

Example:
User: "Remind me to submit the report by tomorrow morning, it's pretty important"
Today: 2026-07-08
Output: {{"task": "Submit the report", "date": "2026-07-09", "time": "09:00", "priority": "high", "notes": ""}}
""".format(
            current_date=f"{today.strftime('%Y-%m-%d')}, time zone {resolve_timezone(timezone_name).key}",
            current_weekday=today.strftime("%A"),
        )

        return self._extract_with_llm(query, system_prompt, chat_history, ReminderDetails)

    def _extract_data_analysis_details(self, query: str, chat_history: list = None, available_files: str = "") -> dict:
        system_prompt = f"""You are a data analyst assistant. The user wants to analyze a file.
Here are the files available in the user's project:
{available_files if available_files else "No files available. The user must upload a file first."}

Identify which file the user is referring to from the list above. If the user refers to "sales data", find the file name that matches "sales" and return its exact file path.
Return ONLY a valid JSON object with this exact schema:
{{
  "file_path": "the absolute file path from the list above, or empty string if no matching file exists"
}}
"""
        return self._extract_with_llm(query, system_prompt, chat_history, FileDetails)

    def _extract_with_llm(self, query: str, system_prompt: str, chat_history: list = None, schema=None) -> dict:
        """Ask for the details as JSON and return them as a dict ({} if the model never gave usable JSON)."""
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
            details = llm_json_sync(
                self.generation_client,
                f"User request: \"{query}\"",
                schema or dict,
                chat_history=messages,
                max_output_tokens=400,
                temperature=0.0,
                json_mode=True,
            )
        except LLMOutputError as exc:
            logger.error(f"[extract_with_llm] No usable JSON in the model's reply: {exc}")
            return {}
        except LLMUnavailable:
            raise
        except Exception as exc:
            logger.error(f"Error extracting action details: {exc}")
            return {}

        return details.model_dump() if hasattr(details, "model_dump") else details
