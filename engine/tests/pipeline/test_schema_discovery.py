"""Tests for SplunkSchemaDiscovery field parsing and type inference."""
import pytest
from app.pipeline.schema_discovery import SplunkSchemaDiscovery, _infer_field_type


# --- field type inference ---

def test_infer_ip_by_name():
    assert _infer_field_type("src_ip", 0, 100) == "ip"
    assert _infer_field_type("dest_ip", 0, 100) == "ip"
    assert _infer_field_type("clientip", 0, 100) == "ip"


def test_infer_timestamp_by_name():
    assert _infer_field_type("_time", 0, 100) == "timestamp"
    assert _infer_field_type("timestamp", 0, 100) == "timestamp"
    assert _infer_field_type("event_time", 0, 100) == "timestamp"


def test_infer_number_by_ratio():
    assert _infer_field_type("bytes_out", 95, 100) == "number"
    assert _infer_field_type("count", 100, 100) == "number"


def test_infer_string_default():
    assert _infer_field_type("user", 0, 100) == "string"
    assert _infer_field_type("action", 10, 100) == "string"


def test_infer_zero_total_is_string():
    assert _infer_field_type("unknown_field", 0, 0) == "string"


# --- SplunkSchemaDiscovery with mock client ---

class _MockSplunkClient:
    def __init__(self, responses: dict[str, list[dict]]):
        self._responses = responses

    async def execute_spl(self, spl: str) -> list[dict]:
        for key, rows in self._responses.items():
            if key in spl:
                return rows
        return []

    async def discover_schema(self) -> dict:
        return {"indexes": {"main": {}}}


@pytest.mark.asyncio
async def test_discover_indexes_parses_metadata_rows():
    client = _MockSplunkClient({
        "metadata type=sourcetypes": [
            {"index": "main"},
            {"index": "security"},
            {"index": "_internal"},  # should be filtered
        ]
    })
    disc = SplunkSchemaDiscovery(client)
    indexes = await disc.discover_indexes()
    assert "main" in indexes
    assert "security" in indexes
    assert "_internal" not in indexes


@pytest.mark.asyncio
async def test_discover_indexes_falls_back_to_rest_api():
    client = _MockSplunkClient({})  # metadata returns nothing
    disc = SplunkSchemaDiscovery(client)
    indexes = await disc.discover_indexes()
    assert "main" in indexes  # from discover_schema fallback


@pytest.mark.asyncio
async def test_discover_sourcetypes():
    client = _MockSplunkClient({
        "metadata type=sourcetypes index=main": [
            {"sourcetype": "access_combined"},
            {"sourcetype": "syslog"},
        ]
    })
    disc = SplunkSchemaDiscovery(client)
    sts = await disc.discover_sourcetypes("main")
    assert "access_combined" in sts
    assert "syslog" in sts


@pytest.mark.asyncio
async def test_discover_fields_parses_fieldsummary():
    client = _MockSplunkClient({
        "fieldsummary": [
            {"field": "src_ip", "count": "100", "numeric_count": "0"},
            {"field": "bytes", "count": "100", "numeric_count": "100"},
            {"field": "_raw", "count": "100", "numeric_count": "0"},  # should be filtered (_)
        ]
    })
    disc = SplunkSchemaDiscovery(client)
    fields = await disc.discover_fields("main")
    assert "src_ip" in fields
    assert fields["src_ip"]["type"] == "ip"
    assert "bytes" in fields
    assert fields["bytes"]["type"] == "number"
    assert "_raw" not in fields


@pytest.mark.asyncio
async def test_discover_full_builds_schema():
    client = _MockSplunkClient({
        "metadata type=sourcetypes | fields index": [{"index": "main"}],
        "metadata type=sourcetypes index=main": [{"sourcetype": "access_combined"}],
        "fieldsummary": [{"field": "src_ip", "count": "50", "numeric_count": "0"}],
    })
    disc = SplunkSchemaDiscovery(client)
    schema = await disc.discover_full(max_indexes=5)
    assert "main" in schema["indexes"]
    assert "access_combined" in schema["indexes"]["main"]["sourcetypes"]
    assert "src_ip" in schema["indexes"]["main"]["fields"]
