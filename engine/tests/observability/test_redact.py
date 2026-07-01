from app.observability.redact import hash_query, scrub_dict


def test_hash_query_length():
    assert len(hash_query("hello")) == 16


def test_hash_query_deterministic():
    assert hash_query("same") == hash_query("same")


def test_hash_query_different_inputs():
    assert hash_query("a") != hash_query("b")


def test_scrub_dict_redacts_sensitive_keys():
    d = {"password": "secret", "api_key": "key123", "data": "visible"}
    result = scrub_dict(d)
    assert result["password"] == "[REDACTED]"
    assert result["api_key"] == "[REDACTED]"
    assert result["data"] == "visible"


def test_scrub_dict_case_insensitive():
    d = {"Authorization": "Bearer token", "normal": "ok"}
    result = scrub_dict(d)
    assert result["Authorization"] == "[REDACTED]"
    assert result["normal"] == "ok"


def test_scrub_dict_empty():
    assert scrub_dict({}) == {}
