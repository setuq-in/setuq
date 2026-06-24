import os
import pytest
from app.config import Settings


def test_settings_loads_defaults():
    # _env_file=None: ignore any local engine/.env so this asserts the in-code
    # defaults, not a developer's machine-specific overrides.
    settings = Settings(
        _env_file=None,
        SPLUNK_PASSWORD="testpass",
        LLM_API_KEY="testkey",
    )
    assert settings.SPLUNK_HOST == "localhost"
    assert settings.SPLUNK_PORT == 8089
    assert settings.SPLUNK_USERNAME == "admin"
    assert settings.SPLUNK_PASSWORD == "testpass"
    assert settings.SPLUNK_VERIFY_SSL is True
    assert settings.LLM_PROVIDER == "openai"
    assert settings.LLM_MODEL == "gpt-4o"
    assert settings.LLM_API_KEY == "testkey"
    assert settings.APP_HOST == "0.0.0.0"
    assert settings.APP_PORT == 8000
