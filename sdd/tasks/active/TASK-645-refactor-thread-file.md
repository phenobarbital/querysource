# TASK-645: Refactor ThreadFile to Inherit ThreadSource

**Feature**: FEAT-093 — MultiQuery New Sources
**Spec**: `sdd/specs/multiquery-new-sources.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-644
**Assigned-to**: unassigned

---

## Context

Implements Spec Module 2: Refactor ThreadFile. After the ThreadSource base class
is created (TASK-644), this task refactors the existing `ThreadFile` to inherit
from it. This validates the base class design with a real component and ensures
backward compatibility with existing MultiQuery file-loading behavior.

---

## Scope

- Refactor `ThreadFile` in `querysource/queries/multi/sources/file.py` to inherit
  from `ThreadSource` instead of `threading.Thread`.
- Move the file parsing logic from `run()` into an async `fetch()` method.
- Keep `_get_file_content()` as a helper (it can remain sync since it does only local I/O).
- Preserve the exact same constructor signature and external behavior: `ThreadFile(name, file_options, request, queue)`.
- Preserve the `excel_based` module-level tuple.
- Ensure existing tests still pass.

**NOT in scope**: Refactoring ThreadQuery (TASK-646), new source implementations.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/file.py` | MODIFY | Inherit from ThreadSource, move logic to fetch() |
| `tests/test_thread_file_refactor.py` | CREATE | Backward compatibility tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Existing imports in file.py (lines 1-8):
import asyncio                    # verified: file.py:1
import threading                  # verified: file.py:2
from aiohttp import web           # verified: file.py:3
from pathlib import Path           # verified: file.py:4
import zipfile                     # verified: file.py:5
import gzip                        # verified: file.py:6
from io import BytesIO             # verified: file.py:7
import pandas as pd                # verified: file.py:8

# New import needed:
from .base import ThreadSource     # created by TASK-644
```

### Existing Signatures to Use
```python
# querysource/queries/multi/sources/file.py:19-100
class ThreadFile(threading.Thread):
    def __init__(self, name: str, file_options: dict, request: web.Request, queue: asyncio.Queue):
        super().__init__()                          # line 22
        self._loop = asyncio.new_event_loop()       # line 23
        self._queue = queue                          # line 24
        self.exc = None                              # line 25
        self._name = name                            # line 26
        self.file_path = file_options.pop('path')    # line 27
        self._mime = file_options.pop('mime')         # line 30
        self._params: dict = file_options             # line 31

    def _get_file_content(self):                     # line 33
        # handles .zip, .gz, plain files → returns Path or BytesIO

    def run(self):                                   # line 54
        # creates event loop, reads file, puts DataFrame in queue

# Module-level constant:
excel_based = (...)                                  # lines 10-16

# ThreadSource base (created by TASK-644):
# querysource/queries/multi/sources/base.py
class ThreadSource(threading.Thread, ABC):
    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue): ...
    def resolve_credential(self, key: str, value: str) -> str: ...
    async def fetch(self) -> pd.DataFrame: ...  # abstract
    def run(self) -> None: ...  # creates loop, calls fetch(), puts in queue
```

### Does NOT Exist
- ~~`ThreadFile.fetch()`~~ — does not exist yet; this task adds it
- ~~`ThreadSource.parse_file()`~~ — no such method on the base class

---

## Implementation Notes

### Pattern to Follow
```python
from .base import ThreadSource

class ThreadFile(ThreadSource):
    def __init__(self, name: str, file_options: dict, request: web.Request, queue: asyncio.Queue):
        # Extract file-specific options BEFORE passing to super
        self.file_path = file_options.pop('path')
        if isinstance(self.file_path, str):
            self.file_path = Path(self.file_path).resolve()
        self._mime = file_options.pop('mime')
        self._params: dict = file_options
        super().__init__(name, file_options, request, queue)

    def _get_file_content(self):
        # Keep existing logic unchanged
        ...

    async def fetch(self) -> pd.DataFrame:
        # Move the DataFrame creation logic from run() here
        file_content = self._get_file_content()
        if self._mime in excel_based:
            # ... existing excel logic ...
            df = pd.read_excel(file_content, ...)
        elif self._mime == 'text/csv':
            df = pd.read_csv(file_content, ...)
        df.infer_objects()
        return df
```

### Key Constraints
- The constructor signature `(name, file_options, request, queue)` must NOT change — `MultiQS` calls it with those exact args.
- `file_options.pop('path')` and `file_options.pop('mime')` must happen before `super().__init__()` since they mutate the dict.
- `run()` is now inherited from `ThreadSource` — do NOT override it.

### References in Codebase
- `querysource/queries/multi/sources/file.py` — current implementation to refactor
- `querysource/queries/multi/__init__.py:158-164` — how MultiQS creates ThreadFile instances

---

## Acceptance Criteria

- [ ] `ThreadFile` inherits from `ThreadSource` (not `threading.Thread` directly)
- [ ] `ThreadFile.fetch()` returns a pandas DataFrame
- [ ] `ThreadFile.run()` is inherited from `ThreadSource` (not overridden)
- [ ] Constructor signature `(name, file_options, request, queue)` is preserved
- [ ] Existing MultiQuery file-loading behavior is unchanged
- [ ] Tests pass: `pytest tests/test_thread_file_refactor.py -v`
- [ ] No linting errors: `ruff check querysource/queries/multi/sources/file.py`

---

## Test Specification

```python
# tests/test_thread_file_refactor.py
import asyncio
import pytest
import pandas as pd
from pathlib import Path
from io import BytesIO
from querysource.queries.multi.sources.file import ThreadFile
from querysource.queries.multi.sources.base import ThreadSource


class TestThreadFileRefactor:
    def test_inherits_thread_source(self):
        assert issubclass(ThreadFile, ThreadSource)

    def test_has_fetch_method(self):
        assert hasattr(ThreadFile, 'fetch')

    def test_csv_file_produces_dataframe(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("col1,col2\n1,a\n2,b")
        queue = asyncio.Queue()
        t = ThreadFile("test_csv", {"path": str(csv_file), "mime": "text/csv"}, None, queue)
        t.start()
        t.join()
        assert t.exc is None
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(queue.get())
        loop.close()
        assert "test_csv" in result
        assert isinstance(result["test_csv"], pd.DataFrame)
        assert list(result["test_csv"].columns) == ["col1", "col2"]
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-new-sources.spec.md` for full context
2. **Check dependencies** — verify TASK-644 is completed
3. **Verify the Codebase Contract** — confirm ThreadFile's current signature, ThreadSource exists
4. **Update status** in `sdd/tasks/index/multiquery-new-sources.json` → `"in-progress"`
5. **Implement** following the scope and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-645-refactor-thread-file.md`
8. **Update index** → `"done"`
9. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
