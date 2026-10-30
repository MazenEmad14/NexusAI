"""Turn a natural-language request into a validated Gmail search (query + result count)."""
import json
import logging
import re
from typing import Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

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
    """Parse model output into a plan. Falls back to field-by-field regex for malformed JSON,
    and to 'latest emails, default count' when nothing usable is found."""
    text = (raw_text or "").strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return GmailSearchPlan(**{k: data[k] for k in ("query", "max_results") if k in data})
        except (json.JSONDecodeError, ValidationError, TypeError):
            pass

    plan = {}
    q_match = re.search(r'"query"\s*:\s*"([^"\n]*)"', text)
    if q_match:
        plan["query"] = q_match.group(1)
    m_match = re.search(r'"max_results"\s*:\s*(\d+)', text)
    if m_match:
        plan["max_results"] = m_match.group(1)
    return GmailSearchPlan(**plan)


async def build_gmail_query(generation_client, user_query: str, chat_history: list = None) -> GmailSearchPlan:
    """Ask the model for a Gmail search; never raises (a failure yields the latest emails)."""
    if generation_client is None:
        return GmailSearchPlan()
    try:
        response = await generation_client.agenerate_text(
            prompt=PROMPT.format(user_query=user_query.replace('"', "'")),
            chat_history=chat_history or [],
            max_output_tokens=150,
            temperature=0.0,
        )
    except Exception as exc:
        logger.warning(f"Failed to generate Gmail query: {exc}")
        return GmailSearchPlan()
    plan = parse_gmail_plan(response)
    logger.info(f"Gmail search plan: query={plan.query!r} max_results={plan.max_results}")
    return plan
