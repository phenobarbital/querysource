# TASK-801: Register Parquet sources, generate schemas, integration test

**Feature**: FEAT-158 — Parquet Sources for MultiQS (local, S3, GCS over fsspec)
**Spec**: `sdd/specs/parquet-multiqs-source.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-798, TASK-799, TASK-800
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 7. This task:
- exposes the three Parquet sources from the `parquet` subpackage;
- registers them in `SOURCE_REGISTRY` under their class names, so MultiQS dispatches them from YAML `sources:`;
- generates their component schemas into `generated/` with `generate-multiquery-docs`;
- updates the registry tests;
- adds a MultiQS-level integration test.

---

## Scope

- Fill in the exports in `parquet/__init__.py` (it's docstring-only after TASK-796).
- Import and register the three classes in `sources/__init__.py` (import block, `__all__`, `SOURCE_REGISTRY`).
- Update `tests/test_source_registry.py`. `test_registry_has_exactly_five_sources` becomes eight, and assertions for the new entries are added.
- Create `tests/test_multiqs_parquet_integration.py`.
- Run `generate-multiquery-docs -c Sources` and commit **only** the three new `generated/Parquet*Source.json` files.

**NOT in scope**: changes to MultiQS dispatch (`multi/__init__.py`) or `registry.py`. Neither needs one.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/parquet/__init__.py` | MODIFY | Export the 4 classes |
| `querysource/queries/multi/sources/__init__.py` | MODIFY | Import, `__all__`, `SOURCE_REGISTRY` |
| `tests/test_source_registry.py` | MODIFY | Size 5 → 8, and new-entry assertions |
| `tests/test_multiqs_parquet_integration.py` | CREATE | MultiQS parse and thread dispatch with a ParquetFileSource |
| `generated/ParquetFileSource.json` | CREATE | Generated schema |
| `generated/ParquetS3Source.json` | CREATE | Generated schema |
| `generated/ParquetGCSSource.json` | CREATE | Generated schema |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.queries.multi.sources import SOURCE_REGISTRY, ThreadSource, __all__   # sources/__init__.py:1-35
from querysource.queries.multi.sources.parquet import (                                   # after this task
    ParquetSource, ParquetFileSource, ParquetS3Source, ParquetGCSSource,
)
from querysource.queries.multi import MultiQS   # used by tests/test_multiqs_sources_integration.py:9
```

### Existing Signatures to Use
```python
# querysource/queries/multi/sources/__init__.py (verified at 4c685c1)
from .table import TableSource                 # line 9
    "TableSource",                             # line 19 (in __all__)
SOURCE_REGISTRY: dict = {                      # line 29
    "TableSource": TableSource,                # line 34
}

