# TASK-698: Catalog & Schema Update for Remote Execution

**Feature**: FEAT-101 — MultiQuery Remote Execution
**Spec**: `sdd/specs/multiquery-remote-execution.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-695
**Assigned-to**: unassigned

---

## Context

> Updates ThreadQuery's `_catalog` dict and `json_schema` to document the new `remote`
> (boolean) and `worker` (string, optional) keys. Also updates the QueryHandler in
> `handlers/multi.py` to emit the `X-Remote-Queries` response header.
> Implements Spec §3 (Module 6).

---

## Scope

- Update `ThreadQuery._catalog["attributes"]` in `query.py` to add:
  - `remote`: `{"name": "remote", "type": "bool", "required": False, "default": False, ...}`
  - `worker`: `{"name": "worker", "type": "str", "required": False, "default": None, ...}`
- Update `ThreadQuery._catalog["json_schema"]["properties"]` to add:
  - `"remote": {"type": "boolean", "description": "..."}`
  - `"worker": {"type": "string", "description": "..."}`
- Update the `example` in `_catalog` to show a remote query example
- Modify `QueryHandler.query()` in `handlers/multi.py` to add `X-Remote-Queries` header
  from `MultiQS._remote_queries` (after TASK-696 adds this attribute)

**NOT in scope**: Executor implementation (TASK-693/694), ThreadQuery logic (TASK-695),
MultiQS dispatch changes (TASK-696)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/query.py` | MODIFY | Update _catalog attributes, json_schema, example |
| `querysource/handlers/multi.py` | MODIFY | Add X-Remote-Queries header |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# No new imports needed for catalog changes — all in existing query.py
```

### Existing Signatures to Use
```python
# querysource/queries/multi/sources/query.py:23-125 — _catalog dict structure:
_catalog = {
    "display_name": "Query",
    "description": "...",
    "usage": "...",
    "icon": "database",
    "attributes": [                         # line 38 — list of attribute dicts
        {"name": "slug", "type": "str", ...},        # line 39
        {"name": "query", "type": "str", ...},       # line 49
        {"name": "driver", "type": "str", ...},      # line 59
        {"name": "datasource", "type": "str", ...},  # line 69
    ],
    "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "slug": {...},       # line 89
            "query": {...},      # line 93
            "driver": {...},     # line 97
            "datasource": {...}, # line 101
        },
        "oneOf": [...],                              # line 106
        "additionalProperties": True,                # line 110
    },
    "example": "...",                                # line 112-124
}

# querysource/handlers/multi.py — response header pattern:
# line 339: 'X-Slug': str(slug),
# line 340: 'X-Total-Time': f'{total_time:.2f} seconds',
```

### Does NOT Exist
- ~~`_catalog["attributes"]` entry for "remote"~~ — does not exist yet; this task adds it
- ~~`_catalog["json_schema"]["properties"]["remote"]`~~ — does not exist yet
- ~~`X-Remote-Queries` header~~ — not yet emitted by QueryHandler

---

## Implementation Notes

### Key Constraints
- **additionalProperties is already True**: The JSON schema at line 110 already allows
  extra keys, so `remote` and `worker` won't break validation. Adding them to the schema
  is for documentation, not enforcement.
- **X-Remote-Queries header**: Access `qs._remote_queries` (added by TASK-696) after
  `result, options = await qs.query()`. If the list is non-empty, set
  `'X-Remote-Queries': ','.join(qs._remote_queries)` on the response headers.
- **Example update**: Show a MultiQS config with mixed local and remote queries.

### References in Codebase
- `querysource/queries/multi/sources/query.py:23-125` — full _catalog dict
- `querysource/handlers/multi.py:339-393` — response header setting

---

## Acceptance Criteria

- [ ] `_catalog["attributes"]` includes `remote` and `worker` entries
- [ ] `json_schema["properties"]` includes `remote` (boolean) and `worker` (string)
- [ ] Catalog `example` shows a remote query configuration
- [ ] `X-Remote-Queries` response header emitted when remote queries are present
- [ ] No linting errors on modified files

---

## Test Specification

```python
# tests/test_catalog_remote.py
from querysource.queries.multi.sources.query import ThreadQuery


class TestCatalogRemoteKeys:
    def test_remote_attribute_in_catalog(self):
        attr_names = [a["name"] for a in ThreadQuery._catalog["attributes"]]
        assert "remote" in attr_names
        assert "worker" in attr_names

    def test_remote_in_json_schema(self):
        props = ThreadQuery._catalog["json_schema"]["properties"]
        assert "remote" in props
        assert props["remote"]["type"] == "boolean"
        assert "worker" in props
        assert props["worker"]["type"] == "string"
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-remote-execution.spec.md` for full context
2. **Check dependencies** — verify TASK-695 is in `sdd/tasks/completed/`
3. **Verify the Codebase Contract** — read query.py _catalog and handlers/multi.py headers
4. **Update status** in `sdd/tasks/index/multiquery-remote-execution.json` → `"in-progress"`
5. **Implement** following the scope, codebase contract, and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-698-catalog-schema-update.md`
8. **Update index** → `"done"`
9. **Fill in the Completion Note** below

---

## Completion Note

**Completed by**: claude-sonnet-4-6
**Date**: 2026-05-26
**Notes**: Updated ThreadQuery._catalog to include remote (bool, default False) and worker (str, default None) attributes. Added both to json_schema["properties"]. Updated example to show mixed local/remote query config. Updated QueryHandler in handlers/multi.py to emit X-Remote-Queries header using getattr(qs, '_remote_queries', []) after qs.query() completes. Pre-existing F841 lint issue (unused `meta` variable at line 117 of multi.py) was already present before this task.

**Deviations from spec**: none
