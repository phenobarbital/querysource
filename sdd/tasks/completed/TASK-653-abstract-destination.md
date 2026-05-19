# TASK-653: AbstractDestination Base Class

**Feature**: FEAT-094 — MultiQuery New Destinations
**Spec**: `sdd/specs/multiquery-destinations.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

This is the foundation task for FEAT-094. It creates the `AbstractDestination` base class and the destination registry that all other destination components will use. It also creates a `TableOutputAdapter` that wraps the existing `TableOutput` class so it can be registered alongside the new destinations.

Implements spec §2 (Overview, New Public Interfaces), §3 Module 1 and Module 2.

---

## Scope

- Create `querysource/outputs/destinations/` package with `__init__.py`
- Implement `AbstractDestination` ABC in `querysource/outputs/destinations/abstract.py`:
  - Constructor: `__init__(self, data: Union[dict, pd.DataFrame], **kwargs)`
  - `resolve_credentials(credentials: dict) -> dict` — resolves navconfig variable names (ALL_CAPS_SNAKE_CASE patterns) to actual values via `navconfig.config.get()`
  - Abstract method: `async run() -> Union[dict, pd.DataFrame]`
  - `async close() -> None` — optional cleanup hook
  - Logger: `logging.getLogger('QS.Output.<ClassName>')`
- Implement `TableOutputAdapter(AbstractDestination)` that wraps the existing `TableOutput` class to conform to the `AbstractDestination` interface
- Create `DESTINATION_REGISTRY` dict in `__init__.py` mapping step names to classes
- Provide `get_destination(step_name: str) -> type[AbstractDestination]` factory function
- Write unit tests for credential resolution and registry lookup

**NOT in scope**: The four new destination classes (ToSharepoint, ToS3, Table, DWH) — those are separate tasks. Modifying MultiQS or QueryHandler dispatch — that is TASK-659.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/outputs/destinations/__init__.py` | CREATE | Package init, DESTINATION_REGISTRY, get_destination() |
| `querysource/outputs/destinations/abstract.py` | CREATE | AbstractDestination ABC |
| `tests/test_destination_base.py` | CREATE | Unit tests for abstract base and registry |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.outputs.tables import TableOutput  # verified: querysource/outputs/tables/__init__.py:1
from querysource.exceptions import OutputError  # verified: querysource/outputs/tables/TableOutput/table.py:9
from navconfig.logging import logging  # verified: used across all modules
```

### Existing Signatures to Use
```python
# querysource/outputs/tables/TableOutput/table.py:19
class TableOutput:
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 20
        self.data = data  # line 22
        self.flavor: str = kwargs.pop('flavor', 'postgresql')  # line 29
    async def run(self):  # line 118
        # Returns self.data after writing

# navconfig credential resolution pattern (used across codebase):
from navconfig import config
value = config.get('VARIABLE_NAME')  # returns None if not found
```

### Does NOT Exist
- ~~`querysource.outputs.destinations`~~ — this package does NOT exist yet; this task creates it
- ~~`AbstractDestination`~~ — does not exist yet; this task creates it
- ~~`querysource.queries.multi.get_output_module()`~~ — no dynamic output loader exists
- ~~`TableOutput.register()`~~ — TableOutput has no registration mechanism
- ~~`querysource.outputs.tables.TableOutput.abstract.AbstractOutput`~~ — this is the DB engine base class, NOT a destination base class. Do not confuse the two.

---

## Implementation Notes

### Pattern to Follow
```python
# Credential resolution: check if value looks like a navconfig variable
import re

_NAVCONFIG_PATTERN = re.compile(r'^[A-Z][A-Z0-9_]+$')

def resolve_credentials(credentials: dict) -> dict:
    from navconfig import config
    resolved = {}
    for key, value in credentials.items():
        if isinstance(value, str) and _NAVCONFIG_PATTERN.match(value):
            resolved_val = config.get(value)
            resolved[key] = resolved_val if resolved_val is not None else value
        else:
            resolved[key] = value
    return resolved
