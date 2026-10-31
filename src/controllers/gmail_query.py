"""Turn a natural-language request into a validated Gmail search (query + result count)."""
import logging
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from helpers.exceptions import LLMOutputError, LLMUnavailable
from helpers.llm_json import llm_json, parse_llm_json

logger = logging.getLogger("uvicorn.error")

DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_LIMIT = 20
MAX_QUERY_LENGTH = 200

PROMPT = """Convert the user's request into a Gmail search and a result limit.
Return ONLY a valid JSON object with EXACTLY two keys: "query" (string) and "max_results" (integer). No markdown, no explanation.

Rules for "query":
- If the user asks for the latest emails with no specific filter (e.g. "last 5 mails", "اخر 3 رسايل"), use an empty string "".
- Use newer_than / older_than ONLY for a real time range (e.g. "yesterday" -> newer_than:2d older_than:1d). Never use them to fake a result count.
- If unsure, use "".

Rules for "max_results":
- The exact number of emails requested (e.g. "last 3 mails" -> 3). If no number is requested, use 5.

Examples:
- "emails from ahmed" -> {{"query": "from:ahmed", "max_results": 5}}
- "what is my last 3 mails" -> {{"query": "", "max_results": 3}}
- "اخر 10 ايميلات امبارح" -> {{"query": "newer_than:2d older_than:1d", "max_results": 10}}
- "رسايل فيها مرفقات من جوجل" -> {{"query": "has:attachment from:google", "max_results": 5}}

User request: "{user_query}"
"""


class GmailSearchPlan(BaseModel):
    query: str = ""
    max_results: int = Field(default=DEFAULT_MAX_RESULTS)

    @field_validator("query", mode="before")
    @classmethod
    def _clean_query(cls, value):
        if value is None:
            return ""
        # Strip wrapping quotes/backticks the model sometimes adds; refuse multi-line values.
        text = str(value).strip().strip("'\"`").strip()
        if "\n" in text or "\r" in text:
            return ""
        return text[:MAX_QUERY_LENGTH]

    @field_validator("max_results", mode="before")
    @classmethod
    def _clamp_results(cls, value):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return DEFAULT_MAX_RESULTS
        return max(1, min(number, MAX_RESULTS_LIMIT))


def parse_gmail_plan(raw_text: Optional[str]) -> GmailSearchPlan:
    """Parse a model reply into a plan; 'latest emails, default count' if nothing usable is in it."""
    try:
        return parse_llm_json(raw_text, GmailSearchPlan)
    except LLMOutputError:
        return GmailSearchPlan()


async def build_gmail_query(generation_client, user_query: str, chat_history: list = None) -> GmailSearchPlan:
    """Ask the model for a Gmail search. An unusable reply yields the latest emails; an unavailable model raises."""
    if generation_client is None:
        return GmailSearchPlan()
    try:
        plan = await llm_json(
            generation_client,
            PROMPT.format(user_query=user_query.replace('"', "'")),
            GmailSearchPlan,
            chat_history=chat_history or [],
            max_output_tokens=150,
            temperature=0.0,
            json_mode=True,
        )
    except LLMOutputError as exc:
        logger.warning(f"Gmail query reply unusable ({exc}); using the latest emails")
        return GmailSearchPlan()
    except LLMUnavailable:
        raise  # searching "the latest emails" instead would silently ignore what the user asked for
    except Exception as exc:
        logger.warning(f"Failed to generate Gmail query: {exc}")
        return GmailSearchPlan()
    has_filter = bool(plan.query)  # whether, not what: the filter names senders and topics
    logger.info(f"Gmail search plan: filter={'yes' if has_filter else 'none'} max_results={plan.max_results}")
    return plan
