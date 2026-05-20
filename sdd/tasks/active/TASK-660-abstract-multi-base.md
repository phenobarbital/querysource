# TASK-660: AbstractMulti Base Class

**Feature**: FEAT-095 — MultiQuery Documentation System
**Spec**: `sdd/specs/multiquery-documentation.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

> Implements Module 1 from the spec. This is the foundational task — all other
> tasks depend on it. Creates the unified base class that extracts duplicated
> boilerplate from AbstractTransform, AbstractOperator, and AbstractComponent
> and adds the introspection classmethods used by documentation tooling.

---

## Scope

- Create `querysource/queries/multi/abstract.py` with `AbstractMulti(ABC)`
- Extract the common init pattern: `__init__(self, data, **kwargs)` with `setattr` loop
- Extract the async context manager: `__aenter__` calling `start()`, `__aexit__` calling `close()` with `QueryException` on error
- Extract lifecycle methods: `start()` (default no-op), `run()` (abstractmethod), `close()` (default no-op)
- Add unified `_print_info(self, df)` debug helper
- Implement `@classmethod get_attributes(cls)` — uses `typing.get_type_hints()` for class-level annotations and inspects `__init__` source for `kwargs.pop`/`kwargs.get` patterns; falls back to `Any` for untyped attrs
- Implement `@classmethod get_schema(cls)` — returns `{"json_schema": {...}, "attributes": [...]}` where `json_schema` follows JSON Schema draft-2020-12
- Implement `@classmethod get_description(cls)` — returns `{"name": str, "description": str, "usage": str, "category": str, "example": dict}` extracted from docstring
- Add `_category: str = "Components"` class-level attribute for category classification (subclasses override)
- Write unit tests in `tests/test_abstract_multi.py`

**NOT in scope**: Refactoring existing abstract classes (TASK-661), docstrings on concrete components (TASK-662), registry (TASK-663)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/abstract.py` | CREATE | AbstractMulti base class |
| `tests/test_abstract_multi.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from abc import ABC, abstractmethod  # stdlib
import inspect  # stdlib — for __init__ source introspection
import typing  # stdlib — for get_type_hints
import logging  # stdlib
import pandas as pd  # existing dep
from querysource.exceptions import QueryException  # verified: used in handlers/multi.py:8
```

### Existing Signatures to Use
```python
# querysource/queries/multi/transformations/abstract.py — REFERENCE pattern to extract
class AbstractTransform:  # line 12
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 13
        self._backend = 'pandas'  # line 14
        self.data = data  # line 15
        self.logger = logging.getLogger(f'QS.Transform.{self.__class__.__name__}')  # line 16
        # kwargs loop: setattr(self, k, v) — lines 17-18
    async def __aenter__(self):  # line 45 — calls await self.start()
    async def __aexit__(self, exc_type, exc_value, traceback):  # line 49 — calls close(), raises QueryException
    async def start(self):  # line 26 — validates data is non-empty
    async def close(self):  # line 56 — no-op
    @abstractmethod
    async def run(self):  # line 59

# querysource/queries/multi/operators/abstract.py — REFERENCE pattern to extract
class AbstractOperator(ABC):  # line 15
    def __init__(self, data: dict, **kwargs) -> None:  # line 20
        self._backend = kwargs.get('backend', 'pandas')  # line 21
        self._pd = mpd or pd  # lines 24/27 — modin/pandas branch
        self.data = data  # line 28
    async def __aenter__(self):  # line 32
    async def __aexit__(self, exc_type, exc_value, traceback):  # line 36
    def _print_info(self, df: pd.DataFrame):  # line 58

# querysource/queries/multi/components/abstract.py — REFERENCE pattern to extract
class AbstractComponent(ABC):  # line 15
    def __init__(self, data: dict, **kwargs) -> None:  # line 20
        self.data = data  # line 21
    def _print_info(self, df: pd.DataFrame):  # line 51
```

### Does NOT Exist
- ~~`querysource/queries/multi/abstract.py`~~ — this is the file YOU create
- ~~`AbstractMulti`~~ — doesn't exist yet; you create it
- ~~`get_schema()` / `get_description()` / `get_attributes()`~~ — no introspection classmethods exist on any class
- ~~`AbstractTransform(ABC)`~~ — AbstractTransform does NOT inherit ABC; it's a plain class
- ~~`AbstractOperator.logger`~~ — AbstractOperator does NOT have a `logger` attribute

---

## Implementation Notes

### Pattern to Follow

The common init pattern across all three existing abstract classes:
```python
def __init__(self, data, **kwargs) -> None:
    self.data = data
    for k, v in kwargs.items():
        setattr(self, k, v)
```

