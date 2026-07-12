import logging
import time
from openai import AsyncOpenAI
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.llm.pricing import compute_cost
from app.observability.decorators import trace_llm
from app.observability.redact import hash_query

_logger = logging.getLogger("setuq.gemini")

# Google exposes Gemini through an OpenAI-compatible endpoint, so we reuse the
# openai client instead of pulling in google-genai. Same request/response shape
# as OpenAIProvider; only the base_url and model names differ.
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str, base_url: str = DEFAULT_GEMINI_BASE_URL):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    @trace_llm(provider="gemini")
    async def generate(
        self,
        system_prompt: str,
        history: list[dict],
        user_prompt: str,
    ) -> LLMResponse:
        messages = [
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": user_prompt},
        ]
        _logger.debug(
            "gemini request model=%s history_turns=%d prompt_hash=%s prompt_len=%d",
            self.model, len(history), hash_query(user_prompt), len(user_prompt),
        )
        start = time.monotonic()
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        in_tok = response.usage.prompt_tokens if response.usage else 0
        out_tok = response.usage.completion_tokens if response.usage else 0
        content = response.choices[0].message.content
        _logger.debug(
            "gemini response model=%s latency_ms=%d in_tok=%d out_tok=%d",
            self.model, latency_ms, in_tok, out_tok,
        )
        return LLMResponse(
            content=content,
            usage=LLMUsage(
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost_usd=compute_cost(self.model, in_tok, out_tok),
                model=self.model,
                latency_ms=latency_ms,
            ),
        )

    async def close(self) -> None:
        await self.client.close()
