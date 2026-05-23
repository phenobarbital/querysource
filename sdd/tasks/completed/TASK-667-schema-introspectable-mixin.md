# TASK-667: Extract SchemaIntrospectable Mixin and Refactor AbstractMulti

**Feature**: FEAT-097 — New Destination Folder for MultiQuery
**Spec**: `sdd/specs/new-destination-multiquery.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Foundation of FEAT-097. The introspection classmethods (`get_attributes`, `get_schema`, `get_description`) currently live on `AbstractMulti`. To give `AbstractDestination` the same introspection without copying code (and without forcing destinations to inherit the data-handling `__init__`/lifecycle of `AbstractMulti`), extract those methods into a `SchemaIntrospectable` mixin that both classes can inherit. Implements spec §3 Modules 1 and 2.

---

## Scope

- Create `querysource/queries/multi/_introspect.py` containing `SchemaIntrospectable` plus the three private helpers (`_type_to_json_schema`, `_hint_to_str`, `_parse_default`).
- Refactor `querysource/queries/multi/abstract.py` so `AbstractMulti(SchemaIntrospectable, ABC)` and the three classmethods + three helpers are deleted from `abstract.py` (now provided by the mixin).
- `AbstractMulti.__init__`, `__aenter__`/`__aexit__`, `start`, `run`, `close`, `_print_info`, and `_category = "Components"` stay in `abstract.py`.
- Add a focused unit test that exercises the mixin in isolation.
- Existing tests for operators/transformations that call `get_schema()`/`get_attributes()`/`get_description()` must continue to pass without modification.

**NOT in scope**:
- Touching `AbstractDestination` (TASK-668).
- Creating `queries/multi/destinations/` (TASK-669).
- Touching `ComponentRegistry` (TASK-672).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/_introspect.py` | CREATE | New mixin module with `SchemaIntrospectable` + three private helpers |
| `querysource/queries/multi/abstract.py` | MODIFY | Inherit from `SchemaIntrospectable`; delete classmethods + helpers now in the mixin; keep lifecycle + `_category` |
| `tests/test_schema_introspectable.py` | CREATE | New unit test exercising the mixin in isolation |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# verified: querysource/queries/multi/abstract.py:13
from abc import ABC, abstractmethod
# verified: querysource/queries/multi/abstract.py:14
from typing import Any, Union
# verified: querysource/queries/multi/abstract.py:16
import pandas as pd
# verified: querysource/queries/multi/abstract.py:18
from ...exceptions import QueryException
```

### Existing Signatures to Use
```python
# querysource/queries/multi/abstract.py — current state (before this task)
class AbstractMulti(ABC):                                       # line 39
    _category: str = "Components"                               # line 50
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None: ...  # line 52
    async def __aenter__(self): ...                             # line 67
    async def __aexit__(self, exc_type, exc_value, traceback): ...  # line 71
    async def start(self): ...                                  # line 85
    @abstractmethod
    async def run(self): ...                                    # line 92
    async def close(self): ...                                  # line 99
    def _print_info(self, df: pd.DataFrame) -> None: ...        # line 107
    # ↓↓↓ MOVE THESE TO _introspect.py ↓↓↓
    @classmethod
    def get_attributes(cls) -> list[dict]: ...                  # line 118
    @classmethod
    def get_schema(cls) -> dict: ...                            # line 191
    @classmethod
    def get_description(cls) -> dict: ...                       # line 216

# Module-level helpers — MOVE TO _introspect.py
def _type_to_json_schema(type_str: str) -> dict: ...            # line 23
def _hint_to_str(hint) -> str: ...                              # line 292
def _parse_default(default_str: str | None) -> Any: ...         # line 308
```

### Existing call sites (must keep working)
```python
# querysource/queries/multi/registry.py:167
from querysource.queries.multi.abstract import AbstractMulti
# registry.py:174-195 calls comp_cls.get_schema() and comp_cls.get_description()
# for any class that issubclass(cls, AbstractMulti). After this refactor, the
# same calls keep working because AbstractMulti inherits the mixin.
```

### Does NOT Exist
- ~~`querysource.queries.multi._introspect`~~ — module to be created by this task.
- ~~`SchemaIntrospectable`~~ — class to be created.
- ~~`SchemaIntrospectable` as ABC~~ — it is a plain class, not `ABC`. Subclasses combine it with `ABC` when needed.
- ~~`AbstractMulti.json_schema`~~ / ~~`AbstractMulti.attributes`~~ — only the three classmethods exist; do not invent instance attributes.

---

## Implementation Notes

### Pattern to Follow

Copy the body of `get_attributes`, `get_schema`, `get_description`, `_type_to_json_schema`, `_hint_to_str`, `_parse_default` from `querysource/queries/multi/abstract.py` verbatim. Place them in `_introspect.py`:

```python
# querysource/queries/multi/_introspect.py
"""
SchemaIntrospectable — class-introspection mixin for MultiQuery components.

Provides three classmethods that derive JSON Schema and attribute lists from
class-level type annotations and ``kwargs.pop(...)`` / ``kwargs.get(...)``
patterns inside ``__init__``.

This mixin is inherited by both :class:`AbstractMulti` (operators, transforms,
sources) and :class:`AbstractDestination` so the documentation endpoint can
introspect every component uniformly.
"""
from __future__ import annotations

import inspect
import json
import re
import typing
from typing import Any, Union


def _type_to_json_schema(type_str: str) -> dict: ...
def _hint_to_str(hint) -> str: ...
def _parse_default(default_str: str | None) -> Any: ...


class SchemaIntrospectable:
    """Mixin providing JSON-Schema introspection from class annotations + __init__."""

    _category: str = "Components"

    @classmethod
    def get_attributes(cls) -> list[dict]: ...
    @classmethod
    def get_schema(cls) -> dict: ...
    @classmethod
    def get_description(cls) -> dict: ...
