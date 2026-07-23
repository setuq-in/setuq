"""Production-hardening regression tests: startup fail-closed, config-driven
rate limits, liveness/readiness probes."""
import os
from unittest.mock import patch

import pytest

from app.config import Settings
from app.main import _validate_startup_config


# --- Startup config validation (fail-closed in prod, warn in dev) -----------

def test_production_hard_fails_on_empty_api_key():
    cfg = Settings(ENVIRONMENT="production", API_KEY="",
                   UI_ORIGINS="https://app.example.com", SPLUNK_VERIFY_SSL=True)
    with pytest.raises(RuntimeError, match="API_KEY is empty"):
        _validate_startup_config(cfg)


def test_production_hard_fails_on_wildcard_cors():
    cfg = Settings(ENVIRONMENT="production", API_KEY="secret",
                   UI_ORIGINS="*", SPLUNK_VERIFY_SSL=True)
    with pytest.raises(RuntimeError, match="wildcard"):
        _validate_startup_config(cfg)


def test_production_hard_fails_on_unverified_splunk_tls():
    cfg = Settings(ENVIRONMENT="production", API_KEY="secret",
                   UI_ORIGINS="https://app.example.com", SPLUNK_VERIFY_SSL=False)
    with pytest.raises(RuntimeError, match="TLS"):
        _validate_startup_config(cfg)


def test_production_boots_with_secure_config():
    cfg = Settings(ENVIRONMENT="production", API_KEY="secret",
                   UI_ORIGINS="https://app.example.com", SPLUNK_VERIFY_SSL=True)
    _validate_startup_config(cfg)  # must not raise


def test_development_only_warns_never_raises():
    cfg = Settings(ENVIRONMENT="development", API_KEY="", UI_ORIGINS="*",
                   SPLUNK_VERIFY_SSL=False)
    _validate_startup_config(cfg)  # must not raise


# --- Config-driven rate limits (no longer hardcoded) ------------------------

def test_ip_rate_limit_reads_env():
    from app.api.routes import _ip_rate_limit
    with patch.dict(os.environ, {"RATE_LIMIT_PER_IP": "5/minute"}):
        assert _ip_rate_limit() == "5/minute"


def test_session_rate_limit_parses_env():
    from app.api.routes import _session_rate_limit
    with patch.dict(os.environ, {"RATE_LIMIT_PER_SESSION": "7/minute"}):
        assert _session_rate_limit() == 7


def test_session_rate_limit_falls_back_on_garbage():
    from app.api.routes import _session_rate_limit
    with patch.dict(os.environ, {"RATE_LIMIT_PER_SESSION": "not-a-number"}):
        assert _session_rate_limit() == 10


# --- Liveness / readiness probes --------------------------------------------

def test_liveness_always_ok_no_auth():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    assert client.get("/api/health/live").json()["status"] == "ok"


def test_readiness_503_before_startup():
    """Without the lifespan running, app.state.ready is False -> 503."""
    from fastapi.testclient import TestClient
    from app.main import app
    app.state.ready = False
    client = TestClient(app)  # bare client does not trigger lifespan
    assert client.get("/api/health/ready").status_code == 503
