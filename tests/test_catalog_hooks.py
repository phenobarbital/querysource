"""FEAT-157 — the Query catalog documents pre-hook / post-hook (TASK-826)."""
import json
from pathlib import Path

import pytest

from querysource.queries.multi.registry import ComponentRegistry

_GENERATED = Path(__file__).resolve().parent.parent / "generated" / "Query.json"


@pytest.fixture(scope="module")
def query_entry():
    catalog = {c.name: c for c in ComponentRegistry.get_catalog()}
    assert "Query" in catalog, "Query component missing from catalog"
    return catalog["Query"]


def test_catalog_lists_hooks(query_entry):
    attrs = {a.name: a for a in query_entry.attributes}
    for key in ("pre-hook", "post-hook"):
        assert key in attrs
        assert attrs[key].required is False
        assert attrs[key].default is None
        assert attrs[key].type == "str"
        assert key in query_entry.json_schema["properties"]
    assert "PostgreSQL" in attrs["pre-hook"].description
    assert "empty" in attrs["post-hook"].description


def test_generated_query_json_lists_hooks():
    data = json.loads(_GENERATED.read_text(encoding="utf-8"))
    names = [a["name"] for a in data["attributes"]]
    assert "pre-hook" in names and "post-hook" in names
    assert names.index("pre-hook") == names.index("tenant") + 1
