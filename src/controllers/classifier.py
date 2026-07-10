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

            response = self.generation_client.generate_text(
                prompt=f"Classify this query: \"{query}\"",
                chat_history=messages,
                max_output_tokens=100,
                temperature=0.0
            )
            logger.info(f"Raw classifier LLM output: {response}")

            if not response:
                logger.error("Empty response from LLM classifier")
                return {"request_type": "retrieval", "action_type": "", "priority": self._detect_priority(query)}

            # First try to parse as JSON
            try:
                # Find the first '{' and last '}' to handle potential markdown wrappers
                start_idx = response.find('{')
                end_idx = response.rfind('}')
                if start_idx != -1 and end_idx != -1:
                    json_str = response[start_idx:end_idx+1]
                    data = json.loads(json_str)
                    
                    req_type = data.get("request_type", data.get("category", "retrieval")).lower()
                    act_type = data.get("action_type", data.get("subcategory", data.get("type", "")))
                    if act_type is None: act_type = ""
                    act_type = str(act_type).lower()
                    
                    # If LLM didn't provide action_type explicitly, search all JSON values
                    if act_type not in ["summary", "send_email", "gmail", "calendar", "reminder", "data_analysis"]:
                        all_values = " ".join([str(v) for v in data.values()]).lower()
                        if "send_email" in all_values: act_type = "send_email"
                        elif "calendar" in all_values: act_type = "calendar"
                        elif "gmail" in all_values: act_type = "gmail"
                        elif "data_analysis" in all_values: act_type = "data_analysis"
                        elif "reminder" in all_values: act_type = "reminder"
                        elif "summary" in all_values and "action + summary" in response.lower(): act_type = "summary"
                        else: act_type = ""
                        
                    if act_type in ["summary", "send_email", "gmail", "calendar", "reminder", "data_analysis"]:
                        req_type = "action"
                            
                    if req_type not in ["action", "retrieval", "web_search", "conversational"]:
                        if "web_search" in response.lower():
                            req_type = "web_search"
                        elif "conversational" in response.lower():
                            req_type = "conversational"
                        else:
                            req_type = "retrieval"
                    
                    priority = data.get("priority", "medium").lower()
                    if priority not in ["low", "medium", "high", "critical"]:
                        priority = self._detect_priority(query)
                        
                    if act_type != "" or req_type in ["web_search", "action", "retrieval", "conversational"]:
                        return {
                            "request_type": req_type,
                            "action_type": act_type,
                            "priority": priority
                        }
            except Exception as e:
                logger.warning(f"Failed to parse LLM JSON: {e}")

            # Fallback robust matching (Ordered by specificity)
            response_text = response.lower()
            if "send_email" in response_text:
                return {"request_type": "action", "action_type": "send_email", "priority": self._detect_priority(query)}
            elif "calendar" in response_text:
                return {"request_type": "action", "action_type": "calendar", "priority": self._detect_priority(query)}
            elif "gmail" in response_text:
                return {"request_type": "action", "action_type": "gmail", "priority": self._detect_priority(query)}
            elif "data_analysis" in response_text:
                return {"request_type": "action", "action_type": "data_analysis", "priority": self._detect_priority(query)}
            elif "reminder" in response_text:
                return {"request_type": "action", "action_type": "reminder", "priority": self._detect_priority(query)}
            elif "action + summary" in response_text or '"action_type": "summary"' in response_text:
                return {"request_type": "action", "action_type": "summary", "priority": self._detect_priority(query)}
            elif "web_search" in response_text:
                return {"request_type": "web_search", "action_type": "", "priority": self._detect_priority(query)}
            elif "conversational" in response_text:
                return {"request_type": "conversational", "action_type": "", "priority": self._detect_priority(query)}
            else:
                return {"request_type": "retrieval", "action_type": "", "priority": self._detect_priority(query)}

        except Exception as e:
            logger.error(f"Error in query classification: {e}")
            return {"request_type": "retrieval", "action_type": "", "priority": self._detect_priority(query)}

    def _detect_priority(self, text: str) -> str:
        from services.retrieval import detect_priority
        return detect_priority(text)

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

RULES FOR EMAIL BODY:
1. Match the tone and length to what the user actually asked for. A quick casual note ("tell him I'll be late") should stay short and simple — a proper greeting and 1-2 direct sentences, no padding. A request for something formal, detailed, or business-facing should get a fully professional structure: greeting, clear body paragraphs, professional sign-off.
2. When the user gives brief points for a substantial email (report, proposal, formal request), expand them into a complete, well-articulated message — but don't invent facts, numbers, or claims the user didn't provide.
3. COVER LETTER MODE: If the user asks for a cover letter, write a professional, structured, persuasive letter tailored to the job/context given. Only reference skills, experience, or achievements the user actually mentioned or that appear in the conversation/uploaded resume — do NOT fabricate specific accomplishments, years of experience, or qualifications not provided. If key details are missing (target role, key strengths), use general professional phrasing rather than invented specifics.
4. RECIPIENT RULE: The recipient MUST be a valid email address if one is provided in the prompt. Do NOT extract just the first name (e.g., if the user says "to mazen@example.com", the recipient MUST be "mazen@example.com", not "Mazen"). If only a name is given and no email is available, use the name.
5. LANGUAGE: Write the email in the same language the user used to request it, unless they explicitly specify a different language for the email itself.

If any field is unclear, use a reasonable default."""

        return self._extract_with_llm(query, system_prompt, chat_history)

    def _extract_meeting_details(self, query: str, chat_history: list = None) -> dict:
        import datetime
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
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
- NEVER include inline comments or explanations in the JSON output (e.g. do NOT write `"title": "ميتنج" ("meeting" in Arabic)`). Output raw valid JSON only.
"""

        return self._extract_with_llm(query, system_prompt, chat_history)

    def _extract_reminder_details(self, query: str, chat_history: list = None) -> dict:
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
"""

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
                max_output_tokens=400,
                temperature=0.0
            )

            if not response:
                return {}

            response_text = response.strip()
            logger.info(f"[extract_with_llm] Raw LLM response: {repr(response_text[:300])}")

            # Extract JSON block between the first '{' and the last '}'
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')

            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx+1]

                try:
                    # strict=False allows unescaped control characters (like \n, \t) inside strings natively
                    return json.loads(json_str, strict=False)
                except json.JSONDecodeError:
                    # Fallback: some models return Python-style dicts with single quotes.
                    # ast.literal_eval handles these safely (no code execution).
                    import ast
                    try:
                        result = ast.literal_eval(json_str)
                        if isinstance(result, dict):
                            logger.info("[extract_with_llm] Recovered via ast.literal_eval fallback")
                            return result
                    except Exception:
                        pass
                    logger.error(f"[extract_with_llm] JSON parse failed. json_str={repr(json_str[:200])}")
                    return {}
            else:
                logger.error(f"[extract_with_llm] No JSON object found in response: {repr(response_text[:200])}")
                return {}

        except Exception as e:
            logger.error(f"Error extracting action details: {e}")
            return {}

