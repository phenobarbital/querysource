# TASK-790: PDF writer — lazy weasyprint import and bounded off-loop render

**Feature**: FEAT-154 — Lazy-import output writers
**Spec**: `sdd/specs/lazy-import-writers.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

`querysource/outputs/writers/pdf.py:7` imports WeasyPrint at module level. That import costs
~0.45 s, about 27% of `import querysource.queries.qs`, and every process pays it, even one that never renders a PDF.
`PDFWriter.get_response` also calls `HTML(string=result).write_pdf(output)` synchronously
inside an `async def` (`pdf.py:53`), which blocks the event loop for the whole render.

This task implements spec §3 **Module 1**, resolved decision §8 Q1 ("Bounded executor"),
and AC6: the import, the HTML build and `write_pdf` all move into a sync helper that runs on a
dedicated, bounded `ThreadPoolExecutor`.

---

## Scope

- Remove the module-level `from weasyprint import HTML` from `pdf.py`.
- Add `_render_pdf(html: str) -> bytes`. It does the function-local weasyprint import, builds
  the document and calls `write_pdf`. It runs in the worker thread.
- Add `_pdf_executor() -> ThreadPoolExecutor`. It lazily creates one process-wide executor
  with `max_workers=PDF_RENDER_WORKERS` and `thread_name_prefix="qs-pdf"`, then reuses it.
- Change `PDFWriter.get_response` to
  `buffer = await asyncio.get_running_loop().run_in_executor(_pdf_executor(), _render_pdf, result)`.
  Everything after the render stays byte-for-byte the same.
- Add `PDF_RENDER_WORKERS = config.getint("PDF_RENDER_WORKERS", fallback=4)` to `querysource/conf.py`.
- Make `pdf.py` ruff-clean: sort its imports, and change `super(PDFWriter, self)` to `super()`.

**NOT in scope**:
- `writers/__init__.py` (TASK-791), `output.py` (TASK-792), `outputs/__init__.py` (TASK-793).
- New test file `tests/unit/test_lazy_writers.py` (TASK-794).
- Moving `ReportWriter.render_content` chart work off the loop (spec non-goal, codex S5).
- `pyproject.toml` / `uv.lock`: WeasyPrint stays a hard dependency (AC7).
- An `asyncio.Semaphore` limiter was rejected by §8 Q1; use the executor.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/outputs/writers/pdf.py` | MODIFY | Lazy weasyprint, `_render_pdf`, `_pdf_executor`, off-loop `get_response` |
| `querysource/conf.py` | MODIFY | Add `PDF_RENDER_WORKERS` config int |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from .report import ReportWriter                 # verified: querysource/outputs/writers/pdf.py:8
from ...conf import CSV_DEFAULT_DELIMITER        # relative-conf precedent: querysource/outputs/writers/csv.py:5
from navconfig import config                     # already used by querysource/conf.py (config.getint at :281)
import asyncio                                   # stdlib — NOT currently imported in pdf.py
from concurrent.futures import ThreadPoolExecutor  # stdlib; precedent querysource/interfaces/http.py:176
```

### Existing Signatures to Use
```python
# querysource/outputs/writers/pdf.py (verified at HEAD 3c19da4)
import time                                      # line 1
from typing import Any, Union                    # line 2
from io import BytesIO                           # line 3
from aiohttp import web                          # line 4
from weasyprint import HTML                      # line 7  ← remove
class PDFWriter(ReportWriter):                   # line 10
    pdf_library: str = 'reportlab'               # line 16
    def __init__(self, request, resultset, filename=None, response_type='web',
                 download=False, compression=None, ctype=None, **kwargs):   # lines 18-28
        super(PDFWriter, self).__init__(...)     # line 29 ← ruff UP008, change to super()
    async def get_response(self) -> web.StreamResponse:   # line 48
        output = BytesIO()                                # 49
        result = await self.render_content()              # 50
        response = await self.response(self.response_type)  # 51
        # Create the PDF.                                 # 52
        HTML(string=result).write_pdf(output)             # 53
        output.seek(0)                                    # 54
        buffer = output.getvalue()                        # 55
        # 56-66: content_length, download branch, else stream_response — UNCHANGED

