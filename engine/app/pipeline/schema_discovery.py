from __future__ import annotations
import json
import logging
import re
import sqlite3
import time
from pathlib import Path

_logger = logging.getLogger("setuq.schema_discovery")

# Field name patterns → inferred type
_IP_PATTERN = re.compile(r"(^|_)(ip|addr|address)($|_)|(ip|addr|address)$", re.IGNORECASE)
_TIME_PATTERN = re.compile(r"(time|timestamp|date|epoch)", re.IGNORECASE)
_NUMERIC_THRESHOLD = 0.9  # fraction of values that are numeric → "number"


def _infer_field_type(field_name: str, numeric_count: int, total_count: int) -> str:
    if _IP_PATTERN.search(field_name):
        return "ip"
    if _TIME_PATTERN.search(field_name) or field_name == "_time":
        return "timestamp"
    if total_count > 0 and (numeric_count / total_count) >= _NUMERIC_THRESHOLD:
        return "number"
    return "string"


class SchemaCache:
    """Minimal SQLite-backed schema cache."""

    _CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS schema_cache (
        id INTEGER PRIMARY KEY,
        schema_json TEXT NOT NULL,
        captured_at REAL NOT NULL
    )
    """

    def __init__(self, db_path: str = "engine/data/schema_cache.db") -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(path)
        with self._conn() as conn:
            conn.execute(self._CREATE_TABLE)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def save(self, schema: dict) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM schema_cache")
            conn.execute(
                "INSERT INTO schema_cache (id, schema_json, captured_at) VALUES (1, ?, ?)",
                (json.dumps(schema), time.time()),
            )

    def load(self) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT schema_json FROM schema_cache WHERE id=1"
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def is_stale(self, ttl_hours: float = 24.0) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT captured_at FROM schema_cache WHERE id=1"
            ).fetchone()
        if row is None:
            return True
        return (time.time() - row[0]) > (ttl_hours * 3600)

    def captured_at(self) -> float | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT captured_at FROM schema_cache WHERE id=1"
            ).fetchone()
        return row[0] if row else None


class SplunkSchemaDiscovery:
    """Discovers Splunk schema via metadata + fieldsummary SPL commands."""

    def __init__(self, splunk_client) -> None:
        self._client = splunk_client

    async def discover_indexes(self) -> list[str]:
        """Return list of non-internal index names via `| metadata type=sourcetypes`."""
        try:
            rows = await self._client.execute_spl(
                "| metadata type=sourcetypes | fields index | dedup index"
            )
            indexes = [r["index"] for r in rows if "index" in r and not r["index"].startswith("_")]
            if not indexes:
                # fallback: REST API discovery already on SplunkClient
                schema = await self._client.discover_schema()
                indexes = list(schema.get("indexes", {}).keys())
            return indexes
        except Exception as exc:
            _logger.warning("Schema index discovery failed: %s", exc)
            return []

    async def discover_sourcetypes(self, index: str) -> list[str]:
        """Return sourcetypes for a given index."""
        try:
            rows = await self._client.execute_spl(
                f"| metadata type=sourcetypes index={index} | fields sourcetype"
            )
            return [r["sourcetype"] for r in rows if "sourcetype" in r]
        except Exception as exc:
            _logger.warning("Sourcetype discovery failed for index=%s: %s", index, exc)
            return []

    async def discover_fields(self, index: str) -> dict[str, dict]:
        """Discover fields for an index via `| fieldsummary`. Returns {field_name: {type, ...}}."""
        try:
            rows = await self._client.execute_spl(
                f"index={index} earliest=-1h | fieldsummary maxsamples=500 | "
                f"where count > 0 | fields field,count,numeric_count | head 100"
            )
            fields: dict[str, dict] = {}
            for row in rows:
                name = row.get("field", "")
                if not name or name.startswith("_") and name != "_time":
                    continue
                try:
                    total = int(row.get("count", 0))
                    numeric = int(row.get("numeric_count", 0))
                except (ValueError, TypeError):
                    total, numeric = 0, 0
                fields[name] = {
                    "type": _infer_field_type(name, numeric, total),
                    "count": total,
                }
            return fields
        except Exception as exc:
            _logger.warning("Field discovery failed for index=%s: %s", index, exc)
            return {}

    async def discover_full(self, max_indexes: int = 20) -> dict:
        """Run full discovery: indexes → sourcetypes → fields. Returns schema dict."""
        indexes = await self.discover_indexes()
        schema: dict = {"indexes": {}}

        for idx_name in indexes[:max_indexes]:
            sourcetypes = await self.discover_sourcetypes(idx_name)
            fields = await self.discover_fields(idx_name)

            sourcetypes_dict: dict = {}
            for st in sourcetypes:
                # Distribute discovered fields across all sourcetypes (cheapest approach)
                sourcetypes_dict[st] = {"fields": list(fields.keys())}

            schema["indexes"][idx_name] = {
                "sourcetypes": sourcetypes_dict,
                "fields": fields,  # typed field map at index level
            }
            _logger.info(
                "Discovered index=%s sourcetypes=%d fields=%d",
                idx_name, len(sourcetypes), len(fields),
            )

        return schema