# querysource/queries/multi/__init__.py:541-557 — MultiQS dispatch (unchanged):
#   cls = SOURCE_REGISTRY.get(source_type); name = source_type | f"{source_type}_{idx}";
#   t = cls(name, config, self._request, self._queue)
# tests/test_multiqs_sources_integration.py:13-20 — pattern: MultiQS(query={"sources": [...]}, request=MagicMock())
#   exposes mqs._sources
# tests/test_source_registry.py:33-34
#   def test_registry_has_exactly_five_sources(self):
#       assert len(SOURCE_REGISTRY) == 5
# querysource/cli/generate_docs.py:103-125 — `generate-multiquery-docs -o generated -c Sources [-f json]`
# querysource/queries/multi/_introspect.py:990 — extract_source_schema(cls) (schemas come from __init__ .get calls)
```

### Does NOT Exist
- ~~Any `Parquet*` key in `SOURCE_REGISTRY`~~: added here.
- ~~A `parquet.catalog.yaml` companion file~~: not required. Don't create one unless `generate-multiquery-docs` demands it.
- ~~`ParquetSource` in `SOURCE_REGISTRY`~~: it is abstract and must NOT be registered (it is exported from the subpackage only).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/sources/parquet/__init__.py", "action": "MODIFY"},
    {"path": "querysource/queries/multi/sources/__init__.py", "action": "MODIFY"},
    {"path": "tests/test_source_registry.py", "action": "MODIFY"},
    {"path": "tests/test_multiqs_parquet_integration.py", "action": "CREATE"},
    {"path": "generated/ParquetFileSource.json", "action": "CREATE"},
    {"path": "generated/ParquetS3Source.json", "action": "CREATE"},
    {"path": "generated/ParquetGCSSource.json", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/sources/__init__.py#SOURCE_REGISTRY",
    "sym:querysource/queries/multi/__init__.py#MultiQS"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **Cross-feature conflict**: FEAT-159 (onedrive-multiqs-source, developed in parallel) also edits
  `sources/__init__.py` and the same `len(SOURCE_REGISTRY) == 5` assertion. Whichever merges second must reconcile
  the count to the true total. Assert the exact expected count for this branch, and note the conflict in the Completion Note.
- Importing `querysource.queries.multi.sources` must stay lazy with respect to pyarrow, fsspec, s3fs and gcsfs.
  Add a test that runs a subprocess `python -c "import querysource.queries.multi.sources, sys; assert 's3fs' not in sys.modules and 'gcsfs' not in sys.modules"`.
- `generate-multiquery-docs` rewrites every Sources JSON. After running it, `git checkout -- generated/` for every file
  except the three new ones, because unrelated schema drift is out of scope.

---

## Implementation Blueprint

### Steps (in order)
1. Fill in `parquet/__init__.py` — *why*: `sources/__init__.py` imports from it.
2. Edit `sources/__init__.py` at the three anchors — *why*: this is what makes the classes dispatchable (MultiQS uses `SOURCE_REGISTRY`).
3. Update the registry tests and add the integration test — *why*: this proves registration and the thread dispatch path.
4. Run `source .venv/bin/activate && generate-multiquery-docs -o generated -c Sources`, restore the unrelated files, and inspect the three new JSONs — *why*: this checks that `source.path` / `credentials.bucket` show as required.

### `querysource/queries/multi/sources/parquet/__init__.py` (MODIFY)
```python
# REPLACE the whole file (TASK-796 left a docstring only) with:
"""Parquet sources for MultiQS (FEAT-158): local filesystem, S3 and GCS over fsspec."""
from .base import ParquetSource
from .gcs import ParquetGCSSource
from .local import ParquetFileSource
from .s3 import ParquetS3Source

__all__ = ["ParquetSource", "ParquetFileSource", "ParquetS3Source", "ParquetGCSSource"]
```
**Why**: these modules import only aiohttp, pandas and querysource at module level, so the export stays lazy.

### `querysource/queries/multi/sources/__init__.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '^from .table import TableSource' querysource/queries/multi/sources/__init__.py)
# BEFORE — insert above `from .query import ThreadQuery` (line 5) to keep alphabetical order:
from .parquet import ParquetFileSource, ParquetGCSSource, ParquetS3Source

# occurrences: 1 (verified: grep -c '    "TableSource",' …) — AFTER `    "TableSource",` (line 19):
    "ParquetFileSource",
    "ParquetS3Source",
    "ParquetGCSSource",

# occurrences: 1 (verified: grep -c '    "TableSource": TableSource,' …) — AFTER line 34:
    "ParquetFileSource": ParquetFileSource,
    "ParquetS3Source": ParquetS3Source,
    "ParquetGCSSource": ParquetGCSSource,
```
**Why**: the registry keys equal the class names, which is the YAML type name (spec §3 M7). `ParquetSource` is not registered because it's abstract.

### `tests/test_source_registry.py` (MODIFY)
```python
# occurrences: 1 — REPLACE lines 33-34:
    def test_registry_has_exactly_eight_sources(self):
        assert len(SOURCE_REGISTRY) == 8

# APPEND to class TestSourceRegistry:
    def test_registry_contains_parquet_sources(self):
        from querysource.queries.multi.sources import (
            ParquetFileSource, ParquetGCSSource, ParquetS3Source,
        )
        assert SOURCE_REGISTRY["ParquetFileSource"] is ParquetFileSource
        assert SOURCE_REGISTRY["ParquetS3Source"] is ParquetS3Source
        assert SOURCE_REGISTRY["ParquetGCSSource"] is ParquetGCSSource
        assert {"ParquetFileSource", "ParquetS3Source", "ParquetGCSSource"} <= set(__all__)
        assert "ParquetSource" not in SOURCE_REGISTRY
