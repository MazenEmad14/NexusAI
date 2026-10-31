from .BaseController import BaseController
from .llm_schemas import Classification, EmailDetails, FileDetails, MeetingDetails, ReminderDetails
from helpers.exceptions import LLMOutputError, LLMUnavailable
from helpers.llm_json import llm_json_sync
from helpers.prompts import render
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

        system_prompt = render("classifier", "classify_system")

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
                    render("classifier", "classify_user", query=query),
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
        system_prompt = render("classifier", "email_system")

        return self._extract_with_llm(query, system_prompt, chat_history, EmailDetails)

    def _extract_meeting_details(self, query: str, chat_history: list = None, timezone_name: str = None) -> dict:
        from helpers.timezones import local_now, resolve_timezone
        # The current time in the user's zone, so "tomorrow" is the user's tomorrow, not the server's.
        now = f"{local_now(timezone_name).strftime('%Y-%m-%d %H:%M')} ({resolve_timezone(timezone_name).key})"
        system_prompt = render("classifier", "meeting_system", now=now)

        return self._extract_with_llm(query, system_prompt, chat_history, MeetingDetails)

    def _extract_reminder_details(self, query: str, chat_history: list = None, timezone_name: str = None) -> dict:
        from helpers.timezones import local_now, resolve_timezone
        today = local_now(timezone_name)
        system_prompt = render("classifier", "reminder_system",
            current_date=f"{today.strftime('%Y-%m-%d')}, time zone {resolve_timezone(timezone_name).key}",
            current_weekday=today.strftime("%A"),
        )

        return self._extract_with_llm(query, system_prompt, chat_history, ReminderDetails)

    def _extract_data_analysis_details(self, query: str, chat_history: list = None, available_files: str = "") -> dict:
        system_prompt = render("classifier", "file_system", available_files=available_files or render("classifier", "no_files"))
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
                render("classifier", "extract_user", query=query),
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
