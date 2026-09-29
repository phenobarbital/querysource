# TASK-792: Lazy `WRITERS` registry and `resolve_writer` in `output.py`

**Feature**: FEAT-154 — Lazy-import output writers
**Spec**: `sdd/specs/lazy-import-writers.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

`querysource/outputs/output.py:19-37` imports all 13 writer classes, and `:39-62` builds the
`WRITERS` dict from them. That module sits on the path of every HTTP handler and of
`DataOutput`, so every writer, including PDF and WeasyPrint, loads at startup.

This task implements spec §3 **Module 3** (AC3, AC4; codex S2, S3, S9):
- `WRITERS` becomes a `LazyWriterRegistry(dict)` holding `"<submodule>:<Class>"` specs, which
  are resolved on first read and cached.
- `resolve_writer()` replaces the `try/except KeyError` lookup in `DataOutput.response`.

The registry works functionally without TASK-791. The startup-time win (spec G1/G2) needs
both TASK-791 and this task, because resolving `json:jsonWriter` runs `writers/__init__.py`.

---

## Scope

- Delete the `from .writers import (...)` block (`output.py:19-37`).
- Add `class LazyWriterRegistry(dict)`, overriding `__getitem__` and `get`. The class contract is in the blueprint.
- Rewrite `WRITERS` (`output.py:39-62`) as a `LazyWriterRegistry` with the same 18 keys, as spec
  strings. Keep the commented-out entries (`profiling`, `eda`, `describe`, `clustering`) as comments.
- Add module-level `resolve_writer(ctype: str) -> type[AbstractWriter]`.
- In `DataOutput.response`, replace the `try: wt = WRITERS[self.format] except KeyError: ...` block
  (`output.py:217-224`) with `wt = resolve_writer(self.format)`.

**NOT in scope**: `writers/__init__.py` (TASK-791), `outputs/__init__.py` (TASK-793), and new tests (TASK-794).
Do not touch any other `DataOutput` method.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/outputs/output.py` | MODIFY | Remove eager writer imports; add `LazyWriterRegistry`, lazy `WRITERS` and `resolve_writer`; call it from `response()` |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from importlib import import_module                   # stdlib; precedent querysource/outputs/dt/factory.py:4
from typing import Optional, Union                    # output.py:1 already imports Union
from navconfig.logging import logging                 # verified: querysource/outputs/output.py:8
from .writers.abstract import AbstractWriter          # verified: querysource/outputs/writers/abstract.py:29 (class AbstractWriter(ABC))
```

### Existing Signatures to Use
```python
# querysource/outputs/output.py (verified at HEAD 3c19da4)
from typing import Union                              # line 1
from ..utils.errors import build_error_payload        # line 18 — last import before the writers block
from .writers import (                                # line 19 … `)` line 37  ← delete
WRITERS = {                                           # line 39 … `}` line 62  ← rewrite; 18 keys:
    # json, table, txt, plain, csv, tsv, excel, xls, xlsx, xlsm, ods, html,
    # bokeh, plotly, pickle, report, pdf, xml
class DataOutput:                                     # line 64
    self.logger = logging.getLogger('QS.Output')      # line 79 (in __init__)
    self.format = ctype                               # line 102
    async def response(self):                         # line 211
        ### before, making calculation of stats.     # line 216
        try:                                          # line 217
            wt = WRITERS[self.format]                 # line 218
        except KeyError:                              # line 219
            ### invalid Writer, defaulting to json
            self.logger.warning(
                f'Invalid Writer {self.format}, default to JSON.'
            )
            wt = WRITERS['json']                      # line 224
        writer = wt(request=..., ...)                 # line 225 — UNCHANGED
```

Writer class names per spec key, from `writers/__init__.py`:
`json:jsonWriter`, `table:TableWriter`, `txt:TXTWriter`, `csv:CSVWriter`, `tsv:TSVWriter`,
`excel:ExcelWriter`, `html:HTMLWriter`, `bokeh:BokehWriter`, `plotly:PlotlyWriter`,
`pickle:PickleWriter`, `report:ReportWriter`, `pdf:PDFWriter`, `xml:XMLWriter`.

Existing contract that must keep working: `tests/qsurl/test_error_envelope.py:59` does
`monkeypatch.setitem(output_module.WRITERS, "json", _StubWriter)`, where `_StubWriter` is a
plain class and not an `AbstractWriter` subclass. A stored class must pass through unchanged.

### Does NOT Exist
- ~~`querysource.outputs.registry`~~ / ~~`get_writer()`~~: no such module or function. `resolve_writer` is new.
- ~~`LazyWriterRegistry`~~: new in this task.
- ~~`querysource.outputs.writers.WRITERS`~~: the registry stays in `output.py`.
- ~~`import_module` already imported in `output.py`~~: it is not. Add it.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/outputs/output.py", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/outputs/output.py#DataOutput",
    "sym:querysource/outputs/output.py#DataOutput.response",
    "sym:querysource/outputs/writers/abstract.py#AbstractWriter"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **Membership before lookup** (codex S3). `resolve_writer` checks `ctype in WRITERS` first.
  Never wrap `WRITERS[ctype]` in `except KeyError`: a `KeyError` raised *inside* a writer
  module's import would then be misrouted to the json fallback.
- **Any resolution failure is `ImportError`**, raised `from` the original error. That covers a
  missing module, a module that raises during import, and a missing class name (`AttributeError`).
- **Cache write-back** uses `dict.__setitem__(self, ctype, cls)`, never `self[ctype] = ...`, so it
  cannot recurse into any future `__setitem__` override.
- **Class passthrough**: a stored value that is not a `str` is returned as is, with no `issubclass` check,
  because the test stub is not an `AbstractWriter` subclass.
- `dict.get` does not call `__getitem__`, so `get` **must** be overridden (AC3).
- `values()` and `items()` intentionally expose the stored form, a spec or a class (spec §3 M3 docstring).
- Keep the warning text `f'Invalid Writer {ctype}, default to JSON.'` and log it on the `QS.Output` logger.
  `resolve_writer` is module-level, so use a module logger `logging.getLogger('QS.Output')`, the same
  logger name as `output.py:79`.
- Import sorting: ruff `I` is enabled. Put `import_module` and `Optional` in the stdlib group, and put
  `from .writers.abstract import AbstractWriter` after the `..` relative imports.

---

## Implementation Blueprint

### Steps (in order)
1. Confirm the anchors, each count must be 1:
   - `grep -c 'from .writers import (' querysource/outputs/output.py`
   - `grep -c '^WRITERS = {' querysource/outputs/output.py`
   - `grep -c '                wt = WRITERS\[self.format\]' querysource/outputs/output.py`

   *Why*: a count that does not match means the code has drifted. Stop and report.
2. Update the stdlib imports: `from importlib import import_module` and `from typing import Optional, Union`.
3. Replace lines 19-62 (the writers import block plus the `WRITERS` dict) with the block below.
4. Replace lines 217-224 in `response()` with `wt = resolve_writer(self.format)`.
5. Run the smoke checks, the Validation Commands and ruff.

### `querysource/outputs/output.py` (MODIFY — imports)
```python
# occurrences: 1 (verified: grep -c '^from typing import Union' querysource/outputs/output.py)
# REPLACE line 1 `from typing import Union` WITH:
from importlib import import_module
from typing import Optional, Union
```

### `querysource/outputs/output.py` (MODIFY — lines 19-62)
```python
# occurrences: 1 each (verified: grep -c 'from .writers import (' / grep -c '^WRITERS = {' querysource/outputs/output.py)
# REPLACE from `from .writers import (` (line 19) through the closing `}` of WRITERS (line 62) WITH:
from .writers.abstract import AbstractWriter

_WRITERS_PACKAGE = "querysource.outputs.writers"
_logger = logging.getLogger('QS.Output')


class LazyWriterRegistry(dict):
    """Format → writer registry; stored values are ``"<submodule>:<Class>"`` specs or classes.

    Reads (``[]`` and ``get``) always return a class: a ``str`` spec is imported
    from ``querysource.outputs.writers.<submodule>`` on first access and cached
    back with ``dict.__setitem__``. Writes, ``monkeypatch.setitem``, ``in`` and
    ``len`` behave like a plain ``dict``; ``values()``/``items()`` expose the
    stored form (spec or class).
    """

    def __getitem__(self, ctype: str) -> type[AbstractWriter]:
        """Resolve and return the writer class registered for ``ctype``.

        Args:
            ctype: output format key (``"json"``, ``"pdf"``, ...).

        Returns:
            type[AbstractWriter]: the writer class (or an injected class as-is).

        Raises:
            KeyError: ``ctype`` is not registered.
            ImportError: the registered spec cannot be imported or names a
                missing class (raised ``from`` the original error).
        """
        value = dict.__getitem__(self, ctype)  # KeyError propagates for a missing key
        if not isinstance(value, str):
            return value
        # FILL IN: split `value` on ":" into (submodule, class_name); import
        #          `import_module(f".{submodule}", _WRITERS_PACKAGE)` and `getattr` the class.
        #          Wrap ANY exception from those two steps as
        #          `raise ImportError(f"cannot load writer {ctype!r} from {value!r}: {exc}") from exc`
        #          — bounded by AC4 + spec §7 "Membership before lookup".
        #          On success `dict.__setitem__(self, ctype, cls)` and return cls — bounded by AC3/AC4 (cached).

    def get(
        self, ctype: str, default: Optional[type[AbstractWriter]] = None
    ) -> Optional[type[AbstractWriter]]:
        """Like ``__getitem__`` but return ``default`` for a missing key.

        Raises:
            ImportError: a registered spec fails to resolve (never masked as ``default``).
        """
        if ctype not in self:
            return default
        return self[ctype]


WRITERS: LazyWriterRegistry = LazyWriterRegistry({
    "json": "json:jsonWriter",
    "table": "table:TableWriter",
    "txt": "txt:TXTWriter",
    "plain": "txt:TXTWriter",
    "csv": "csv:CSVWriter",
    "tsv": "tsv:TSVWriter",
    'excel': "excel:ExcelWriter",
    'xls': "excel:ExcelWriter",
    'xlsx': "excel:ExcelWriter",
    'xlsm': "excel:ExcelWriter",
    'ods': "excel:ExcelWriter",
    'html': "html:HTMLWriter",
    'bokeh': "bokeh:BokehWriter",
    'plotly': "plotly:PlotlyWriter",
    'pickle': "pickle:PickleWriter",
    # 'profiling': "profiling:ProfileWriter",
    'report': "report:ReportWriter",
    'pdf': "pdf:PDFWriter",
    'xml': "xml:XMLWriter",
    # 'eda': "eda:EDAWriter",
    # 'describe': "describe:DescribeWriter",
    # 'clustering': "clustering:ClusterWriter"
})


def resolve_writer(ctype: str) -> type[AbstractWriter]:
    """Return the writer class for ``ctype``, importing it on first use.

    A format that is not registered logs a warning and falls back to the
    json writer (unchanged behaviour). A registered format always returns
    its own class — the lazily-imported one or an injected override.

    Args:
        ctype: output format key.

    Returns:
        type[AbstractWriter]: the writer class to instantiate.

    Raises:
        ImportError: a registered writer fails to import; never swallowed
            into the json fallback.
    """
    # FILL IN: `if ctype not in WRITERS:` log
    #          `_logger.warning(f'Invalid Writer {ctype}, default to JSON.')` and
    #          `return WRITERS['json']`; otherwise `return WRITERS[ctype]`
    #          — bounded by AC4 (membership check BEFORE lookup, no `except KeyError`).
```
**Why this shape**: the aliases (`xls`, `xlsx` and so on) resolve to the same class because they
share the same module in `sys.modules`, which `test_registry_aliases_share_class` asserts (codex S2).
The mixed-quote keys copy the original dict so the diff stays small.

### `querysource/outputs/output.py` (MODIFY — `DataOutput.response`)
```python
# occurrences: 1 (verified: grep -c '                wt = WRITERS\[self.format\]' querysource/outputs/output.py)
# REPLACE (output.py:217-224):
#             try:
#                 wt = WRITERS[self.format]
#             except KeyError:
#                 ### invalid Writer, defaulting to json
#                 self.logger.warning(
#                     f'Invalid Writer {self.format}, default to JSON.'
#             )
#                 wt = WRITERS['json']
# WITH:
            wt = resolve_writer(self.format)
```
**Why**: this is the only place that reads the registry, and routing it through
`resolve_writer` keeps the fallback semantics in one function.

### FILL IN checklist
- [ ] `output.py::LazyWriterRegistry.__getitem__`: split the spec, import, get the attribute, wrap every failure as `ImportError` raised `from` the original, and cache with `dict.__setitem__`. Bounded by AC3 and AC4.
- [ ] `output.py::resolve_writer`: check membership first, log the warning and fall back to json only for a missing key. Bounded by AC4.

---

## Acceptance Criteria

- [ ] `WRITERS` is a `dict` subclass with the same 18 keys (AC3)
- [ ] `WRITERS[k]` and `WRITERS.get(k)` return a class for all 18 keys, and `WRITERS.get("nope") is None`
- [ ] `WRITERS["xls"] is WRITERS["excel"]` and `WRITERS["plain"] is WRITERS["txt"]`
- [ ] After `WRITERS["csv"]`, `dict.__getitem__(WRITERS, "csv")` is a class, not a `str` (cached)
- [ ] `resolve_writer("nope")` returns the json writer and logs the warning; `WRITERS["x"] = "does_not_exist:Nope"` makes `resolve_writer("x")` raise `ImportError` (AC4)
- [ ] `python -c "import sys, querysource.outputs.output as o; assert all(isinstance(dict.__getitem__(o.WRITERS, k), str) for k in o.WRITERS)"` succeeds
- [ ] `ruff check querysource/outputs/output.py` is clean
- [ ] Validation Commands pass, including `tests/qsurl/test_error_envelope.py` unchanged

---

## Validation Commands

- `pytest tests/qsurl/test_error_envelope.py -q`
- `pytest tests/unit/test_output_error.py -q`
- `pytest tests/unit/test_handler_output_status.py -q`
- `pytest tests/unit/test_iter_format_dataframe.py -q`

---

## Test Specification

Covered by TASK-794 (`tests/unit/test_lazy_writers.py`, spec §4 rows tagged M3).

---

## Agent Instructions

1. Work in the feature worktree:
   `python -m scripts.sdd.ensure_worktree --slug lazy-import-writers --feature-id FEAT-154`
2. Read spec §2 Overview, §3 M3 and §7.
3. No dependencies.
4. Verify the Codebase Contract. Re-run the three `grep -c` anchor checks.
5. Set the status to `"in-progress"` in `sdd/tasks/index/lazy-import-writers.json`.
6. Implement from the blueprint.
7. Run the Validation Commands, the smoke checks and ruff.
8. Commit only `querysource/outputs/output.py`.
9. Close with `scripts/sdd/close_task.sh TASK-792 lazy-import-writers verified`.
10. Fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
