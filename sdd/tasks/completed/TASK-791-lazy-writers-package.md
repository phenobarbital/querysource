# TASK-791: Lazy `querysource.outputs.writers` package (PEP 562)

**Feature**: FEAT-154 — Lazy-import output writers
**Spec**: `sdd/specs/lazy-import-writers.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

`querysource/outputs/writers/__init__.py:1-17` eagerly imports all 13 writer submodules. Any
`import querysource.outputs.writers.<x>`, including `.abstract`, which `output.py` needs,
therefore runs every writer module, plus `pdf.py` → weasyprint before TASK-790.

This task implements spec §3 **Module 2** (goal G3, AC5). Names resolve on first attribute
access, and `__all__`, `dir()` and `from ... import *` keep working.

---

## Scope

- Replace the eager imports with a `_WRITER_MODULES` name → `.submodule` map.
- Add `__all__ = tuple(_WRITER_MODULES)` with the same 13 names.
- Add a PEP 562 `__getattr__` that imports `.submodule`, reads the class, caches it in
  `globals()` and returns it. Unknown names raise `AttributeError` with the standard message.
- Add `__dir__` returning `sorted(__all__)`.
- Keep the four commented-out writers (`ProfileWriter`, `EDAWriter`, `DescribeWriter`, `ClusterWriter`) as comments.

**NOT in scope**: `output.py`'s `WRITERS` (TASK-792), `outputs/__init__.py` (TASK-793), and the tests (TASK-794).
Do not add a `WRITERS` object to this package (see Does NOT Exist).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/outputs/writers/__init__.py` | MODIFY | Full rewrite: lazy exports via `__getattr__`/`__dir__` |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from importlib import import_module   # stdlib; precedent querysource/outputs/dt/factory.py:4
```

### Existing Signatures to Use
```python
# querysource/outputs/writers/__init__.py (current, verified at HEAD 3c19da4) — 13 live names:
from .json import jsonWriter      # :1
from .txt import TXTWriter        # :2
from .csv import CSVWriter        # :3
from .excel import ExcelWriter    # :4
from .html import HTMLWriter      # :5
from .bokeh import BokehWriter    # :6
from .plotly import PlotlyWriter  # :7
from .tsv import TSVWriter        # :8
from .report import ReportWriter  # :9
from .pickle import PickleWriter  # :10
from .table import TableWriter    # :11
from .pdf import PDFWriter        # :12
# from .profiling import ProfileWriter   # :13
# from .eda import EDAWriter             # :14
# from .describe import DescribeWriter   # :15
# from .clustering import ClusterWriter  # :16
from .xml import XMLWriter        # :17

# Pattern to copy — querysource/queries/multi/destinations/__init__.py:76-86
def __getattr__(name: str):
    ...
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
def __dir__():
    return __all__
```

Callers that must keep working unchanged:
- `querysource/outputs/output.py:19-37`: `from .writers import (BokehWriter, ..., jsonWriter)`. With PEP 562, `from pkg import Name` falls through to `__getattr__`.
- `tests/unit/test_csv_writer_dataframe.py` and `tests/integration/test_csv_stream_response.py` import submodules directly.

### Does NOT Exist
- ~~`querysource.outputs.writers.WRITERS`~~: the registry lives only in `querysource/outputs/output.py`.
- ~~An `__all__` in this package today~~: there is none. This task adds it.
- ~~`querysource.outputs.writers.AbstractWriter` as a package export~~: it was never exported. Import it from `.abstract`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/outputs/writers/__init__.py", "action": "MODIFY"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- `__getattr__` **must** write the resolved class into `globals()`. That way later
  accesses skip `__getattr__`, and `writers.PDFWriter is WRITERS["pdf"]` holds, because both
  come from the same `sys.modules` entry (spec §7 "identity").
- `import *` on a PEP 562 module uses `__all__` and calls `__getattr__` for each missing name.
  So `__all__` must list exactly the 13 names (AC5).
- Use `import_module(sub, __name__)`, where `sub` is the relative `.json` etc. and `__name__` is the anchor package.
- Resolve nothing at import time. The module body must not import any submodule.

---

## Implementation Blueprint

### Steps (in order)
1. Confirm the anchor: `grep -c 'from .json import jsonWriter' querysource/outputs/writers/__init__.py` → 1.
   *Why*: a 0 means the file has drifted. Stop and report.
2. Replace the whole file with the block below, then complete `__getattr__`.
3. Run the smoke checks and the Validation Commands, then `ruff check querysource/outputs/writers/__init__.py`.

### `querysource/outputs/writers/__init__.py` (MODIFY — full rewrite of lines 1-17)
```python
"""Output writers for QuerySource, resolved lazily on first access (FEAT-154).

Importing this package (or any submodule such as ``.abstract``) no longer
imports every writer. ``from querysource.outputs.writers import PDFWriter``
imports only ``.pdf`` (PEP 562 ``__getattr__``).
"""
from importlib import import_module

