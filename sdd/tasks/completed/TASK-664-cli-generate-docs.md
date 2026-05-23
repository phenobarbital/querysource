# TASK-664: CLI Documentation Generator

**Feature**: FEAT-095 — MultiQuery Documentation System
**Spec**: `sdd/specs/multiquery-documentation.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-663
**Assigned-to**: unassigned

---

## Context

> Implements Module 5 from the spec. Creates an argparse-based CLI command that
> discovers all MultiQuery components via ComponentRegistry, extracts their
> documentation using introspection classmethods, and writes per-component JSON
> files to a `generated/` directory.

---

## Scope

- Create `querysource/cli/` directory with `__init__.py` and `generate_docs.py`
- Implement `generate_docs(output_dir)` function:
  - Uses `ComponentRegistry.get_catalog()` to discover all components
  - Calls `get_schema()` and `get_description()` on each
  - Writes one JSON file per component to `<output_dir>/` (default: `generated/`)
  - Each JSON file contains: name, description, usage, category, json_schema, attributes, example
- Implement `main()` with argparse:
  - `--output-dir` / `-o`: output directory (default: `generated/`)
  - `--category` / `-c`: filter by category (optional)
  - `--format` / `-f`: output format — `json` (default) or `summary` (brief text)
- Register entry point in `pyproject.toml`: `generate-multiquery-docs = "querysource.cli.generate_docs:main"`
- Write tests

**NOT in scope**: HTTP endpoints (TASK-665/666), modifying existing CLI (`__cli__.py`)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/cli/__init__.py` | CREATE | Package init |
| `querysource/cli/generate_docs.py` | CREATE | CLI command implementation |
| `pyproject.toml` | MODIFY | Add entry point |
| `tests/test_cli_generate_docs.py` | CREATE | Tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Registry (created by TASK-663)
from querysource.queries.multi.registry import ComponentRegistry, ComponentInfo

# Serialization
import orjson  # verified: existing dep, used throughout codebase

# stdlib
import argparse
from pathlib import Path
```

### Existing Signatures to Use
```python
# ComponentRegistry (created by TASK-663)
@classmethod def get_catalog(cls) -> list[ComponentInfo]: ...

# ComponentInfo dataclass (created by TASK-663)
@dataclass
class ComponentInfo:
    name: str
    category: str
    description: str
    usage: str
    attributes: list[AttributeInfo]
    json_schema: dict
    example: dict

# Existing CLI entry point pattern (from pyproject.toml)
# [project.scripts]
# query = "querysource.__cli__:main"
```

### Does NOT Exist
- ~~`querysource/cli/`~~ — directory does not exist; YOU create it
- ~~`generated/`~~ — directory does not exist; CLI creates it on run
- ~~click or typer~~ — no CLI framework in deps; use argparse (stdlib)

---

## Implementation Notes

### Output JSON Format

Per-component file (e.g., `generated/Join.json`):
```json
{
    "name": "Join",
    "category": "Operators",
    "description": "Join two or more DataFrames on shared columns or index.",
    "usage": "Performs SQL-style joins between DataFrames in the pipeline.",
    "attributes": [
        {"name": "type", "type": "str", "default": "inner", "required": false},
        {"name": "left", "type": "str", "default": null, "required": true},
        {"name": "right", "type": "str", "default": null, "required": true}
    ],
    "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "title": "Join",
        "properties": { ... },
        "required": ["left", "right"]
    },
    "example": {
        "Join": {"type": "inner", "left": "revenue", "right": "costs", "on": "date"}
    }
}
```

### Key Constraints
- Use `orjson` for JSON serialization (consistent with rest of codebase)
- Create output directory if it doesn't exist
- Handle `ImportError` gracefully for components with optional deps
- Print summary to stdout after generation

---

## Acceptance Criteria

- [ ] `querysource/cli/generate_docs.py` exists with `generate_docs()` and `main()` functions
- [ ] Running `generate-multiquery-docs` produces JSON files in `generated/`
- [ ] Each JSON file contains: name, description, usage, category, json_schema, attributes, example
- [ ] `--output-dir` flag works to change output directory
- [ ] `--category` flag filters output by category
- [ ] Entry point registered in `pyproject.toml`
- [ ] Tests pass: `pytest tests/test_cli_generate_docs.py -v`

---

## Test Specification

```python
# tests/test_cli_generate_docs.py
import pytest
import tempfile
import json
from pathlib import Path
from querysource.cli.generate_docs import generate_docs


class TestGenerateDocs:
    def test_generates_files(self, tmp_path):
        generate_docs(output_dir=str(tmp_path))
        json_files = list(tmp_path.glob("*.json"))
        assert len(json_files) > 0

    def test_file_has_required_fields(self, tmp_path):
        generate_docs(output_dir=str(tmp_path))
        json_files = list(tmp_path.glob("*.json"))
        assert len(json_files) > 0
        with open(json_files[0]) as f:
            data = json.load(f)
        for field in ["name", "category", "description", "json_schema", "attributes"]:
            assert field in data, f"Missing field: {field}"

    def test_json_schema_format(self, tmp_path):
        generate_docs(output_dir=str(tmp_path))
        json_files = list(tmp_path.glob("*.json"))
        with open(json_files[0]) as f:
            data = json.load(f)
        assert "$schema" in data["json_schema"]
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-documentation.spec.md` for full context
2. **Check dependencies** — verify TASK-663 is in `sdd/tasks/completed/`
3. **Create** `querysource/cli/` directory and files
4. **Implement** the CLI command
5. **Update** `pyproject.toml` with the new entry point
6. **Run tests**: `source .venv/bin/activate && pytest tests/test_cli_generate_docs.py -v`
7. **Move this file** to `sdd/tasks/completed/TASK-664-cli-generate-docs.md`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: sdd-worker (Claude)
**Date**: 2026-05-20
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none
