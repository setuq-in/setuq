import os

import pytest

import app.pipeline.prompt_registry as prompt_registry
from app.pipeline.guardrails import load_guardrail_config

_CONFIG_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "config")
PROMPTS_PATH = os.path.join(_CONFIG_DIR, "prompts.yaml")
GUARDRAILS_PATH = os.path.join(_CONFIG_DIR, "guardrails.yaml")


@pytest.fixture(autouse=True)
def _load_shipped_prompts():
    """Load the shipped prompts so agents have their system prompts.

    Prompts now live ONLY in config/prompts.yaml (no code defaults), so every
    test that exercises an agent needs them loaded. Function-scoped + cleared so
    registry-mechanics tests that mutate state don't leak into other tests.
    """
    prompt_registry._prompts.clear()
    prompt_registry.load(PROMPTS_PATH)
    yield
    prompt_registry._prompts.clear()


@pytest.fixture(scope="session")
def shipped_guardrail_config():
    """The guardrail rules from the shipped config/guardrails.yaml."""
    return load_guardrail_config(GUARDRAILS_PATH)