# querysource/conf.py
HTTPCLIENT_MAX_SEMAPHORE = config.getint("HTTPCLIENT_MAX_SEMAPHORE", fallback=5)   # line 280
HTTPCLIENT_MAX_WORKERS = config.getint("HTTPCLIENT_MAX_WORKERS", fallback=1)       # line 281
```

### Does NOT Exist
- ~~`PDF_RENDER_WORKERS`~~: not in `conf.py` yet; this task adds it.
- ~~`PDFWriter.render_pdf`~~: no such method. The helper is the module-level `_render_pdf`.
- ~~`asyncio` in `pdf.py`~~: not imported today; add it.
- ~~`weasyprint` anywhere else in `querysource/`~~: `pdf.py` is the only user.
- ~~`asyncio.to_thread` / default executor~~: rejected by §8 Q1; use `_pdf_executor()`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/outputs/writers/pdf.py", "action": "MODIFY"},
    {"path": "querysource/conf.py", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/outputs/writers/pdf.py#PDFWriter",
    "sym:querysource/outputs/writers/pdf.py#PDFWriter.get_response"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- The weasyprint import **must** be inside `_render_pdf`, not at module level and not in
  `get_response`, because the first call pays ~0.45 s and that must happen in the worker thread (codex S5).
- The executor must not depend on any event loop: no `asyncio.Semaphore` at module level, since
  pytest and gunicorn use several loops. Create it lazily, because creating it at import time
  would start pool bookkeeping in every process.
- Do not shut the executor down. Its idle threads exit with the process (spec §7).
- Exceptions from `_render_pdf` propagate unchanged. Do **not** wrap them in `try/except`:
  `DataOutput.response` already handles writer errors.

### References in Codebase
- `querysource/interfaces/http.py:176-178`: the `ThreadPoolExecutor(max_workers=int(HTTPCLIENT_MAX_WORKERS))` precedent.
- `querysource/outputs/writers/bokeh.py:37-41`, `report.py:10-13`: heavy imports at the call site.

---

## Implementation Blueprint

### Steps (in order)
1. Confirm the anchors: `grep -c 'from weasyprint import HTML' querysource/outputs/writers/pdf.py` → 1,
   `grep -c 'HTTPCLIENT_MAX_WORKERS = config.getint' querysource/conf.py` → 1. *Why*: an anchor
   count of 0 means the code has drifted. Stop and report instead of guessing.
2. Add `PDF_RENDER_WORKERS` to `conf.py` below `HTTPCLIENT_MAX_WORKERS`. *Why*: `pdf.py` imports it,
   so it must exist first.
3. Replace `pdf.py` lines 1-8 with the import block below. *Why*: this drops weasyprint and keeps ruff's `I001` clean.
4. Add `_EXECUTOR`, `_pdf_executor` and `_render_pdf` between the imports and `class PDFWriter`.
5. Replace lines 49-55 of `get_response` with the executor call. Leave lines 56-66 untouched.
   *Why*: AC6 requires the same bytes, `content_length` and `Content-Disposition` handling.
6. Change `super(PDFWriter, self).__init__(` to `super().__init__(`. *Why*: ruff UP008 on a file you touch.
7. Run the smoke check and the Validation Commands.

### `querysource/conf.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'HTTPCLIENT_MAX_WORKERS = config.getint' querysource/conf.py)
# AFTER — insert below `HTTPCLIENT_MAX_WORKERS = config.getint("HTTPCLIENT_MAX_WORKERS", fallback=1)` (verified: querysource/conf.py:281)

## PDF rendering (WeasyPrint) worker pool — bounds concurrent PDF renders:
PDF_RENDER_WORKERS = config.getint("PDF_RENDER_WORKERS", fallback=4)
```
**Why**: this follows the existing `config.getint(..., fallback=N)` pattern next to the other
worker-count key. The default of 4 comes from §8 Q1.

### `querysource/outputs/writers/pdf.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'from weasyprint import HTML' querysource/outputs/writers/pdf.py)
# REPLACE lines 1-8 (from `import time` through `from .report import ReportWriter`) with:
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import Any, Optional, Union

from aiohttp import web

from ...conf import PDF_RENDER_WORKERS
from .report import ReportWriter

# from reportlab.lib.pagesizes import letter, A4
# from reportlab.platypus import SimpleDocTemplate, Paragraph

_EXECUTOR: Optional[ThreadPoolExecutor] = None


def _pdf_executor() -> ThreadPoolExecutor:
    """Return the process-wide PDF render executor, creating it on first use.

    Bounded to ``PDF_RENDER_WORKERS`` threads (prefix ``qs-pdf``) so parallel
    WeasyPrint renders cannot grow memory without limit. Loop-agnostic: no
    ``asyncio.Semaphore``, so it is safe across event loops.

    Returns:
        ThreadPoolExecutor: the shared executor instance.
    """
    global _EXECUTOR  # noqa: PLW0603 — process-wide lazy singleton
    # FILL IN: create `ThreadPoolExecutor(max_workers=PDF_RENDER_WORKERS, thread_name_prefix="qs-pdf")`
    #          only when `_EXECUTOR is None`, then return `_EXECUTOR` — bounded by AC6 (same instance on every call)


