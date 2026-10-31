"""One way to get validated JSON out of a language model.

Models wrap JSON in prose or code fences, leave trailing commas, add `// comments`, or answer with
a Python-style dict. Every place that needed structured output used to parse it differently. Now:

    plan = await llm_json(client, prompt, GmailSearchPlan, json_mode=True)

  1. the JSON is located and cleaned (`extract_json`),
  2. validated against a Pydantic schema (`parse_llm_json`),
  3. and when the reply is unusable the model is asked once more, shown what was wrong,
  4. after which `LLMOutputError` carries the last raw reply so a caller can fall back.

Errors from the provider itself (network, quota) are not caught here: they propagate unchanged.
"""
import ast
import json
import logging
from typing import Any, Optional

from pydantic import TypeAdapter, ValidationError

from helpers.exceptions import LLMOutputError

logger = logging.getLogger("uvicorn.error")

_MAX_CANDIDATES = 25  # brackets tried before giving up on a reply
_DECODER = json.JSONDecoder(strict=False)  # strict=False tolerates raw newlines/tabs inside strings


def _clean(text: str) -> str:
    """Drop `//` and `/* */` comments and trailing commas that sit outside JSON strings."""
    out, i, n = [], 0, len(text)
    in_string = escaped = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        elif ch == ",":
            # a comma directly before a closing bracket is a trailing comma
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
            out.append(ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def extract_json(text: Optional[str]) -> Any:
    """The first JSON object or array found in `text`, or None.

    Works through each `{` or `[` in turn, so prose such as "Sure [see below]: {...}" is skipped over.
    """
    if not text:
        return None
    candidates = 0
    for start, ch in enumerate(text):
        if ch not in "{[":
            continue
        candidates += 1
        if candidates > _MAX_CANDIDATES:
            break
        try:
            value, _ = _DECODER.raw_decode(_clean(text[start:]))
        except (json.JSONDecodeError, RecursionError):  # absurdly nested output must not crash the caller
            continue
        if isinstance(value, (dict, list)):
            return value

    # Last resort: some models answer with a Python-style dict (single quotes, True/None).
    # literal_eval only reads literals, it cannot run code, and the result is still validated.
    first, last = text.find("{"), text.rfind("}")
    if 0 <= first < last:
        try:
            value = ast.literal_eval(text[first:last + 1])
            if isinstance(value, dict):
                return value
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            pass
    return None


def parse_llm_json(text: Optional[str], schema) -> Any:
    """Locate the JSON in `text` and validate it against `schema` (a Pydantic model or type)."""
    data = extract_json(text)
    if data is None:
        raise LLMOutputError("the reply contains no JSON", raw=text or "")
    try:
        return TypeAdapter(schema).validate_python(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'reply'}: {e['msg']}" for e in exc.errors()[:5])
        raise LLMOutputError(f"the JSON does not match the expected format ({problems})", raw=text or "") from exc


def _repair_prompt(prompt: str, error: LLMOutputError) -> str:
    return (
        f"{prompt}\n\n---\n"
        f"Your previous reply could not be used: {error}.\n"
        f"Your previous reply was:\n{error.raw[:400]}\n\n"
        "Reply again with ONLY the corrected JSON, in the format requested above."
    )


def _is_unsupported_json_mode(exc: Exception) -> bool:
    """A provider rejecting `response_format` (an old model, say) says so in a 4xx error."""
    text = str(exc).lower()
    return any(word in text for word in ("response_format", "json_object", "json mode", "json_mode"))


async def llm_json(client, prompt: str, schema, *, chat_history: list = None, max_output_tokens: int = 400,
                   temperature: float = 0.0, retries: int = 1, json_mode: bool = False):
    """Ask the model for JSON and return it validated. Raises LLMOutputError if it never is."""
    use_json_mode = json_mode
    error: Optional[LLMOutputError] = None
    for attempt in range(retries + 1):
        current = prompt if error is None else _repair_prompt(prompt, error)
        kwargs = {"chat_history": chat_history, "max_output_tokens": max_output_tokens, "temperature": temperature}
        if use_json_mode:
            kwargs["json_mode"] = True
        try:
            reply = await client.agenerate_text(prompt=current, **kwargs)
        except Exception as exc:
            if use_json_mode and _is_unsupported_json_mode(exc):
                logger.warning("The model rejected JSON mode; asking without it")
                use_json_mode = False
                reply = await client.agenerate_text(
                    prompt=current, chat_history=chat_history, max_output_tokens=max_output_tokens, temperature=temperature
                )
            else:
                raise
        try:
            return parse_llm_json(reply, schema)
        except LLMOutputError as exc:
            error = exc
            logger.info(f"Model JSON attempt {attempt + 1} unusable: {exc}")
    raise error


def llm_json_sync(client, prompt: str, schema, *, chat_history: list = None, max_output_tokens: int = 400,
                  temperature: float = 0.0, retries: int = 1, json_mode: bool = False):
    """`llm_json` for synchronous callers (code that already runs in a worker thread)."""
    use_json_mode = json_mode
    error: Optional[LLMOutputError] = None
    for attempt in range(retries + 1):
        current = prompt if error is None else _repair_prompt(prompt, error)
        kwargs = {"chat_history": chat_history, "max_output_tokens": max_output_tokens, "temperature": temperature}
        if use_json_mode:
            kwargs["json_mode"] = True
        try:
            reply = client.generate_text(prompt=current, **kwargs)
        except Exception as exc:
            if use_json_mode and _is_unsupported_json_mode(exc):
                logger.warning("The model rejected JSON mode; asking without it")
                use_json_mode = False
                reply = client.generate_text(
                    prompt=current, chat_history=chat_history, max_output_tokens=max_output_tokens, temperature=temperature
                )
            else:
                raise
        try:
            return parse_llm_json(reply, schema)
        except LLMOutputError as exc:
            error = exc
            logger.info(f"Model JSON attempt {attempt + 1} unusable: {exc}")
    raise error
