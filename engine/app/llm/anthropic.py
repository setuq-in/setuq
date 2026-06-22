import time
from anthropic import AsyncAnthropic
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.llm.pricing import compute_cost
from app.observability.decorators import trace_llm


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model

    @trace_llm(provider="anthropic")
    async def generate(
        self,
        system_prompt: str,
        history: list[dict],
        user_prompt: str,
    ) -> LLMResponse:
        # Anthropic takes system as a separate param, not in the messages array
        messages = [
            *history,
            {"role": "user", "content": user_prompt},
        ]
        start = time.monotonic()
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
            temperature=0,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        in_tok = response.usage.input_tokens if response.usage else 0
        out_tok = response.usage.output_tokens if response.usage else 0
        return LLMResponse(
            content=response.content[0].text,
            usage=LLMUsage(
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost_usd=compute_cost(self.model, in_tok, out_tok),
                model=self.model,
                latency_ms=latency_ms,
            ),
        )
