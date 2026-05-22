# TASK-670: Migrate FEAT-094 Destinations to New Folder + Back-Compat Shims

**Feature**: FEAT-097 — New Destination Folder for MultiQuery
**Spec**: `sdd/specs/new-destination-multiquery.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-669
**Assigned-to**: unassigned

---

## Context

Move the four FEAT-094 destination classes (`ToSharepoint`, `ToS3`, `TableDestination`, `DWHDestination`) from `querysource/outputs/destinations/` to `querysource/queries/multi/destinations/`. Convert the four files in the old location into one-line back-compat shims so every existing import path continues to work for tests and Flowtask. `TableOutputAdapter` and `AbstractDestination` are NOT moved — they stay in `outputs/destinations/` (Flowtask-shared). Implements spec §3 Modules 5-9.

---

## Scope

For each of the four destination modules (`sharepoint`, `s3`, `table`, `dwh`):

1. Move the full file body to `querysource/queries/multi/destinations/<name>.py`.
2. Replace the import `from .abstract import AbstractDestination` with `from . import AbstractDestination` (resolves to the shim re-export added in TASK-669).
3. Replace the old file at `querysource/outputs/destinations/<name>.py` with a one-line back-compat shim re-exporting every name previously imported by tests/Flowtask.

For `table.py` the shim must re-export `TableDestination`, `DRIVER_MAP`, `VALID_METHODS`, and `_EXTERNAL_DRIVERS` (tests use `DRIVER_MAP`).
For `dwh.py` the shim must re-export `DWHDestination` and `_clean_dynamo_record` (used in tests).

Do NOT modify class bodies (other than the import line). Tests should pass unmodified.

**NOT in scope**:
- Touching `outputs/destinations/__init__.py` aggregator / `DESTINATION_REGISTRY` (TASK-671 handles that — it depends on this task to know where to import from).
- Touching `ComponentRegistry` (TASK-672).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/destinations/sharepoint.py` | CREATE | Moved file body; import via package shim |
| `querysource/queries/multi/destinations/s3.py` | CREATE | Moved file body; import via package shim |
| `querysource/queries/multi/destinations/table.py` | CREATE | Moved file body; import via package shim |
| `querysource/queries/multi/destinations/dwh.py` | CREATE | Moved file body; import via package shim |
| `querysource/outputs/destinations/sharepoint.py` | OVERWRITE | One-line shim re-exporting `ToSharepoint` |
| `querysource/outputs/destinations/s3.py` | OVERWRITE | One-line shim re-exporting `ToS3` |
| `querysource/outputs/destinations/table.py` | OVERWRITE | Shim re-exporting `TableDestination`, `DRIVER_MAP`, `VALID_METHODS`, `_EXTERNAL_DRIVERS` |
| `querysource/outputs/destinations/dwh.py` | OVERWRITE | Shim re-exporting `DWHDestination`, `_clean_dynamo_record` |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports (current bodies — these will be moved)
```python
# querysource/outputs/destinations/sharepoint.py:14-20
import io
import asyncio
from pathlib import PurePosixPath
from typing import Union
import pandas as pd
from querysource.exceptions import OutputError
from .abstract import AbstractDestination  # ← becomes: from . import AbstractDestination

# querysource/outputs/destinations/s3.py:32-38
import gzip
import io
from pathlib import PurePosixPath
from typing import Union
import pandas as pd
from querysource.exceptions import OutputError
from .abstract import AbstractDestination  # ← becomes: from . import AbstractDestination

# querysource/outputs/destinations/table.py:27-31
import asyncio
from typing import List, Optional, Union
import pandas as pd
from querysource.exceptions import DataNotFound, DriverError, OutputError
from .abstract import AbstractDestination  # ← becomes: from . import AbstractDestination

# querysource/outputs/destinations/dwh.py:33-38
from __future__ import annotations
from typing import Any, Dict, List, Optional, Union
import pandas as pd
from querysource.exceptions import DriverError, OutputError
from .abstract import AbstractDestination  # ← becomes: from . import AbstractDestination
```

### Existing Public Symbols (must be re-exported by shims)
```python
# querysource/outputs/destinations/sharepoint.py
class ToSharepoint(AbstractDestination): ...                  # line 28

# querysource/outputs/destinations/s3.py
class ToS3(AbstractDestination): ...                          # line 73

# querysource/outputs/destinations/table.py
DRIVER_MAP: dict[str, str] = { ... }                          # line 38
VALID_METHODS = frozenset({"append", "upsert", "truncate"})    # line 48
_EXTERNAL_DRIVERS = frozenset({"bigquery"})                   # line 51
class TableDestination(AbstractDestination): ...              # line 54

# querysource/outputs/destinations/dwh.py
def _clean_dynamo_record(record: dict) -> dict: ...           # (verify line in file before moving)
class DWHDestination(AbstractDestination): ...                # line 52
```

