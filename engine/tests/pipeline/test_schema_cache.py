"""Tests for SchemaCache (SQLite-backed)."""
import time
import pytest
from app.pipeline.schema_discovery import SchemaCache


@pytest.fixture
def cache(tmp_path):
    return SchemaCache(db_path=str(tmp_path / "test_schema.db"))


def test_cache_save_and_load(cache):
    schema = {"indexes": {"main": {"sourcetypes": {"access_combined": {"fields": ["src_ip"]}}}}}
    cache.save(schema)
    loaded = cache.load()
    assert loaded == schema


def test_cache_load_empty_returns_none(cache):
    assert cache.load() is None


def test_cache_is_stale_when_empty(cache):
    assert cache.is_stale(ttl_hours=24.0) is True


def test_cache_not_stale_after_save(cache):
    cache.save({"indexes": {}})
    assert cache.is_stale(ttl_hours=24.0) is False


def test_cache_stale_with_zero_ttl(cache):
    cache.save({"indexes": {}})
    assert cache.is_stale(ttl_hours=0.0) is True


def test_cache_captured_at_none_when_empty(cache):
    assert cache.captured_at() is None


def test_cache_captured_at_set_after_save(cache):
    t_before = time.time()
    cache.save({"indexes": {}})
    t_after = time.time()
    ts = cache.captured_at()
    assert ts is not None
    assert t_before <= ts <= t_after


def test_cache_overwrites_on_second_save(cache):
    cache.save({"indexes": {"old": {}}})
    cache.save({"indexes": {"new": {}}})
    loaded = cache.load()
    assert "new" in loaded["indexes"]
    assert "old" not in loaded["indexes"]