_WRITER_MODULES: dict[str, str] = {
    "jsonWriter": ".json",
    "TXTWriter": ".txt",
    "CSVWriter": ".csv",
    "ExcelWriter": ".excel",
    "HTMLWriter": ".html",
    "BokehWriter": ".bokeh",
    "PlotlyWriter": ".plotly",
    "TSVWriter": ".tsv",
    "ReportWriter": ".report",
    "PickleWriter": ".pickle",
    "TableWriter": ".table",
    "PDFWriter": ".pdf",
    # "ProfileWriter": ".profiling",
    # "EDAWriter": ".eda",
    # "DescribeWriter": ".describe",
    # "ClusterWriter": ".clustering",
    "XMLWriter": ".xml",
}

__all__ = tuple(_WRITER_MODULES)


def __getattr__(name: str) -> type:
    """Import and cache the writer class ``name`` on first access (PEP 562).

    Args:
        name: a writer class name listed in ``__all__``.

    Returns:
        type: the writer class.

    Raises:
        AttributeError: if ``name`` is not a registered writer.
    """
    # FILL IN: look up `name` in `_WRITER_MODULES`; if absent raise
    #          AttributeError(f"module {__name__!r} has no attribute {name!r}")
    #          (exact message — bounded by spec §7 Patterns, destinations/__init__.py:80).
    #          Otherwise `cls = getattr(import_module(_WRITER_MODULES[name], __name__), name)`,
    #          store `globals()[name] = cls`, return cls — bounded by AC5 + identity (spec §7).


def __dir__() -> list[str]:
    """Expose the lazily-resolved writer names to ``dir()``."""
    return sorted(__all__)
```
**Why this shape**: a plain dict plus `__getattr__` is the precedent already used in
`multi/destinations/__init__.py`. Caching in `globals()` makes each name cost one import. `__dir__`
returns a sorted list because `dir()` expects a list, not a tuple.

### FILL IN checklist
- [ ] `writers/__init__.py::__getattr__`: unknown name raises `AttributeError` with the exact message. A known name is imported, cached and returned. Bounded by AC5 and spec §7.

---

## Acceptance Criteria

- [ ] `python -c "from querysource.outputs.writers import CSVWriter; import sys; assert 'querysource.outputs.writers.pdf' not in sys.modules"` succeeds
- [ ] `python -c "from querysource.outputs.writers import *; print(PDFWriter, XMLWriter, jsonWriter)"` succeeds (AC5)
- [ ] `querysource.outputs.writers.Nope` raises `AttributeError`
- [ ] `set(dir(querysource.outputs.writers)) >= set(querysource.outputs.writers.__all__)`, and `len(__all__) == 13`
- [ ] `ruff check querysource/outputs/writers/__init__.py` is clean
- [ ] Validation Commands pass

---

## Validation Commands

- `pytest tests/unit/test_csv_writer_dataframe.py -q`
- `pytest tests/integration/test_csv_stream_response.py -q`
- `pytest tests/unit/test_output_error.py -q`

---

## Test Specification

Covered by TASK-794 (`tests/unit/test_lazy_writers.py`, spec §4 rows tagged M2).

---

## Agent Instructions

1. Work in the feature worktree:
   `python -m scripts.sdd.ensure_worktree --slug lazy-import-writers --feature-id FEAT-154`
2. Read spec §3 M2 and §7.
3. No dependencies.
4. Verify the Codebase Contract.
5. Set the status to `"in-progress"` in `sdd/tasks/index/lazy-import-writers.json`.
6. Implement from the blueprint.
7. Run the Validation Commands, the smoke checks and ruff.
8. Commit only `querysource/outputs/writers/__init__.py`.
9. Close with `scripts/sdd/close_task.sh TASK-791 lazy-import-writers verified`.
10. Fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
