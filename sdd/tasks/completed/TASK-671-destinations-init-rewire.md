# TASK-671: Rewire outputs/destinations/__init__.py to Use Migrated Classes

**Feature**: FEAT-097 — New Destination Folder for MultiQuery
**Spec**: `sdd/specs/new-destination-multiquery.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-670
**Assigned-to**: unassigned

---

## Context

`outputs/destinations/__init__.py` currently registers each destination in `DESTINATION_REGISTRY` via `from .sharepoint import ToSharepoint` etc. After TASK-670 those local files are shims that themselves import from `queries/multi/destinations/`. The aggregator file still works as-is (the shim re-exports preserve identity), but the spec calls for explicit re-routing so the layered design is obvious to future readers. Implements spec §3 Module 10.

Also: `TableOutputAdapter` continues to live here and remains the registry entry for `tableOutput` / `TableOutput` (Flowtask-shared).

---

## Scope

- Update `querysource/outputs/destinations/__init__.py` so each `try / except ImportError` block imports from the canonical new path (`from querysource.queries.multi.destinations.sharepoint import ToSharepoint` etc.) instead of the local shim file (`from .sharepoint import ToSharepoint`). This makes the layering explicit.
- Keep `TableOutputAdapter` definition and its two registry entries (`tableOutput`, `TableOutput`) untouched.
- Keep `get_destination()` and `__all__` unchanged.
- Confirm `DESTINATION_REGISTRY` still has exactly six keys: `tableOutput`, `TableOutput`, `ToSharepoint`, `ToS3`, `Table`, `DWH`.

**NOT in scope**:
- Touching the shim files (TASK-670 owns them).
- Removing `TableOutputAdapter` (out of FEAT-097 scope per §1 Non-Goals).
- Touching `ComponentRegistry` (TASK-672).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/outputs/destinations/__init__.py` | MODIFY | Re-route concrete-class imports to `queries/multi/destinations/`; keep `TableOutputAdapter` and registry keys |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Current file (querysource/outputs/destinations/__init__.py) imports — to be re-routed
# verified: line 63 — from .sharepoint import ToSharepoint
# verified: line 71 — from .s3 import ToS3
# verified: line 79 — from .table import TableDestination
# verified: line 87 — from .dwh import DWHDestination

# After TASK-670, the canonical home is:
from querysource.queries.multi.destinations.sharepoint import ToSharepoint
from querysource.queries.multi.destinations.s3 import ToS3
from querysource.queries.multi.destinations.table import TableDestination
from querysource.queries.multi.destinations.dwh import DWHDestination

# Unchanged imports — keep as-is
# verified: line 17 — from ..tables import TableOutput
# verified: line 18 — from ...exceptions import OutputError
# verified: line 19 — from .abstract import AbstractDestination
```

### Existing Signatures to Use
```python
# querysource/outputs/destinations/__init__.py
class TableOutputAdapter(AbstractDestination):                 # line 24 — KEEP UNCHANGED
    def __init__(self, data, **kwargs) -> None: ...            # line 33
    async def run(self): ...                                   # line 39
    async def close(self): ...                                 # line 45

DESTINATION_REGISTRY: dict[str, type[AbstractDestination]] = { # line 55
    "tableOutput": TableOutputAdapter,                         # line 56
    "TableOutput": TableOutputAdapter,                         # line 57
    # registered via try/except below — see lines 62-91
    # "ToSharepoint": ToSharepoint,
    # "ToS3": ToS3,
    # "Table": TableDestination,
    # "DWH": DWHDestination,
}

def get_destination(step_name: str) -> type[AbstractDestination]:  # line 95
```

### Does NOT Exist
- ~~`outputs/destinations/__init__.py` exporting a new `DESTINATION_REGISTRY_V2` or similar~~ — only one registry exists at this layer.
- ~~`outputs/destinations` re-exporting the moved-class file objects~~ — the shims handle that.
- ~~A method to "remove" the legacy keys~~ — backward compat requires keeping all six.

---

## Implementation Notes

### Pattern to Follow

The diff is small. Each `from .X import Y` inside a try/except becomes `from querysource.queries.multi.destinations.X import Y`, then the registry assignment line is unchanged.

```python
# After change
try:
    from querysource.queries.multi.destinations.sharepoint import ToSharepoint
    DESTINATION_REGISTRY["ToSharepoint"] = ToSharepoint
except ImportError:
    _pkg_logger.debug(
        "ToSharepoint destination not available: msgraph-sdk or azure-identity not installed"
    )

try:
    from querysource.queries.multi.destinations.s3 import ToS3
    DESTINATION_REGISTRY["ToS3"] = ToS3
except ImportError:
    _pkg_logger.debug("ToS3 destination not available: aioboto3 not installed")

