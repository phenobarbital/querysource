# TASK-668: Refactor AbstractDestination to Inherit SchemaIntrospectable

**Feature**: FEAT-097 — New Destination Folder for MultiQuery
**Spec**: `sdd/specs/new-destination-multiquery.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-667
**Assigned-to**: unassigned

---

## Context

`AbstractDestination` currently has no introspection capabilities. The documentation endpoint (FEAT-095) and the component-registry catalog return empty JSON Schema for every destination because of this. Once TASK-667 has extracted `SchemaIntrospectable`, `AbstractDestination` can inherit it for free. Implements spec §3 Module 3.

---

## Scope

- Modify `querysource/outputs/destinations/abstract.py` so `AbstractDestination(SchemaIntrospectable, ABC)`.
- Add `_category: str = "Destinations"` class attribute (overrides the mixin's `"Components"` default).
- Leave `__init__`, `resolve_credentials`, abstract `run`, `close`, and the navconfig pattern regex unchanged.
- Add unit tests verifying that a concrete `AbstractDestination` subclass returns a populated JSON Schema and `category == "Destinations"`.

**NOT in scope**:
- Moving any concrete destination class (TASK-670).
- Creating the `queries/multi/destinations/` folder (TASK-669).
- Touching `ComponentRegistry._classify` (TASK-672).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/outputs/destinations/abstract.py` | MODIFY | Inherit from `SchemaIntrospectable`; add `_category` |
| `tests/test_destination_base.py` | MODIFY | Add tests for `_category` and `get_schema` on a subclass |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Will be created by TASK-667 — depend on it being merged first.
from querysource.queries.multi._introspect import SchemaIntrospectable

# verified: querysource/outputs/destinations/abstract.py:6
import re
# verified: querysource/outputs/destinations/abstract.py:7
from abc import ABC, abstractmethod
# verified: querysource/outputs/destinations/abstract.py:8
from typing import Union
# verified: querysource/outputs/destinations/abstract.py:9
import pandas as pd
# verified: querysource/outputs/destinations/abstract.py:10
from navconfig.logging import logging
```

### Existing Signatures to Use
```python
# querysource/outputs/destinations/abstract.py — current state (before this task)
_NAVCONFIG_PATTERN = re.compile(r'^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$')  # line 14

class AbstractDestination(ABC):                                 # line 17
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None: ...  # line 26
        # Sets self.data, self.logger
    def resolve_credentials(self, credentials: dict) -> dict: ...  # line 32
    @abstractmethod
    async def run(self) -> Union[dict, pd.DataFrame]: ...       # line 56
    async def close(self) -> None: ...                          # line 68
```

### Existing Test File (to extend)
```python
# tests/test_destination_base.py
# verified: line 11 — from querysource.outputs.destinations.abstract import AbstractDestination
# verified: line 12 — from querysource.outputs.destinations import DESTINATION_REGISTRY, get_destination
# This file already has fixtures for concrete AbstractDestination subclasses — read it before adding tests.
```

### Does NOT Exist
- ~~`AbstractDestination._category`~~ — added by this task.
- ~~`AbstractDestination.get_schema()`~~ / `get_attributes()` / `get_description()` — added via inheritance.
- ~~`AbstractDestination.json_schema` instance attribute~~ — only the classmethods exist.
- ~~`SchemaIntrospectable` in `querysource.outputs.destinations.*`~~ — it lives in `querysource.queries.multi._introspect`.

---

## Implementation Notes

### Pattern to Follow

```python
# querysource/outputs/destinations/abstract.py
"""AbstractDestination — base class for MultiQuery destinations."""
import re
from abc import ABC, abstractmethod
from typing import Union
import pandas as pd
from navconfig.logging import logging

from querysource.queries.multi._introspect import SchemaIntrospectable


_NAVCONFIG_PATTERN = re.compile(r'^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$')


