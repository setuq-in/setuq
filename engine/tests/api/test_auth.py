"""Sprint 2 — Auth tests: Bearer token dependency, disabled-auth pass-through."""
import os
import pytest
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


# ---------------------------------------------------------------------------
# Health endpoint — never requires auth
# ---------------------------------------------------------------------------

def test_health_no_auth_required():
    """Health check must return 200 with no Authorization header."""
    # Import fresh so env state is clean
    from httpx import Client as SyncClient
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# verify_api_key unit tests
# ---------------------------------------------------------------------------

def test_verify_api_key_rejects_wrong_token():
    """verify_api_key raises 401 for wrong token and for missing credentials."""
    from app.api.routes import verify_api_key

    with patch("app.api.routes._get_api_key", return_value="secret123"):
        # Wrong token
        bad_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(credentials=bad_creds)
        assert exc_info.value.status_code == 401

        # No credentials at all
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(credentials=None)
        assert exc_info.value.status_code == 401


def test_verify_api_key_accepts_correct_token():
    """verify_api_key must not raise when the correct token is supplied."""
    from app.api.routes import verify_api_key

    with patch("app.api.routes._get_api_key", return_value="secret123"):
        good_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="secret123")
        verify_api_key(credentials=good_creds)  # must not raise


def test_verify_api_key_disabled_when_empty():
    """Empty API_KEY disables auth — any/no credentials must pass silently."""
    from app.api.routes import verify_api_key

    with patch("app.api.routes._get_api_key", return_value=""):
        # No credentials
        verify_api_key(credentials=None)

        # Garbage credentials still pass when auth is disabled
        bad_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="garbage")
        verify_api_key(credentials=bad_creds)


# ---------------------------------------------------------------------------
# Integration: /query reachable without Bearer when API_KEY is unset
# ---------------------------------------------------------------------------

def test_query_auth_disabled_passes():
    """With API_KEY unset, /query must not return 401 (may return other codes)."""
    # Ensure API_KEY is absent from env
    env = {k: v for k, v in os.environ.items() if k != "API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app, raise_server_exceptions=False)
        # No Authorization header
        resp = client.post("/api/query", json={"query": "test"})
        # Should not be 401 — business logic errors (422, 500, 502) are fine
        assert resp.status_code != 401, (
            f"Expected non-401 when auth is disabled, got {resp.status_code}"
        )
