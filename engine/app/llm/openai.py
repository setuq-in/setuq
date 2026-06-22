import time
from openai import AsyncOpenAI
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.llm.pricing import compute_cost
from app.observability.decorators import trace_llm


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    @trace_llm(provider="openai")
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
        start = time.monotonic()
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        in_tok = response.usage.prompt_tokens if response.usage else 0
        out_tok = response.usage.completion_tokens if response.usage else 0
        return LLMResponse(
            content=response.choices[0].message.content,
            usage=LLMUsage(
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost_usd=compute_cost(self.model, in_tok, out_tok),
                model=self.model,
                latency_ms=latency_ms,
            ),
        )
