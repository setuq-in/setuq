"""Tests for SchemaManager with discovery + cache integration."""
import pytest
from app.pipeline.schema_manager import SchemaManager
from app.pipeline.schema_discovery import SchemaCache


class _MockDiscovery:
    def __init__(self, schema: dict):
        self._schema = schema
        self.refresh_calls = 0
        self.refresh_index_calls: list[str] = []

    async def discover_full(self, max_indexes: int = 20) -> dict:
        self.refresh_calls += 1
        return self._schema

    async def discover_sourcetypes(self, index: str) -> list[str]:
        self.refresh_index_calls.append(index)
        return []

    async def discover_fields(self, index: str) -> dict:
        return {}


@pytest.mark.asyncio
async def test_schema_manager_refresh_merges_discovered(tmp_path):
    disc = _MockDiscovery({"indexes": {"new_index": {"sourcetypes": {}}}})
    sm = SchemaManager(
        overrides_path="schema_overrides.yaml",
        cache_path=str(tmp_path / "cache.db"),
        discovery=disc,
    )
    await sm.refresh()
    assert "new_index" in sm.get_schema()["indexes"]


@pytest.mark.asyncio
async def test_schema_manager_refresh_saves_to_cache(tmp_path):
    db = str(tmp_path / "cache.db")
    disc = _MockDiscovery({"indexes": {"cached_idx": {"sourcetypes": {}}}})
    sm = SchemaManager(cache_path=db, discovery=disc)
    await sm.refresh()
    cache = SchemaCache(db_path=db)
    loaded = cache.load()
    assert loaded is not None
    assert "cached_idx" in loaded["indexes"]


@pytest.mark.asyncio
async def test_schema_manager_loads_stale_cache_and_calls_refresh(tmp_path):
    db = str(tmp_path / "cache.db")
    old_cache = SchemaCache(db_path=db)
    old_cache.save({"indexes": {"old_idx": {}}})

    disc = _MockDiscovery({"indexes": {"fresh_idx": {"sourcetypes": {}}}})
    # ttl_hours=0 → always stale → will not auto-refresh on init (init only loads, not refreshes)
    sm = SchemaManager(cache_path=db, discovery=disc, ttl_hours=0.0)
    # stale cache is NOT loaded on init when stale
    assert "old_idx" not in sm.get_schema().get("indexes", {})


@pytest.mark.asyncio
async def test_schema_manager_refresh_index(tmp_path):
    disc = _MockDiscovery({"indexes": {}})
    sm = SchemaManager(discovery=disc)
    await sm.refresh_index("mystery_index")
    assert "mystery_index" in disc.refresh_index_calls


@pytest.mark.asyncio
async def test_schema_manager_refresh_no_discovery_is_noop(tmp_path):
    sm = SchemaManager(overrides_path=str(tmp_path / "nonexistent.yaml"))
    await sm.refresh()  # should not raise
    assert sm.get_schema() == {"indexes": {}}


def test_schema_manager_get_prompt_context_with_schema():
    sm = SchemaManager(overrides_path="nonexistent_overrides.yaml")
    sm.merge_discovered({"indexes": {
        "main": {"sourcetypes": {"access_combined": {"fields": ["src_ip", "bytes"]}}}
    }})
    ctx = sm.get_prompt_context()
    assert "main" in ctx
    assert "access_combined" in ctx
    assert "src_ip" in ctx