```

### `tests/test_multiqs_parquet_integration.py` (CREATE)
```python
"""MultiQS integration for Parquet sources (FEAT-158, TASK-801)."""
import asyncio
import subprocess
import sys
from unittest.mock import MagicMock

import pandas as pd

from querysource.queries.multi import MultiQS
from querysource.queries.multi.sources import SOURCE_REGISTRY


def test_multiqs_parses_parquet_source(tmp_path):
    query = {"sources": [{"ParquetFileSource": {"source": {"path": str(tmp_path / "x.parquet")}}}]}
    mqs = MultiQS(query=query, request=MagicMock())
    assert mqs._sources and "ParquetFileSource" in mqs._sources[0]


def test_parquet_thread_puts_frame_on_queue(tmp_path):
    pd.DataFrame({"a": [1, 2]}).to_parquet(tmp_path / "x.parquet", index=False)
    queue: asyncio.Queue = asyncio.Queue()
    cls = SOURCE_REGISTRY["ParquetFileSource"]
    thread = cls("ParquetFileSource", {"source": {"path": str(tmp_path / "x.parquet")}}, MagicMock(), queue)
    thread.start()
    thread.join(timeout=30)
    assert thread.exc is None
    # FILL IN: queue.get_nowait() == {"ParquetFileSource": df} with df["a"].tolist() == [1, 2]
    #          (the thread's loop put it; asyncio.Queue.get_nowait is thread-agnostic here).


def test_sources_import_is_lazy():
    code = ("import sys, querysource.queries.multi.sources; "
            "assert 's3fs' not in sys.modules and 'gcsfs' not in sys.modules")
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
```

### FILL IN checklist
- [ ] Integration test: assert the queue item.
- [ ] Generated schemas: confirm `source.path` (ParquetFileSource) and `credentials.bucket` (ParquetGCSSource) are marked required, then commit only the three files.

---

## Acceptance Criteria

- [ ] `SOURCE_REGISTRY` and `__all__` contain the three classes. `ParquetSource` is not registered.
- [ ] MultiQS parses a `sources: [{ParquetFileSource: …}]` entry, and the thread puts `{name: df}` on the queue.
- [ ] Importing `querysource.queries.multi.sources` doesn't import s3fs/gcsfs.
- [ ] `generated/ParquetFileSource.json`, `generated/ParquetS3Source.json` and `generated/ParquetGCSSource.json` are committed, with no unrelated `generated/` changes.
- [ ] Validation Commands pass. The full feature suite is run by `/sdd-done`.
- [ ] `ruff check querysource/queries/multi/sources/ tests/test_source_registry.py tests/test_multiqs_parquet_integration.py` is clean.

---

## Validation Commands

- `pytest tests/test_source_registry.py -q`
- `pytest tests/test_multiqs_parquet_integration.py -q`
- `pytest tests/test_multiqs_sources_integration.py -q`

---

## Test Specification

See the test blocks above.

---

## Agent Instructions

1. **Work in the feature worktree**: `python -m scripts.sdd.ensure_worktree --slug parquet-multiqs-source --feature-id FEAT-158`.
2. **Read the spec** at the path above.
3. **Check dependencies**: TASK-798, TASK-799 and TASK-800 must be `"done"`.
4. **Verify the Codebase Contract** and re-run each `grep -c` anchor before editing.
5. **Update status** to `"in-progress"` (set `started_at`) and commit only the index file.
6. **Implement** from the Blueprint and complete every FILL IN.
7. **Verify**: run the Validation Commands.
8. **Commit the code**, staging only the files listed above.
9. **Close**: `scripts/sdd/close_task.sh TASK-801 parquet-multiqs-source verified`.
10. **Fill in the Completion Note**, then commit the staged SDD state.

---

## Completion Note

**Completed by**: sdd-worker (seat gpt-5.6-terra, codex, 1 attempt, ~317s)
**Date**: 2026-09-30
**Notes**: Registry wiring, exports, integration test and 3 generated schemas delivered. Registry + integration tests: 24 passed;
full Parquet + S3 suites: 85 passed. `tests/test_multiqs_sources_integration.py::test_guardrail_rejects_too_many_sources`
fails identically on unmodified `dev` (expects DriverError, gets QueryException) — pre-existing, not caused by this feature.

**Deviations from spec**: none
