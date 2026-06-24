import pytest
from app.llm.base import LLMProvider
from app.llm.factory import create_llm_provider
from app.config import Settings


def test_factory_creates_openai_provider():
    settings = Settings(
        LLM_PROVIDER="openai",
        LLM_MODEL="gpt-4o",
        LLM_API_KEY="test-key",
        SPLUNK_PASSWORD="x",
    )
    provider = create_llm_provider(settings)
    assert isinstance(provider, LLMProvider)


def test_factory_creates_anthropic_provider():
    settings = Settings(
        LLM_PROVIDER="anthropic",
        LLM_MODEL="claude-sonnet-4-20250514",
        LLM_API_KEY="test-key",
        SPLUNK_PASSWORD="x",
    )
    provider = create_llm_provider(settings)
    assert isinstance(provider, LLMProvider)


def test_factory_raises_on_unknown_provider():
    settings = Settings(
        LLM_PROVIDER="unknown",
        LLM_MODEL="model",
        LLM_API_KEY="test-key",
        SPLUNK_PASSWORD="x",
    )
    with pytest.raises(ValueError, match="Unknown LLM provider: unknown"):
        create_llm_provider(settings)


def test_factory_creates_ollama_provider():
    settings = Settings(
        LLM_PROVIDER="ollama",
        LLM_MODEL="gemma3:12b",
        LLM_API_KEY="",
        SPLUNK_PASSWORD="x",
    )
    provider = create_llm_provider(settings)
    assert isinstance(provider, LLMProvider)
