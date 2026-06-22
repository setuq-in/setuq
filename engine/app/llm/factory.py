from app.config import Settings
from app.llm.base import LLMProvider
from app.llm.openai import OpenAIProvider
from app.llm.anthropic import AnthropicProvider
from app.llm.ollama import OllamaProvider
from app.llm.harness import HarnessedProvider
from app.llm.fallback import FallbackProvider


def _make_base_provider(name: str, settings: Settings) -> LLMProvider:
    if name == "openai":
        return OpenAIProvider(api_key=settings.LLM_API_KEY, model=settings.LLM_MODEL)
    if name == "anthropic":
        return AnthropicProvider(api_key=settings.LLM_API_KEY, model=settings.LLM_MODEL)
    if name == "ollama":
        return OllamaProvider(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.LLM_MODEL,
            timeout=settings.OLLAMA_TIMEOUT_SECONDS,
            json_mode=settings.OLLAMA_JSON_MODE,
        )
    raise ValueError(f"Unknown LLM provider: {name}")


def create_llm_provider(settings: Settings) -> LLMProvider:
    primary = HarnessedProvider(
        base=_make_base_provider(settings.LLM_PROVIDER, settings),
        timeout_seconds=60.0,
        max_retries=3,
    )

    if not settings.FALLBACK_ENABLED or not settings.FALLBACK_PROVIDERS.strip():
        return primary

    chain: list[LLMProvider] = [primary]
    for name in settings.FALLBACK_PROVIDERS.split(","):
        name = name.strip()
        if not name or name == settings.LLM_PROVIDER:
            continue
        try:
            fallback_base = _make_base_provider(name, settings)
            chain.append(HarnessedProvider(base=fallback_base, timeout_seconds=60.0, max_retries=1))
        except ValueError:
            pass

    if len(chain) == 1:
        return primary
    return FallbackProvider(providers=chain)
