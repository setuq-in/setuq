from __future__ import annotations
import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
import yaml

_logger = logging.getLogger("setuq.schema_manager")


class SchemaManager:
    def __init__(
        self,
        overrides_path: str = "schema_overrides.yaml",
        cache_path: str | None = None,
        discovery=None,
        ttl_hours: float = 24.0,
        on_change: Callable[[list[str]], None] | None = None,
        max_context_chars: int | None = None,
    ) -> None:
        self._schema: dict = {"indexes": {}}
        self._overrides_path = overrides_path
        self._discovery = discovery
        self._cache = None
        self._ttl_hours = ttl_hours
        self._on_change = on_change
        # Cap the schema prompt only for small-context models (e.g. Ollama);
        # None = no limit, so large-context providers get the full schema.
        self._max_context_chars = max_context_chars

        if cache_path:
            from app.pipeline.schema_discovery import SchemaCache
            self._cache = SchemaCache(db_path=cache_path)
            cached = self._cache.load()
            if cached and not self._cache.is_stale(ttl_hours):
                _logger.info("Schema loaded from cache (captured_at=%s)", self._cache.captured_at())
                self._schema = cached

        self._load_overrides(overrides_path)

    def _load_overrides(self, path: str) -> None:
        file_path = Path(path)
        if not file_path.exists():
            return
        try:
            content = yaml.safe_load(file_path.read_text())
            if not content:
                return
            if "indexes" in content:
                self._apply_overrides(content)
            for key in ("glossary", "investigation_patterns", "eventtypes", "macros", "lookups", "soc_queries"):
                if key in content:
                    self._schema[key] = content[key]
        except yaml.YAMLError:
            return

    def get_schema(self) -> dict:
        return self._schema

    def _apply_overrides(self, overrides: dict) -> None:
        """Deep-merge override schema on top of discovered schema (override wins).

        Unlike merge_discovered (which preserves existing/override data over
        freshly-discovered data), overrides explicitly take precedence: an
        override sourcetype's description/relationships/fields/etc. replace or
        extend whatever auto-discovery produced, with field lists unioned by
        name so override-supplied descriptions aren't lost on later refreshes.
        """
        for index_name, index_data in overrides.get("indexes", {}).items():
            existing_index = self._schema["indexes"].setdefault(index_name, {})
            for key, value in index_data.items():
                if key != "sourcetypes":
                    existing_index[key] = value
            existing_sourcetypes = existing_index.setdefault("sourcetypes", {})
            for st_name, st_override in index_data.get("sourcetypes", {}).items():
                existing_st = existing_sourcetypes.setdefault(st_name, {})
                for key, value in st_override.items():
                    if key != "fields":
                        existing_st[key] = value
                override_fields = st_override.get("fields")
                if override_fields is not None:
                    existing_st["fields"] = self._merge_fields(
                        existing_st.get("fields", []), override_fields
                    )

    @staticmethod
    def _merge_fields(existing: list, override: list) -> list:
        """Union field lists by name; override entries replace existing ones."""

        def field_name(field):
            return field.get("name") if isinstance(field, dict) else field

        merged = {field_name(f): f for f in existing}
        for field in override:
            merged[field_name(field)] = field
        return list(merged.values())

    def merge_discovered(self, discovered: dict) -> None:
        """Merge auto-discovered schema. Overrides take precedence on conflicts."""
        for index_name, index_data in discovered.get("indexes", {}).items():
            if index_name not in self._schema["indexes"]:
                self._schema["indexes"][index_name] = index_data
                continue
            existing_sourcetypes = self._schema["indexes"][index_name].get("sourcetypes", {})
            for st_name, st_data in index_data.get("sourcetypes", {}).items():
                if st_name not in existing_sourcetypes:
                    existing_sourcetypes[st_name] = st_data
            self._schema["indexes"][index_name]["sourcetypes"] = existing_sourcetypes

    @staticmethod
    def _format_field(field) -> str:
        if isinstance(field, dict):
            name = field.get("name", "")
            descr = field.get("description")
            ftype = field.get("type")
            suffix = f" ({ftype})" if ftype else ""
            return f"{name}{suffix}: {descr}" if descr else f"{name}{suffix}"
        return str(field)

    def get_prompt_context(self) -> str:
        """Format schema as a string suitable for LLM system prompts.

        Renders not just index/sourcetype/field names but the semantic layer
        from schema_overrides.yaml (descriptions, relationships, derived
        metrics, common questions, glossary, investigation patterns) so the
        planner and SPL generator can reason about joins, business meaning,
        and multi-step decompositions — not just valid field names.
        """
        lines = ["Available Splunk Schema:"]
        for index_name, index_data in self._schema.get("indexes", {}).items():
            lines.append(f"\nIndex: {index_name}")
            if descr := index_data.get("description"):
                lines.append(f"  Description: {descr.strip()}")
            if role := index_data.get("role"):
                lines.append(f"  Role: {role}")
            for st_name, st_data in index_data.get("sourcetypes", {}).items():
                lines.append(f"  Sourcetype: {st_name}")
                if st_descr := st_data.get("description"):
                    lines.append(f"    Description: {st_descr.strip()}")
                if time_field := st_data.get("time_field"):
                    lines.append(f"    Time field: {time_field}")
                fields = st_data.get("fields", [])
                if fields:
                    lines.append("    Fields:")
                    for field in fields:
                        lines.append(f"      - {self._format_field(field)}")
                for rel in st_data.get("relationships", []):
                    lines.append(
                        f"    Join: {rel.get('field')} -> {rel.get('references')}"
                        f" ({rel.get('purpose', '')})"
                    )
                for metric in st_data.get("derived_metrics", []):
                    lines.append(
                        f"    Derived metric: {metric.get('name')} = {metric.get('formula')}"
                        f" — {metric.get('meaning', '')}"
                    )
                for question in st_data.get("common_questions", []):
                    lines.append(f"    Example question: {question}")

        if eventtypes := self._schema.get("eventtypes"):
            lines.append("\nEventtypes (pre-built SPL filters — use `eventtype=<name>` instead of hand-coding):")
            for et in eventtypes:
                tags_str = f"  [tags: {', '.join(et['tags'])}]" if et.get("tags") else ""
                lines.append(f"  - eventtype={et.get('name')}: {et.get('search', '')}{tags_str}")

        if macros := self._schema.get("macros"):
            lines.append("\nMacros (use `name` or `name(arg)` inline in SPL):")
            for m in macros:
                lines.append(f"  - {m.get('call')}: {m.get('definition', '')[:120]}")

        if lookups := self._schema.get("lookups"):
            lines.append("\nLookup tables (enrich with `| lookup <name> <key_field>`):")
            for lk in lookups:
                fields_str = ", ".join(lk.get("fields") or [])
                hint = f"  — {lk['use_when']}" if lk.get("use_when") else ""
                lines.append(f"  - {lk.get('name')} ({lk.get('type')}): [{fields_str}]{hint}")

        if soc_queries := self._schema.get("soc_queries"):
            lines.append("\nScheduled SOC queries (reference patterns for this environment):")
            for q in soc_queries:
                desc = f" — {q['description']}" if q.get("description") else ""
                lines.append(f"  - {q.get('name')}{desc}: {q.get('query', '')[:150]}")

        if glossary := self._schema.get("glossary"):
            lines.append("\nGlossary (business term -> schema mapping):")
            for entry in glossary:
                lines.append(f"  - \"{entry.get('term')}\" -> {entry.get('maps_to')}")

        if patterns := self._schema.get("investigation_patterns"):
            lines.append("\nInvestigation patterns (reusable multi-step decompositions):")
            for pattern in patterns:
                lines.append(f"  - {pattern.get('name')} (applies to: {pattern.get('applies_to')})")
                for step in pattern.get("steps", []):
                    lines.append(f"      {step}")

        result = "\n".join(lines)
        # TO-DO - consider smarter truncation that preserves more of the semantic layer and doesn't cut off mid-index
        max_chars = self._max_context_chars
        if max_chars is not None and len(result) > max_chars:
            result = result[:max_chars] + "\n... [schema truncated — context limit]"
            _logger.warning("Schema context truncated to %d chars", max_chars)
        return result

    async def refresh(self, max_indexes: int = 20) -> None:
        """Run full schema discovery and persist to cache."""
        if self._discovery is None:
            _logger.debug("Schema discovery not configured — skip refresh")
            return
        try:
            discovered = await self._discovery.discover_full(max_indexes=max_indexes)
            self.merge_discovered(discovered)
            self._load_overrides(self._overrides_path)
            if self._cache:
                self._cache.save(self._schema)
            indexes = list(self._schema.get("indexes", {}).keys())
            _logger.info("Schema refreshed: %d indexes", len(indexes))
            _logger.info("schema.changed indexes=%s", indexes)
            if self._on_change:
                self._on_change(indexes)
        except Exception as exc:
            _logger.warning("Schema refresh failed: %s", exc)

    async def refresh_index(self, index_name: str) -> None:
        """Reactive refresh for a single index on guardrail violation."""
        if self._discovery is None:
            return
        try:
            sourcetypes = await self._discovery.discover_sourcetypes(index_name)
            fields = await self._discovery.discover_fields(index_name)
            sourcetypes_dict = {st: {"fields": list(fields.keys())} for st in sourcetypes}
            partial = {"indexes": {index_name: {"sourcetypes": sourcetypes_dict, "fields": fields}}}
            self.merge_discovered(partial)
            if self._cache:
                self._cache.save(self._schema)
            indexes = list(self._schema.get("indexes", {}).keys())
            _logger.info("schema.changed index=%s (reactive)", index_name)
            if self._on_change:
                self._on_change(indexes)
        except Exception as exc:
            _logger.warning("Reactive schema refresh failed for index=%s: %s", index_name, exc)