```

### Key Constraints
- `AbstractDestination.run()` must return the original `self.data` unchanged (pass-through for pipeline chaining)
- `TableOutputAdapter` must delegate entirely to `TableOutput` — do not duplicate its logic
- The registry must register `tableOutput` and `TableOutput` (both cases) for backward compatibility
- `get_destination()` must raise `OutputError` for unknown step names

---

## Acceptance Criteria

- [ ] `querysource/outputs/destinations/` package exists with `__init__.py` and `abstract.py`
- [ ] `AbstractDestination` ABC has constructor, `resolve_credentials()`, abstract `run()`, and `close()`
- [ ] `TableOutputAdapter` wraps `TableOutput` and implements `AbstractDestination` interface
- [ ] `DESTINATION_REGISTRY` maps `tableOutput`, `TableOutput` to `TableOutputAdapter`
- [ ] `get_destination('TableOutput')` returns `TableOutputAdapter`
- [ ] `get_destination('unknown')` raises `OutputError`
- [ ] Credential resolution replaces navconfig variable names with actual values
- [ ] All tests pass: `pytest tests/test_destination_base.py -v`
- [ ] No linting errors: `ruff check querysource/outputs/destinations/`

---

## Test Specification

```python
# tests/test_destination_base.py
import pytest
import pandas as pd
from unittest.mock import patch
from querysource.outputs.destinations.abstract import AbstractDestination
from querysource.outputs.destinations import DESTINATION_REGISTRY, get_destination
from querysource.exceptions import OutputError


class TestCredentialResolution:
    def test_literal_values_passed_through(self):
        """Literal values (not ALL_CAPS) stay unchanged."""
        dest = ConcreteDestination(data=pd.DataFrame())
        result = dest.resolve_credentials({"key": "literal_value", "num": 42})
        assert result == {"key": "literal_value", "num": 42}

    @patch("navconfig.config.get", return_value="resolved_secret")
    def test_navconfig_variables_resolved(self, mock_get):
        """ALL_CAPS_SNAKE_CASE values are resolved via navconfig."""
        dest = ConcreteDestination(data=pd.DataFrame())
        result = dest.resolve_credentials({"client_id": "SHAREPOINT_APP_ID"})
        assert result["client_id"] == "resolved_secret"

    @patch("navconfig.config.get", return_value=None)
    def test_unresolvable_variable_kept_as_is(self, mock_get):
        """If navconfig returns None, keep the original value."""
        dest = ConcreteDestination(data=pd.DataFrame())
        result = dest.resolve_credentials({"key": "UNKNOWN_VAR"})
        assert result["key"] == "UNKNOWN_VAR"


class TestDestinationRegistry:
    def test_table_output_registered(self):
        assert "tableOutput" in DESTINATION_REGISTRY
        assert "TableOutput" in DESTINATION_REGISTRY

    def test_get_destination_known(self):
        cls = get_destination("TableOutput")
        assert cls is not None

    def test_get_destination_unknown(self):
        with pytest.raises(OutputError):
            get_destination("NonExistent")
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-destinations.spec.md` for full context
2. **Check dependencies** — this task has no dependencies
3. **Verify the Codebase Contract** — before writing ANY code:
   - Confirm `from querysource.outputs.tables import TableOutput` works
   - Confirm `from querysource.exceptions import OutputError` works
   - Confirm `from navconfig.logging import logging` works
   - **NEVER** reference an import not in the contract without verifying it exists
4. **Update status** in `sdd/tasks/index/multiquery-destinations.json` → `"in-progress"`
5. **Implement** following the scope, codebase contract, and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-653-abstract-destination.md`
8. **Update index** → `"done"`
9. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any

---

**Completed by**: SDD Worker (Claude)
**Date**: 2026-05-19
**Notes**: Implemented AbstractDestination ABC with credential resolution (navconfig variable pattern detection), TableOutputAdapter wrapper, DESTINATION_REGISTRY dict with tableOutput/TableOutput backward-compatible entries, and get_destination() factory that raises OutputError for unknown names. All 18 unit tests pass.
**Deviations from spec**: none