### Existing import-site evidence (must keep working)
```python
# tests/test_destination_sharepoint.py:11
from querysource.outputs.destinations.sharepoint import ToSharepoint
# tests/test_destination_s3.py:11
from querysource.outputs.destinations.s3 import ToS3
# tests/test_destination_table.py:10
from querysource.outputs.destinations.table import TableDestination, DRIVER_MAP
# tests/test_destination_dwh.py:10
from querysource.outputs.destinations.dwh import DWHDestination, _clean_dynamo_record
# tests/test_destination_integration.py:21-24 — imports all four
```

### Engine imports inside table.py (do NOT touch — keep as-is in the moved file)
```python
# querysource/outputs/destinations/table.py:108-115 — these absolute imports stay verbatim
from querysource.outputs.tables.TableOutput.postgres import PgOutput
from querysource.outputs.tables.TableOutput.mysql import MysqlOutput
from querysource.outputs.tables.TableOutput.bigquery import BigQueryOutput
```

### Does NOT Exist
- ~~`AbstractDestination` defined inside `queries/multi/destinations/`~~ — it lives in `outputs/destinations/abstract.py`; the new folder re-exports via the package `__init__` (TASK-669).
- ~~`querysource.queries.multi.destinations.abstract`~~ — module does not exist. Import via `from . import AbstractDestination`, NOT `from .abstract import ...`.
- ~~A new `get_destination()` in the moved folder~~ — the function lives in `outputs/destinations/__init__.py:95`. Do not duplicate.

---

## Implementation Notes

### Pattern to Follow — moved file
```python
# querysource/queries/multi/destinations/sharepoint.py
"""ToSharepoint Destination. (... full docstring unchanged ...)"""
import io
import asyncio
from pathlib import PurePosixPath
from typing import Union
import pandas as pd
from querysource.exceptions import OutputError
from . import AbstractDestination   # ← the only line that changes


_SMALL_FILE_THRESHOLD = 4 * 1024 * 1024
_CHUNK_SIZE = 10 * 1024 * 1024


class ToSharepoint(AbstractDestination):
    # ... body unchanged ...
```

### Pattern to Follow — back-compat shim
```python
# querysource/outputs/destinations/sharepoint.py
"""Backward-compatibility shim.

The real class now lives at querysource.queries.multi.destinations.sharepoint.
This shim keeps the old import path working for tests and Flowtask.
"""
from querysource.queries.multi.destinations.sharepoint import ToSharepoint  # noqa: F401

__all__ = ("ToSharepoint",)
```

```python
# querysource/outputs/destinations/table.py — re-export helpers too
"""Backward-compatibility shim for TableDestination."""
from querysource.queries.multi.destinations.table import (  # noqa: F401
    TableDestination,
    DRIVER_MAP,
    VALID_METHODS,
    _EXTERNAL_DRIVERS,
)

__all__ = ("TableDestination", "DRIVER_MAP", "VALID_METHODS", "_EXTERNAL_DRIVERS")
```

```python
# querysource/outputs/destinations/dwh.py — re-export helper too
"""Backward-compatibility shim for DWHDestination."""
from querysource.queries.multi.destinations.dwh import (  # noqa: F401
    DWHDestination,
    _clean_dynamo_record,
)

__all__ = ("DWHDestination", "_clean_dynamo_record")
```

### Key Constraints

- After this task, the `DESTINATION_REGISTRY` populated in `outputs/destinations/__init__.py:62-91` may temporarily import via either the shim path or the new path. Either works — TASK-671 will normalize. As long as the registry's class references remain the same `type` objects, tests pass.
- Identity test: `from querysource.outputs.destinations.sharepoint import ToSharepoint as A; from querysource.queries.multi.destinations.sharepoint import ToSharepoint as B; assert A is B`.
- The `queries/multi/destinations/__init__.py` scan logic from TASK-669 will pick up the four files automatically once they land. Verify `DESTINATION_REGISTRY` after the move contains the four classes.
- Preserve `_clean_dynamo_record` even though it's private — `tests/test_destination_dwh.py:10` imports it.