class AbstractDestination(SchemaIntrospectable, ABC):
    """AbstractDestination.

    Base class for all MultiQuery destination components.
    Subclasses must implement :meth:`run` to write data to their target backend
    and return the original data (pass-through) for pipeline chaining.
    """

    _category: str = "Destinations"

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        # unchanged
        ...

    # resolve_credentials, run, close — unchanged
```

### Key Constraints

- The import path `from querysource.queries.multi._introspect import SchemaIntrospectable` introduces a NEW dependency direction: `outputs/destinations/abstract.py → queries/multi/_introspect.py`. Verify it does NOT cause a circular import by also running `python -c "import querysource.outputs.destinations"` after the change.
- Existing instance attributes (`self.data`, `self.logger`) and method signatures must NOT change. Concrete subclasses (`ToSharepoint`, `ToS3`, `TableDestination`, `DWHDestination`, `TableOutputAdapter`) must continue to instantiate cleanly without code changes.
- `_category = "Destinations"` is a class attribute on `AbstractDestination`, not on `SchemaIntrospectable`. Do NOT modify the mixin's default.

### References in Codebase
- `tests/test_destination_base.py` — extend with new test cases.
- `tests/test_destination_integration.py:20` — uses `AbstractDestination`; should keep passing.

---

## Acceptance Criteria

- [ ] `AbstractDestination` inherits from `(SchemaIntrospectable, ABC)`.
- [ ] `AbstractDestination._category == "Destinations"`.
- [ ] `AbstractDestination.get_schema()` is callable on any concrete subclass (e.g. `ToSharepoint.get_schema()` returns `{"json_schema": {...}, "attributes": [...]}` with at least one attribute — verifies the introspection actually reads `__init__`).
- [ ] `python -c "from querysource.outputs.destinations import DESTINATION_REGISTRY, get_destination, TableOutputAdapter; print(list(DESTINATION_REGISTRY))"` works (no import cycle).
- [ ] All tests pass:
  - `pytest tests/test_destination_base.py -v`
  - `pytest tests/test_destination_integration.py -v`
  - `pytest tests/test_destination_sharepoint.py tests/test_destination_s3.py tests/test_destination_table.py tests/test_destination_dwh.py -v`
- [ ] `ruff check querysource/outputs/destinations/abstract.py` — no errors.

---

## Test Specification

```python
# Append to tests/test_destination_base.py

class TestAbstractDestinationIntrospection:
    def test_category_is_destinations(self):
        from querysource.outputs.destinations.abstract import AbstractDestination
        assert AbstractDestination._category == "Destinations"

    def test_concrete_subclass_get_schema_populated(self):
        """A concrete destination must produce a non-empty JSON Schema."""
        from querysource.outputs.destinations.sharepoint import ToSharepoint
        schema = ToSharepoint.get_schema()
        assert schema["json_schema"]["title"] == "ToSharepoint"
        assert schema["json_schema"]["properties"], (
            "Expected at least one property to be introspected from ToSharepoint.__init__"
        )

    def test_get_description_reports_destinations_category(self):
        from querysource.outputs.destinations.s3 import ToS3
        desc = ToS3.get_description()
        assert desc["category"] == "Destinations"
        assert desc["name"] == "ToS3"
```

---

## Agent Instructions

1. **Read the spec** at `sdd/specs/new-destination-multiquery.spec.md` (§3 Module 3, §6 Codebase Contract).
2. **Check dependencies** — TASK-667 must be in `sdd/tasks/completed/`.
3. **Verify the Codebase Contract** — `python -c "from querysource.queries.multi._introspect import SchemaIntrospectable"` must work before you start.
4. **Update status** in `sdd/tasks/index/new-destination-multiquery.json` → `"in-progress"`.
5. **Implement** — small change to `abstract.py`, append the test class.
6. **Verify** — run the full `tests/test_destination_*.py` suite.
7. **Move** this file to `sdd/tasks/completed/TASK-668-abstractdestination-mixin-refactor.md`.
8. **Update index** → `"done"`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