```

Then in `abstract.py`:

```python
from ._introspect import SchemaIntrospectable

class AbstractMulti(SchemaIntrospectable, ABC):
    # _category stays here too (overrides the mixin default with the same value;
    # explicit is fine, mirrors existing layout)
    _category: str = "Components"
    # ... lifecycle methods unchanged ...
```

### Key Constraints

- The `_skip = {"data"}` guard inside `get_attributes` (currently at `abstract.py:139`) must be preserved so introspection ignores the `data` parameter.
- The `kwargs.pop`/`kwargs.get` regex (currently at `abstract.py:165-168`) must be copied verbatim.
- The MRO walk in `get_attributes` currently breaks at `AbstractMulti`, `ABC`, or `object` (`abstract.py:156-157`). After the refactor it must break at `SchemaIntrospectable`, `ABC`, or `object` — otherwise a destination subclass would never have its `__init__` source inspected.
- Keep the `if kwarg_name in ("backend",): continue` skip in `get_attributes` (currently `abstract.py:172-173`).
- Do NOT import `pandas` inside `_introspect.py` — the mixin doesn't need it. `pandas` stays imported in `abstract.py` (used by `_print_info` and type hints).
- The mixin must be importable without triggering any MultiQuery side effects (no imports from `querysource.queries.multi.*` other than stdlib).

### References in Codebase
- Current source — `querysource/queries/multi/abstract.py:23-332` (the methods + helpers being moved)
- Caller — `querysource/queries/multi/registry.py:167-195` (uses `get_schema`/`get_description`)

---

## Acceptance Criteria

- [ ] `querysource/queries/multi/_introspect.py` exists and exposes `SchemaIntrospectable`, `_type_to_json_schema`, `_hint_to_str`, `_parse_default`.
- [ ] `SchemaIntrospectable._category` defaults to `"Components"`.
- [ ] `AbstractMulti` inherits `(SchemaIntrospectable, ABC)` and its `__init__`, `__aenter__`, `__aexit__`, `start`, `run`, `close`, `_print_info` bodies are unchanged.
- [ ] `querysource/queries/multi/abstract.py` no longer defines `get_attributes`, `get_schema`, `get_description`, `_type_to_json_schema`, `_hint_to_str`, or `_parse_default`.
- [ ] `import` chain still works: `from querysource.queries.multi.abstract import AbstractMulti` resolves.
- [ ] Existing tests pass without modification:
  - `pytest tests/test_component_registry.py -v`
  - `pytest tests/test_source_registry.py -v`
  - Any test that calls `<operator>.get_schema()` (search `grep -r get_schema tests/`)
- [ ] New test `tests/test_schema_introspectable.py` passes — exercises the mixin on a standalone class.
- [ ] `ruff check querysource/queries/multi/_introspect.py querysource/queries/multi/abstract.py` — no errors.

---

## Test Specification

```python
# tests/test_schema_introspectable.py
"""Unit tests for the SchemaIntrospectable mixin."""
from __future__ import annotations

import pytest

from querysource.queries.multi._introspect import SchemaIntrospectable


class _Fake(SchemaIntrospectable):
    """Test class.

    Usage: Fake(foo='hello', bar=1)

    Example:
        {"foo": "hello"}
    """

    _category = "Destinations"

    def __init__(self, **kwargs) -> None:
        self._foo = kwargs.pop("foo", "default-foo")
        self._bar = kwargs.pop("bar", 42)


class TestSchemaIntrospectable:
    def test_get_attributes_lists_kwarg_pops(self):
        attrs = _Fake.get_attributes()
        names = {a["name"] for a in attrs}
        assert {"foo", "bar"}.issubset(names)

    def test_get_attributes_includes_defaults(self):
        attrs = {a["name"]: a for a in _Fake.get_attributes()}
        assert attrs["foo"]["default"] == "default-foo"
        assert attrs["bar"]["default"] == 42

    def test_get_schema_returns_json_schema_and_attributes(self):
        schema = _Fake.get_schema()
        assert schema["json_schema"]["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["json_schema"]["title"] == "_Fake"
        assert "foo" in schema["json_schema"]["properties"]
        assert "bar" in schema["json_schema"]["properties"]

    def test_get_description_reads_category(self):
        desc = _Fake.get_description()
        assert desc["name"] == "_Fake"
        assert desc["category"] == "Destinations"
        assert desc["description"].startswith("Test class")

    def test_skips_backend_kwarg(self):
        class _WithBackend(SchemaIntrospectable):
            def __init__(self, **kwargs):
                self._backend = kwargs.pop("backend", "sqlite")
                self._x = kwargs.pop("x", 1)
        attrs = {a["name"] for a in _WithBackend.get_attributes()}
        assert "backend" not in attrs
        assert "x" in attrs
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/new-destination-multiquery.spec.md` for full context (especially §2 Overview, §3 Modules 1-2, §6 Codebase Contract).
2. **Check dependencies** — none.
3. **Verify the Codebase Contract** — `grep -n "def get_attributes\|def get_schema\|def get_description\|_type_to_json_schema\|_hint_to_str\|_parse_default" querysource/queries/multi/abstract.py` should produce the same line numbers as listed above. If they have drifted, update this contract first.
4. **Update status** in `sdd/tasks/index/new-destination-multiquery.json` → `"in-progress"`.
5. **Implement** the mixin module, then refactor `abstract.py`, then add the unit test.
6. **Verify** all acceptance criteria — run the full destination/source/registry test suite at the end.
7. **Move this file** to `sdd/tasks/completed/TASK-667-schema-introspectable-mixin.md`.
8. **Update index** → `"done"`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