### References in Codebase
- All existing test files for the four destinations are the canonical regression set.
- `outputs/destinations/abstract.py:14` — `_NAVCONFIG_PATTERN` stays in the original location, unchanged.

---

## Acceptance Criteria

- [ ] All four files now exist under `querysource/queries/multi/destinations/`:
  - `sharepoint.py` contains `ToSharepoint(AbstractDestination)`
  - `s3.py` contains `ToS3(AbstractDestination)`
  - `table.py` contains `TableDestination`, `DRIVER_MAP`, `VALID_METHODS`, `_EXTERNAL_DRIVERS`
  - `dwh.py` contains `DWHDestination`, `_clean_dynamo_record`
- [ ] All four files under `querysource/outputs/destinations/{sharepoint,s3,table,dwh}.py` are one-block shims that re-export the symbols above via `from querysource.queries.multi.destinations.X import ...`.
- [ ] Identity invariants:
  - `from querysource.outputs.destinations.sharepoint import ToSharepoint` IS `from querysource.queries.multi.destinations.sharepoint import ToSharepoint`.
  - same for `s3.ToS3`, `table.TableDestination`, `dwh.DWHDestination`.
- [ ] The local `DESTINATION_REGISTRY` exposed by `querysource.queries.multi.destinations.__init__` now contains exactly the four keys: `ToSharepoint`, `ToS3`, `TableDestination`, `DWHDestination`.
- [ ] All existing tests pass without modification:
  - `pytest tests/test_destination_sharepoint.py -v`
  - `pytest tests/test_destination_s3.py -v`
  - `pytest tests/test_destination_table.py -v`
  - `pytest tests/test_destination_dwh.py -v`
  - `pytest tests/test_destination_base.py -v`
  - `pytest tests/test_destination_integration.py -v`
- [ ] `ruff check querysource/queries/multi/destinations/ querysource/outputs/destinations/` — no errors.

---

## Test Specification

Add this small identity regression test to `tests/test_multi_destinations_subpackage.py`:

```python
def test_old_and_new_paths_resolve_to_same_classes():
    """Backward-compat shims must not duplicate class definitions."""
    from querysource.outputs.destinations.sharepoint import ToSharepoint as A_old
    from querysource.queries.multi.destinations.sharepoint import ToSharepoint as A_new
    assert A_old is A_new

    from querysource.outputs.destinations.s3 import ToS3 as B_old
    from querysource.queries.multi.destinations.s3 import ToS3 as B_new
    assert B_old is B_new

    from querysource.outputs.destinations.table import TableDestination, DRIVER_MAP
    from querysource.queries.multi.destinations.table import (
        TableDestination as TD_new,
        DRIVER_MAP as DM_new,
    )
    assert TableDestination is TD_new
    assert DRIVER_MAP is DM_new

    from querysource.outputs.destinations.dwh import DWHDestination, _clean_dynamo_record
    from querysource.queries.multi.destinations.dwh import (
        DWHDestination as DW_new,
        _clean_dynamo_record as ccdr_new,
    )
    assert DWHDestination is DW_new
    assert _clean_dynamo_record is ccdr_new


def test_local_registry_populated_after_migration():
    from querysource.queries.multi.destinations import DESTINATION_REGISTRY
    assert set(DESTINATION_REGISTRY) >= {
        "ToSharepoint", "ToS3", "TableDestination", "DWHDestination"
    }
```

---

## Agent Instructions

1. **Read the spec** at `sdd/specs/new-destination-multiquery.spec.md` (§3 Modules 5-9, §7 Patterns to Follow).
2. **Check dependencies** — TASK-669 in `sdd/tasks/completed/`.
3. **Verify the Codebase Contract** — confirm the four current files are still byte-stable: `wc -l querysource/outputs/destinations/{sharepoint,s3,table,dwh}.py`. Re-read any that look different.
4. **Update status** in `sdd/tasks/index/new-destination-multiquery.json` → `"in-progress"`.
5. **Implement** — move one destination at a time (sharepoint → s3 → table → dwh). After each move:
   - Write the moved file.
   - Overwrite the old file with a shim.
   - Run the relevant test file (e.g. `pytest tests/test_destination_sharepoint.py -v`).
   - Only proceed once the test passes.
6. **Verify** — full test pass on `tests/test_destination_*.py`. Also `python -c "from querysource.queries.multi.destinations import DESTINATION_REGISTRY; print(sorted(DESTINATION_REGISTRY))"` lists all four.
7. **Move** this file to `sdd/tasks/completed/TASK-670-migrate-destinations-with-shims.md`.
8. **Update index** → `"done"`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
