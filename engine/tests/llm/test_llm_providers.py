"""Tests for individual LLM provider message construction and response parsing."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx


# --- OpenAI Provider ---

class TestOpenAIProvider:
    @pytest.fixture
    def provider(self):
        with patch("app.llm.openai.AsyncOpenAI") as MockClient:
            from app.llm.openai import OpenAIProvider
            provider = OpenAIProvider(api_key="test-key", model="gpt-4o")
            provider.client = MockClient.return_value
            return provider

    @pytest.mark.asyncio
    async def test_message_construction(self, provider):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="result"))]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        provider.client.chat.completions.create = AsyncMock(return_value=mock_response)

        await provider.generate(
            system_prompt="You are helpful.",
            history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
            user_prompt="test query",
        )

        call_kwargs = provider.client.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "You are helpful."}
        assert messages[1] == {"role": "user", "content": "hi"}
        assert messages[2] == {"role": "assistant", "content": "hello"}
        assert messages[3] == {"role": "user", "content": "test query"}
        assert call_kwargs["temperature"] == 0

    @pytest.mark.asyncio
    async def test_returns_content(self, provider):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="generated text"))]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        provider.client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await provider.generate("sys", [], "prompt")
        assert result.content == "generated text"

    @pytest.mark.asyncio
    async def test_empty_history(self, provider):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
        mock_response.usage = MagicMock(prompt_tokens=5, completion_tokens=2)
        provider.client.chat.completions.create = AsyncMock(return_value=mock_response)

        await provider.generate("system", [], "user msg")
        messages = provider.client.chat.completions.create.call_args[1]["messages"]
        assert len(messages) == 2  # system + user only


# --- Anthropic Provider ---

class TestAnthropicProvider:
    @pytest.fixture
    def provider(self):
        with patch("app.llm.anthropic.AsyncAnthropic") as MockClient:
            from app.llm.anthropic import AnthropicProvider
            provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-20250514")
            provider.client = MockClient.return_value
            return provider

    @pytest.mark.asyncio
    async def test_system_prompt_separate(self, provider):
        """Anthropic takes system as separate param, not in messages."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="result")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        provider.client.messages.create = AsyncMock(return_value=mock_response)

        await provider.generate(
            system_prompt="You are an analyst.",
            history=[{"role": "user", "content": "hi"}],
            user_prompt="test",
        )

        call_kwargs = provider.client.messages.create.call_args[1]
        assert call_kwargs["system"] == "You are an analyst."
        messages = call_kwargs["messages"]
        # System should NOT be in messages
        assert not any(m.get("role") == "system" for m in messages)
        assert messages[0] == {"role": "user", "content": "hi"}
        assert messages[1] == {"role": "user", "content": "test"}

    @pytest.mark.asyncio
    async def test_returns_text(self, provider):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="anthropic output")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        provider.client.messages.create = AsyncMock(return_value=mock_response)

        result = await provider.generate("sys", [], "prompt")
        assert result.content == "anthropic output"

    @pytest.mark.asyncio
    async def test_max_tokens_and_temperature(self, provider):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_response.usage = MagicMock(input_tokens=5, output_tokens=2)
        provider.client.messages.create = AsyncMock(return_value=mock_response)

        await provider.generate("sys", [], "prompt")
        call_kwargs = provider.client.messages.create.call_args[1]
        assert call_kwargs["max_tokens"] == 4096
        assert call_kwargs["temperature"] == 0


# --- Ollama Provider ---

class TestOllamaProvider:
    @pytest.fixture
    def provider(self):
        from app.llm.ollama import OllamaProvider
        provider = OllamaProvider(base_url="http://localhost:11434", model="llama3")
        return provider

    @pytest.mark.asyncio
    async def test_message_construction(self, provider):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "ollama result"},
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            await provider.generate(
                system_prompt="system",
                history=[{"role": "user", "content": "prior"}],
                user_prompt="current",
            )

            call_kwargs = mock_client.post.call_args[1]
            messages = call_kwargs["json"]["messages"]
            assert messages[0] == {"role": "system", "content": "system"}
            assert messages[1] == {"role": "user", "content": "prior"}
            assert messages[2] == {"role": "user", "content": "current"}

    @pytest.mark.asyncio
    async def test_returns_content(self, provider):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "hello from ollama"},
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            result = await provider.generate("sys", [], "prompt")

        assert result.content == "hello from ollama"

    @pytest.mark.asyncio
    async def test_list_models(self, provider):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "models": [{"name": "llama3"}, {"name": "mistral"}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(provider, "_client") as mock_client:
            mock_client.get = AsyncMock(return_value=mock_response)
            models = await provider.list_models()

        assert models == ["llama3", "mistral"]

    @pytest.mark.asyncio
    async def test_list_models_empty(self, provider):
        mock_response = MagicMock()
        mock_response.json.return_value = {"models": []}
        mock_response.raise_for_status = MagicMock()

        with patch.object(provider, "_client") as mock_client:
            mock_client.get = AsyncMock(return_value=mock_response)
            models = await provider.list_models()

        assert models == []

    @pytest.mark.asyncio
    async def test_base_url_strips_trailing_slash(self):
        from app.llm.ollama import OllamaProvider
        provider = OllamaProvider(base_url="http://localhost:11434/", model="test")
        assert provider.base_url == "http://localhost:11434"

    @pytest.mark.asyncio
    async def test_http_error_raises(self, provider):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=MagicMock(status_code=500)
        )

        with patch.object(provider, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            with pytest.raises(httpx.HTTPStatusError):
                await provider.generate("sys", [], "prompt")