The `_backend` and `logger` attributes are subclass-specific (Transform has logger, Operator has modin). They stay in the subclass `__init__`.

### get_attributes() Strategy

```python
@classmethod
def get_attributes(cls) -> list[dict]:
    # 1. Class-level type hints via typing.get_type_hints(cls)
    # 2. Inspect __init__ source for kwargs.pop('name', default) / kwargs.get('name', default)
    # 3. Merge, dedup, type fallback to "Any"
    # Return: [{"name": str, "type": str, "default": Any, "required": bool}]
```

### get_schema() Strategy

```python
@classmethod
def get_schema(cls) -> dict:
    attrs = cls.get_attributes()
    # Build JSON Schema draft-2020-12 from attrs
    json_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "title": cls.__name__,
        "properties": {a["name"]: _type_to_json_schema(a["type"]) for a in attrs},
        "required": [a["name"] for a in attrs if a["required"]],
    }
    return {"json_schema": json_schema, "attributes": attrs}
```

### Key Constraints
- `AbstractMulti.__init__` should accept `data` with a flexible type (`Union[dict, pd.DataFrame]`) since transforms accept both but operators only accept dict — the subclass can narrow the type
- The `__aexit__` must raise `QueryException` on error (matches existing behavior)
- `_print_info` should match the Operator/Component version (not the Transform `colum_info` typo)

---

## Acceptance Criteria

- [ ] `AbstractMulti` class exists at `querysource/queries/multi/abstract.py`
- [ ] `AbstractMulti` inherits from `ABC`
- [ ] `__init__` accepts `data` and `**kwargs`, sets attrs via `setattr`
- [ ] `__aenter__` / `__aexit__` implement async context manager calling `start()`/`close()`
- [ ] `run()` is `@abstractmethod`
- [ ] `get_attributes()` returns list of attribute dicts with type fallback to `Any`
- [ ] `get_schema()` returns `{"json_schema": {...}, "attributes": [...]}`
- [ ] `json_schema` follows JSON Schema draft-2020-12 format
- [ ] `get_description()` extracts name, description, usage, category from docstring
- [ ] `_print_info(df)` debug helper is included
- [ ] Unit tests pass: `pytest tests/test_abstract_multi.py -v`
- [ ] Import works: `from querysource.queries.multi.abstract import AbstractMulti`

---

## Test Specification

```python
# tests/test_abstract_multi.py
import pytest
from querysource.queries.multi.abstract import AbstractMulti


class ConcreteStep(AbstractMulti):
    """A test step.

    Usage: Used for testing the AbstractMulti base class.
    """
    _category = "TestCategory"
    test_attr: str = "default"

    def __init__(self, data, **kwargs):
        super().__init__(data, **kwargs)

    async def start(self):
        pass

    async def run(self):
        return self.data


class TestAbstractMultiInit:
    def test_sets_data(self):
        step = ConcreteStep(data={"key": "value"})
        assert step.data == {"key": "value"}

    def test_sets_kwargs_as_attrs(self):
        step = ConcreteStep(data={}, custom="val")
        assert step.custom == "val"


class TestAbstractMultiContextManager:
    @pytest.mark.asyncio
    async def test_aenter_aexit(self):
        step = ConcreteStep(data={})
        async with step as s:
            assert s is step


class TestGetAttributes:
    def test_returns_typed_list(self):
        attrs = ConcreteStep.get_attributes()
        names = [a["name"] for a in attrs]
        assert "test_attr" in names

    def test_fallback_any_for_untyped(self):
        attrs = ConcreteStep.get_attributes()
        for a in attrs:
            assert "type" in a


class TestGetSchema:
    def test_json_schema_format(self):
        schema = ConcreteStep.get_schema()
        assert "json_schema" in schema
        assert schema["json_schema"]["$schema"] == "https://json-schema.org/draft/2020-12/schema"

    def test_simplified_attributes(self):
        schema = ConcreteStep.get_schema()
        assert "attributes" in schema
        assert isinstance(schema["attributes"], list)


class TestGetDescription:
    def test_extracts_name(self):
        desc = ConcreteStep.get_description()
        assert desc["name"] == "ConcreteStep"

    def test_extracts_category(self):
        desc = ConcreteStep.get_description()
        assert desc["category"] == "TestCategory"
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-documentation.spec.md` for full context
2. **Check dependencies** — this task has no dependencies
3. **Verify the Codebase Contract** — confirm the existing abstract classes still have the listed signatures
4. **Implement** `querysource/queries/multi/abstract.py`
5. **Write tests** in `tests/test_abstract_multi.py`
6. **Run tests**: `source .venv/bin/activate && pytest tests/test_abstract_multi.py -v`
7. **Move this file** to `sdd/tasks/completed/TASK-660-abstract-multi-base.md`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