def _render_pdf(html: str) -> bytes:
    """Render ``html`` to PDF bytes with WeasyPrint (sync, CPU-bound).

    Runs entirely in the worker thread: the ``weasyprint`` import (the first
    call pays ~0.45 s), the ``HTML(string=html)`` build and ``write_pdf`` all
    happen here, so none of them touch the event loop.

    Args:
        html: the rendered report HTML.

    Returns:
        bytes: the PDF document.
    """
    from weasyprint import HTML  # lazy: keeps ~0.45 s off every process start (FEAT-154)

    output = BytesIO()
    HTML(string=html).write_pdf(output)
    return output.getvalue()
```
**Why this shape**: all three weasyprint touch-points live in one sync function that the
executor runs, which satisfies G1 (no import at startup) and G4 (no blocking on the loop) together.
`# noqa: PLW0603` only documents intent, since `PLW` is not in the ruff `select`. It is harmless,
and you may drop it if ruff reports it as an unused noqa (`RUF100` is not selected either).

```python
# occurrences: 1 (verified: grep -c '        HTML(string=result).write_pdf(output)' querysource/outputs/writers/pdf.py)
# REPLACE inside PDFWriter.get_response the lines (pdf.py:49-55):
#         output = BytesIO()
#         result = await self.render_content()
#         response = await self.response(self.response_type)
#         # Create the PDF.
#         HTML(string=result).write_pdf(output)
#         output.seek(0)
#         buffer = output.getvalue()
# WITH:
        result = await self.render_content()
        response = await self.response(self.response_type)
        # Create the PDF off the event loop, on the bounded qs-pdf executor.
        buffer = await asyncio.get_running_loop().run_in_executor(
            _pdf_executor(), _render_pdf, result
        )
```
Add a Google-style docstring to `get_response`, using the text from spec §3 M1's skeleton.
**Why**: `buffer` keeps its name and type (`bytes`), so lines 56-66 need no change.

### FILL IN checklist
- [ ] `pdf.py::_pdf_executor`: lazy create and reuse. Bounded by AC6 (`_max_workers == PDF_RENDER_WORKERS`, `qs-pdf` thread-name prefix, same instance on every call).
- [ ] `pdf.py::PDFWriter.get_response`: add the docstring, and leave the code after `buffer` unchanged. Bounded by AC6.

---

## Acceptance Criteria

- [ ] `python -c "import sys, querysource.outputs.writers.pdf; assert 'weasyprint' not in sys.modules"` succeeds
      (this holds even before TASK-791: no other writer imports weasyprint)
- [ ] `querysource.conf.PDF_RENDER_WORKERS` is an `int`, default `4`
- [ ] `_pdf_executor() is _pdf_executor()` and `_pdf_executor()._max_workers == PDF_RENDER_WORKERS`
- [ ] `get_response` awaits `run_in_executor(_pdf_executor(), _render_pdf, result)`, and the post-render code is unchanged (AC6)
- [ ] `ruff check querysource/outputs/writers/pdf.py querysource/conf.py` is clean
- [ ] Validation Commands pass

---

## Validation Commands

- `pytest tests/unit/test_output_error.py -q`
- `pytest tests/unit/test_handler_output_status.py -q`

The behaviour tests for this module (`test_pdf_render_runs_off_loop`, `test_pdf_render_error_propagates`,
`test_pdf_executor_bounded`, `test_pdf_render_workers_config_default`) are written in TASK-794.
Until then, also run this smoke check:
`python -c "from querysource.outputs.writers.pdf import _render_pdf, _pdf_executor; assert _pdf_executor().submit(_render_pdf, '<p>hi</p>').result()[:4] == b'%PDF'"`

---

## Test Specification

Covered by TASK-794 (`tests/unit/test_lazy_writers.py`, spec §4 rows tagged M1).

---

## Agent Instructions

1. Work in the feature worktree:
   `python -m scripts.sdd.ensure_worktree --slug lazy-import-writers --feature-id FEAT-154`
2. Read spec §3 M1, §7 and §8 Q1.
3. No dependencies.
4. Verify the Codebase Contract. Re-run the two `grep -c` anchor checks.
5. Set the status to `"in-progress"` in `sdd/tasks/index/lazy-import-writers.json`.
6. Implement from the blueprint and complete every `# FILL IN:`.
7. Run the Validation Commands, the smoke check and ruff.
8. Commit only `querysource/outputs/writers/pdf.py` and `querysource/conf.py`.
9. Close with `scripts/sdd/close_task.sh TASK-790 lazy-import-writers verified`.
10. Fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