try:
    from querysource.queries.multi.destinations.table import TableDestination
    DESTINATION_REGISTRY["Table"] = TableDestination
except ImportError:
    _pkg_logger.debug("Table destination not available")

try:
    from querysource.queries.multi.destinations.dwh import DWHDestination
    DESTINATION_REGISTRY["DWH"] = DWHDestination
except ImportError:
    _pkg_logger.debug("DWH destination not available")
```

### Key Constraints

- The registry keys (`"ToSharepoint"`, `"ToS3"`, `"Table"`, `"DWH"`, `"tableOutput"`, `"TableOutput"`) MUST remain identical. They appear in user YAML — changing them is a breaking change.
- Module-level `import` order matters for the optional-dependency guards: each try/except wraps a single import + dict insert so a missing optional dep skips just that one entry.
- Do NOT delete the `__all__` tuple — Flowtask imports from this package and may use `from querysource.outputs.destinations import *`.
- `get_destination()` body is unchanged.

### References in Codebase
- `tests/test_destination_base.py:156,162` and `test_destination_integration.py:108` — both import `TableOutputAdapter` directly from this file. Must keep working.
- `querysource/queries/multi/__init__.py:18` — `from ...outputs.destinations import get_destination`. Must keep working.

---

## Acceptance Criteria

- [ ] `outputs/destinations/__init__.py` imports `ToSharepoint`, `ToS3`, `TableDestination`, `DWHDestination` from `querysource.queries.multi.destinations.*` instead of relative `.X import ...`.
- [ ] `TableOutputAdapter` class definition and its two registry entries (`tableOutput`, `TableOutput`) are unchanged.
- [ ] `__all__` is unchanged.
- [ ] `from querysource.outputs.destinations import DESTINATION_REGISTRY` returns a dict with EXACTLY these six keys (assuming all optional deps are installed): `{"tableOutput", "TableOutput", "ToSharepoint", "ToS3", "Table", "DWH"}`.
- [ ] `get_destination("Table")` returns the same class as `querysource.queries.multi.destinations.table.TableDestination` (identity check).
- [ ] All existing tests pass:
  - `pytest tests/test_destination_base.py tests/test_destination_integration.py -v`
  - `pytest tests/test_destination_sharepoint.py tests/test_destination_s3.py tests/test_destination_table.py tests/test_destination_dwh.py -v`
- [ ] `ruff check querysource/outputs/destinations/__init__.py` — no errors.

---

## Test Specification

Add to `tests/test_multi_destinations_subpackage.py`:

```python
def test_legacy_registry_still_has_six_keys():
    from querysource.outputs.destinations import DESTINATION_REGISTRY
    expected = {"tableOutput", "TableOutput", "ToSharepoint", "ToS3", "Table", "DWH"}
    assert expected.issubset(set(DESTINATION_REGISTRY))


def test_legacy_registry_points_to_migrated_classes():
    from querysource.outputs.destinations import DESTINATION_REGISTRY
    from querysource.queries.multi.destinations.sharepoint import ToSharepoint
    from querysource.queries.multi.destinations.s3 import ToS3
    from querysource.queries.multi.destinations.table import TableDestination
    from querysource.queries.multi.destinations.dwh import DWHDestination
    assert DESTINATION_REGISTRY["ToSharepoint"] is ToSharepoint
    assert DESTINATION_REGISTRY["ToS3"] is ToS3
    assert DESTINATION_REGISTRY["Table"] is TableDestination
    assert DESTINATION_REGISTRY["DWH"] is DWHDestination


def test_table_output_adapter_still_available():
    from querysource.outputs.destinations import TableOutputAdapter
    from querysource.outputs.destinations.abstract import AbstractDestination
    assert issubclass(TableOutputAdapter, AbstractDestination)
```

---

## Agent Instructions

1. **Read the spec** at `sdd/specs/new-destination-multiquery.spec.md` (§3 Module 10).
2. **Check dependencies** — TASK-670 in `sdd/tasks/completed/`.
3. **Verify the Codebase Contract** — confirm the four shim files exist and re-export the four classes (proof TASK-670 landed).
4. **Update status** in `sdd/tasks/index/new-destination-multiquery.json` → `"in-progress"`.
5. **Implement** — replace four import lines in `outputs/destinations/__init__.py`. Nothing else.
6. **Verify** — run the full `tests/test_destination_*.py` and `tests/test_multi_destinations_subpackage.py`.
7. **Move** this file to `sdd/tasks/completed/TASK-671-destinations-init-rewire.md`.
8. **Update index** → `"done"`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any

## Completion Note

Completed by: Claude Sonnet 4.6
Date: 2026-05-22
Notes: Replaced four `from .X import Y` calls with `from querysource.queries.multi.destinations.X import Y`.
Added 3 new tests to test_multi_destinations_subpackage.py.
All 28 tests pass.

Deviations from spec: none
