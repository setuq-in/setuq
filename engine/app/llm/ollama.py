import logging
import time
import httpx
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.llm.pricing import compute_cost
from app.observability.decorators import trace_llm
from app.observability.redact import hash_query

_logger = logging.getLogger("setuq.ollama")


class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str, model: str, timeout: float = 300.0, json_mode: bool = False):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._json_mode = json_mode
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )

    @trace_llm(provider="ollama")
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
        # Don't log raw prompts (PII) at WARNING on every request — a hash + length
        # is enough to correlate, at DEBUG.
        _logger.debug(
            "ollama request model=%s json_mode=%s prompt_hash=%s prompt_len=%d",
            self.model, self._json_mode, hash_query(user_prompt), len(user_prompt),
        )
        start = time.monotonic()
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 4096, "num_ctx": 32768},
        }
        if self._json_mode:
            payload["format"] = "json"
        response = await self._client.post("/api/chat", json=payload)
        response.raise_for_status()
        latency_ms = int((time.monotonic() - start) * 1000)
        data = response.json()
        content = data["message"]["content"]
        in_tok = data.get("prompt_eval_count", 0)
        out_tok = data.get("eval_count", 0)
        _logger.debug(
            "ollama response model=%s latency_ms=%d in_tok=%d out_tok=%d content_len=%d",
            self.model, latency_ms, in_tok, out_tok, len(content),
        )
        return LLMResponse(
            content=content,
            usage=LLMUsage(
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost_usd=0.0,
                model=self.model,
                latency_ms=latency_ms,
            ),
        )

    async def list_models(self) -> list[str]:
        """Return names of locally available Ollama models."""
        response = await self._client.get("/api/tags", timeout=10.0)
        response.raise_for_status()
        data = response.json()
        return [m["name"] for m in data.get("models", [])]

    async def close(self) -> None:
        await self._client.aclose()
