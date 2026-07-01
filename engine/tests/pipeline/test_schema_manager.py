import pytest
from pathlib import Path
from app.pipeline.schema_manager import SchemaManager


@pytest.fixture
def overrides_path(tmp_path):
    overrides = tmp_path / "schema_overrides.yaml"
    overrides.write_text("""
indexes:
  chocolate_index:
    sourcetypes:
      sales:
        fields:
          - order_id
          - revenue
          - profit
      products:
        fields:
          - product_id
          - product_name
""")
    return str(overrides)


def test_load_overrides(overrides_path):
    manager = SchemaManager(overrides_path=overrides_path)
    schema = manager.get_schema()
    assert "chocolate_index" in schema["indexes"]
    assert "sales" in schema["indexes"]["chocolate_index"]["sourcetypes"]
    fields = schema["indexes"]["chocolate_index"]["sourcetypes"]["sales"]["fields"]
    assert "order_id" in fields
    assert "revenue" in fields


def test_schema_to_prompt_context(overrides_path):
    manager = SchemaManager(overrides_path=overrides_path)
    context = manager.get_prompt_context()
    assert "chocolate_index" in context
    assert "order_id" in context
    assert "product_name" in context


def test_prompt_context_not_truncated_without_limit(overrides_path):
    # Default (no max_context_chars) = no truncation, even for huge schemas.
    manager = SchemaManager(overrides_path=overrides_path)
    manager._schema["macros"] = [
        {"call": f"m{i}", "definition": "x" * 200} for i in range(500)
    ]
    context = manager.get_prompt_context()
    assert "[schema truncated" not in context
    assert len(context) > 24_000


def test_prompt_context_truncated_when_limit_set(overrides_path):
    # Ollama path passes a char cap -> schema is truncated to fit.
    manager = SchemaManager(overrides_path=overrides_path, max_context_chars=24_000)
    manager._schema["macros"] = [
        {"call": f"m{i}", "definition": "x" * 200} for i in range(500)
    ]
    context = manager.get_prompt_context()
    assert "[schema truncated — context limit]" in context
    assert len(context) <= 24_000 + 60


def test_merge_discovered_schema(overrides_path):
    manager = SchemaManager(overrides_path=overrides_path)
    discovered = {
        "indexes": {
            "chocolate_index": {
                "sourcetypes": {
                    "stores": {
                        "fields": ["store_id", "store_name", "city"]
                    }
                }
            }
        }
    }
    manager.merge_discovered(discovered)
    schema = manager.get_schema()
    assert "stores" in schema["indexes"]["chocolate_index"]["sourcetypes"]
    sales_fields = schema["indexes"]["chocolate_index"]["sourcetypes"]["sales"]["fields"]
    assert "order_id" in sales_fields


def test_empty_overrides_file(tmp_path):
    empty_path = str(tmp_path / "empty.yaml")
    Path(empty_path).write_text("")
    manager = SchemaManager(overrides_path=empty_path)
    schema = manager.get_schema()
    assert schema == {"indexes": {}}


def test_missing_overrides_file():
    manager = SchemaManager(overrides_path="/nonexistent/path.yaml")
    schema = manager.get_schema()
    assert schema == {"indexes": {}}
