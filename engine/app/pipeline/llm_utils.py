from __future__ import annotations
import json
import logging
import re
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

_logger = logging.getLogger("setuq.llm_utils")

T = TypeVar("T", bound=BaseModel)

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", re.DOTALL)
_RAW_JSON_RE = re.compile(r"(\{[\s\S]*\}|\[[\s\S]*\])", re.DOTALL)


class LLMOutputValidationError(ValueError):
    """Raised when LLM output fails JSON parsing or Pydantic validation after all retries."""


def clean_llm_json(raw: str) -> str:
    """Extract the first JSON block from LLM output.

    Handles fenced blocks (```json...```), prose before a fence,
    <thinking> preambles, and unfenced JSON preceded by label text.
    """
    m = _FENCED_JSON_RE.search(raw)
    if m:
        return m.group(1).strip()
    stripped = re.sub(r"<[^>]+>.*?</[^>]+>", "", raw, flags=re.DOTALL).strip()
    m2 = _RAW_JSON_RE.search(stripped)
    if m2:
        return m2.group(1).strip()
    return raw.strip()


def parse_llm_json(raw: str, fallback: Any = None) -> Any:
    """Parse LLM response as JSON, returning fallback on failure."""
    try:
        return json.loads(clean_llm_json(raw))
    except json.JSONDecodeError:
        return fallback


async def generate_validated(
    llm,
    system_prompt: str,
    history: list[dict],
    user_prompt: str,
    model_class: Type[T],
    max_retries: int = 1,
) -> T:
    """Call LLM, parse JSON, validate against Pydantic model. Retry once with correction prompt on failure."""
    last_error: Exception | None = None
    current_user_prompt = user_prompt

    for attempt in range(max_retries + 1):
        response = await llm.generate(
            system_prompt=system_prompt,
            history=history,
            user_prompt=current_user_prompt,
        )
        raw = response.content
        try:
            data = json.loads(clean_llm_json(raw))
            return model_class.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            if attempt < max_retries:
                _logger.warning("LLM output validation failed (attempt %d): %s", attempt + 1, exc)
                current_user_prompt = (
                    f"{user_prompt}\n\n"
                    f"Your previous response failed validation: {exc}\n"
                    "Please respond with ONLY valid JSON matching the required schema. No markdown fences."
                )

    raise LLMOutputValidationError(f"LLM output validation failed after {max_retries + 1} attempts: {last_error}")
