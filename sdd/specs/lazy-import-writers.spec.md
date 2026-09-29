---
type: feature
base_branch: dev
projects: [outputs, handlers]
tags: [lazy-import, startup-time, weasyprint, pdf, writers, performance]
---

# Feature Specification: Lazy-import output writers

**Feature ID**: FEAT-154
**Date**: 2026-09-29
**Author**: Jesus Lara (spec drafted by Claude Code)
**Status**: draft
**Target version**: 5.2.0
**Proposal**: `sdd/proposals/lazy-import-writers.proposal.md` (research state: `sdd/state/FEAT-154/`)

---

## 1. Motivation & Business Requirements

### Problem Statement

> actualmente todos los writers (incluido PDF que depende de weasyprint) hace un
> import on startup, lo que ralentiza la carga de Querysource en modo normal y
> servidor http — *e.g.* `querysource.services > outputs > writers > PDFWriter -> weasyprint`

Every output writer is imported at process start. `querysource/outputs/writers/__init__.py`
imports all 13 writer classes, and `querysource/outputs/output.py` builds a static
`WRITERS` dict from them. `pdf.py` imports `weasyprint` at module level, which loads
cffi/pango. That costs **~440–610 ms, about 27% of `import querysource.queries.qs`**
(proposal F005). Every other writer costs under 3 ms, because each one already defers
pandas, bokeh, plotly, seaborn and matplotlib to call time.

Both entry modes pay the cost:
- **Library mode:** `querysource/queries/base.py:14` (`from ..outputs.dt import OutputFactory`)
  runs `querysource/outputs/__init__.py`, which imports `output.py`, which imports every writer.
- **HTTP mode:** `querysource/services.py:22` imports handlers, which import
  `queries.qs` → `queries.base` → the same chain (proposal F009).

`PDFWriter.get_response` also calls `HTML(string=...).write_pdf()` synchronously inside
`async def`, which blocks the event loop for the whole render.

### Goals
- G1: Importing `querysource.queries.qs`, `querysource.outputs.dt`,
  `querysource.outputs.output` or `querysource.services` does not import `weasyprint`.
- G2: A writer class is imported only when its output format is first requested, and is
  then cached.
- G3: Every existing import path keeps working unchanged:
  - `from querysource.outputs import DataOutput`
  - `from querysource.outputs.output import DataOutput`
  - `from querysource.outputs.writers import <AnyWriter>`
  - `from querysource.outputs.writers.<mod> import <Writer>`
- G4: PDF rendering no longer blocks the event loop; it runs in a worker thread.

### Non-Goals (explicitly out of scope)
- **Packaging stays as it is.** `WeasyPrint>=65.0` remains in `[project].dependencies`
  (`pyproject.toml:111`); it is not moved to an extra (proposal U2).
- **Other import-time costs stay** (asyncdb/BigQuery ~0.5 s, pandas ~0.2 s, `interfaces.queries`).
  These are candidates for a follow-up.
- **Disabled writers stay as they are:** `describe.py`, `eda.py`, `clustering.py`, `profiling.py`.
  They are not registered, and their imports are not touched.
- **Unrelated outputs code is not touched:** `querysource/outputs/destinations/` and the
  internals of `querysource/outputs/dt/`.
- **Chart rendering stays on the loop.** `ReportWriter.render_content` still does synchronous
  chart work (matplotlib/seaborn/pygal, `report.py:248-300`) on the event loop. Only the
  WeasyPrint step moves off-loop (codex S5).

---

## 2. Architectural Design

### Overview

Three lazy layers, plus one call-site change:

1. **Lazy writer registry (`output.py`).** `WRITERS` becomes an instance of
   `LazyWriterRegistry`, a `dict` subclass. It stays module-level and mutable, so
   `monkeypatch.setitem` keeps working. Its values start as string specs of the form
   `"<submodule of querysource.outputs.writers>:<ClassName>"`, e.g. `"pdf:PDFWriter"`.
   - `WRITERS[k]` and `WRITERS.get(k)` **always return a class**, so existing direct
     consumers that expect `WRITERS[fmt]` to be callable keep working (codex S2).
     On the first access to a string spec, the registry imports the module with
     `importlib.import_module`, takes the class and **writes it back** (`dict.__setitem__`)
     as a cache.
   - A value that is already a class is returned as it is. That covers both a cached
     class and one injected with `monkeypatch.setitem(output_module.WRITERS, "json", Stub)`.
   - A new function, `resolve_writer(ctype)`, checks **membership first**
     (`ctype in WRITERS`). Only a *missing* key takes today's fallback: a warning, then
     `WRITERS["json"]`.
   - Any failure resolving a *registered* key propagates as `ImportError`, including a
     missing class attribute (re-raised as `ImportError ... from exc`). It is never turned
     into the json fallback (codex S3).
   - The top-level `from .writers import (...)` block is removed.
2. **Lazy `writers` package (`writers/__init__.py`).** The eager imports are replaced
   by a name→submodule map, a PEP 562 `__getattr__` and a `__dir__`. The same names are
   exported through `__all__`. The resolved class is cached in the module globals.
3. **Lazy `outputs` package (`outputs/__init__.py`).** `DataOutput` is exposed through
   PEP 562 `__getattr__`, so importing `querysource.outputs.dt` or `querysource.outputs.destinations`
   no longer executes `output.py`. This helps **library mode** (`queries.base` → `outputs.dt`).
   In **HTTP mode**, `querysource/handlers/__init__.py:6-15` still imports every handler, and
   those import `DataOutput`. `output.py` therefore still loads at server startup; what drops
   out is weasyprint and every writer submodule (codex S4). Making the handler imports lazy is
   out of scope.
4. **`PDFWriter.get_response` (`writers/pdf.py`).** The top-level `from weasyprint import HTML`
   moves into a private sync helper, `_render_pdf(html) -> bytes`. The helper does the
   `weasyprint` import, the `HTML(...)` construction and `write_pdf`, all **inside the worker
   thread**, so the first ~0.45 s import never runs on the event loop (codex S5).
   - The helper runs on a **bounded, dedicated `ThreadPoolExecutor`** via
     `loop.run_in_executor(_pdf_executor(), _render_pdf, html)`, not the default pool
     (§8 Q1, resolved by the user).
   - `_pdf_executor()` creates the executor lazily on first call, with
     `max_workers=PDF_RENDER_WORKERS` (new navconfig key in `querysource/conf.py`, default `4`)
     and `thread_name_prefix="qs-pdf"`.
   - A `ThreadPoolExecutor` does not depend on which event loop is running. Do **not** use a
     module-level `asyncio.Semaphore`, which binds to a single loop (codex S6). This follows
     the executor precedent at `querysource/interfaces/http.py:176`.

The design follows the lazy-import precedent already in the repo:
`querysource/queries/multi/destinations/__init__.py:76` (`__getattr__` + `__dir__`),
and `querysource/outputs/dt/factory.py:4` (`import_module`).

### Component Diagram
```
import querysource.services / queries.qs
        │
        ▼
queries/base.py ──→ outputs.dt (OutputFactory)
                        │  (runs outputs/__init__.py — now lazy: no output.py)
                        ▼
                    [stop — no writers, no weasyprint]

HTTP request ──→ handlers/{service,multi,qsurl}.py ──→ outputs.DataOutput   (PEP 562 → output.py)
                                                          │
                                        DataOutput.response() ──→ resolve_writer(ctype)
                                                          │         (import_module on first use, cache)
                                                          ▼
                                                 writers/<fmt>.py ──→ (pdf only) run_in_executor(_pdf_executor(), _render_pdf)
                                                                               └─→ import weasyprint (first PDF, in qs-pdf thread)
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `DataOutput.response` (`output.py:211`) | modifies | `WRITERS[...]` lookup + json fallback replaced by `resolve_writer(self.format)` |
| `WRITERS` (`output.py:39`) | modifies | Becomes `LazyWriterRegistry(dict)`; stored values are `str` specs or classes; `[]`/`.get()` always return a class |
| `querysource/outputs/__init__.py` | modifies | PEP 562 `__getattr__` for `DataOutput` |
| `querysource/outputs/writers/__init__.py` | modifies | PEP 562 `__getattr__` for the 13 writer names |
| `PDFWriter.get_response` (`pdf.py:48`) | modifies | Lazy weasyprint, rendered on a bounded `qs-pdf` executor |
| `querysource/conf.py` | modifies | New `PDF_RENDER_WORKERS = config.getint(..., fallback=4)`, following `conf.py:281` |
| `querysource/handlers/__init__.py:6-15` | unchanged (limits M4) | Eager handler imports keep `output.py` loaded at HTTP startup |
| handlers `service.py:24`, `multi.py:19`, `qsurl.py:12` | unchanged consumer | `from ..outputs import DataOutput` resolves via `__getattr__` |
| `tests/qsurl/test_error_envelope.py:59` | unchanged consumer | `monkeypatch.setitem(WRITERS, "json", Stub)` still works (class values are returned as-is) |

### Data Models
No new data models. `LazyWriterRegistry(dict)` stores `str` specs or classes, and its reads return classes.

### New Public Interfaces
```python
# querysource/outputs/output.py
class LazyWriterRegistry(dict):
    """Format → writer registry whose reads import writer classes on demand."""

def resolve_writer(ctype: str) -> type[AbstractWriter]:
    """Return the writer class registered for ``ctype``, importing it on first use."""

# querysource/conf.py
PDF_RENDER_WORKERS: int   # config.getint("PDF_RENDER_WORKERS", fallback=4)
```

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: PDF writer lazy + bounded off-loop | yes | `_render_pdf(html: str) -> bytes` sync helper, with a function-local `from weasyprint import HTML` inside the helper; `_pdf_executor()` creates a `ThreadPoolExecutor(max_workers=PDF_RENDER_WORKERS, thread_name_prefix="qs-pdf")` once; `get_response` does `buffer = await asyncio.get_running_loop().run_in_executor(_pdf_executor(), _render_pdf, result)`; `PDF_RENDER_WORKERS = config.getint("PDF_RENDER_WORKERS", fallback=4)` in `conf.py`, next to `HTTPCLIENT_MAX_WORKERS` (`conf.py:281`) | — |
| M2: Lazy `writers` package | yes | `_WRITER_MODULES: dict[str, str]` name→`.submodule`; `__getattr__` caches into `globals()`; `__dir__` returns `sorted(__all__)`; `AttributeError` message format matches `destinations/__init__.py:80` | — |
| M3: Lazy `WRITERS` registry | yes | `LazyWriterRegistry(dict)` overriding `__getitem__` and `get`; spec format `"<submodule>:<ClassName>"` relative to `querysource.outputs.writers`; `resolve_writer` checks membership before lookup; contract in §2 (cache write-back via `dict.__setitem__`, class passthrough, missing key → warn + json, any resolution failure → `ImportError`) | — |
| M4: Lazy `outputs` package | yes | `__getattr__("DataOutput")` → `from .output import DataOutput`; `__all__ = ('DataOutput',)` kept; `__dir__` | — |
| M5: Import-weight and behaviour tests | yes | Subprocess `python -c "import X, sys; print(...)"` probes; `WRITERS` snapshot fixture; test list in §4 | — |

### Module 1: PDF writer — lazy weasyprint and off-loop render
- **Path**: `querysource/outputs/writers/pdf.py`, `querysource/conf.py`
- **Responsibility**: Drop the module-level `weasyprint` import, and render the PDF (including the
  first weasyprint import) on a bounded worker pool so the event loop is not blocked.
- **Depends on**: none
- **Interface Skeleton**:
  ```python
  # querysource/outputs/writers/pdf.py  (modifies querysource/outputs/writers/pdf.py:7, :48-55)
  import asyncio
  from concurrent.futures import ThreadPoolExecutor
  from typing import Optional

  from ...conf import PDF_RENDER_WORKERS  # new key; ...conf import precedent: querysource/outputs/writers/csv.py:5

  _EXECUTOR: Optional[ThreadPoolExecutor] = None

  def _pdf_executor() -> ThreadPoolExecutor:
      """Return the process-wide PDF render executor, creating it on first use.

      Bounded to ``PDF_RENDER_WORKERS`` threads (prefix ``qs-pdf``) so parallel
      WeasyPrint renders cannot grow memory without limit. It does not depend
      on which event loop is running (no ``asyncio.Semaphore``).
      """

  def _render_pdf(html: str) -> bytes:
      """Render ``html`` to PDF bytes with WeasyPrint (sync, CPU-bound).

      Runs entirely in the worker thread: the ``from weasyprint import HTML``
      import (the first call pays ~0.45 s), the ``HTML(string=html)`` build and
      ``write_pdf`` all happen here, so none of them touch the event loop.
      """

  class PDFWriter(ReportWriter):  # verified: querysource/outputs/writers/pdf.py:10
      async def get_response(self) -> web.StreamResponse:  # verified: pdf.py:48
          """Render content, build the PDF on the ``qs-pdf`` executor, then download or stream it.

          Exceptions raised by ``_render_pdf`` propagate unchanged, into
          ``DataOutput.response``'s existing writer-error handling. Behaviour after
          the render (content_length, Content-Disposition, download vs
          stream_response) is unchanged.
          """
  ```
  ```python
  # querysource/conf.py  (adds next to HTTPCLIENT_MAX_WORKERS, verified: querysource/conf.py:281)
  PDF_RENDER_WORKERS = config.getint("PDF_RENDER_WORKERS", fallback=4)
  ```

### Module 2: Lazy `writers` package
- **Path**: `querysource/outputs/writers/__init__.py`
- **Responsibility**: Resolve `from querysource.outputs.writers import X` on demand, without importing
  every writer submodule.
- **Depends on**: none
- **Interface Skeleton**:
  ```python
  # querysource/outputs/writers/__init__.py  (modifies :1-17 — full rewrite of the eager imports)
  _WRITER_MODULES: dict[str, str] = {
      "jsonWriter": ".json", "TXTWriter": ".txt", "CSVWriter": ".csv",
      "ExcelWriter": ".excel", "HTMLWriter": ".html", "BokehWriter": ".bokeh",
      "PlotlyWriter": ".plotly", "TSVWriter": ".tsv", "ReportWriter": ".report",
      "PickleWriter": ".pickle", "TableWriter": ".table", "PDFWriter": ".pdf",
      "XMLWriter": ".xml",
  }
  __all__ = tuple(_WRITER_MODULES)

  def __getattr__(name: str) -> type:
      """Import and cache the writer class ``name`` on first access (PEP 562).

      Raises:
          AttributeError: if ``name`` is not a registered writer.
      """

  def __dir__() -> list[str]:
      """Expose the lazily-resolved writer names to ``dir()``."""
  ```
  The commented-out `ProfileWriter`/`EDAWriter`/`DescribeWriter`/`ClusterWriter` lines are kept as
  comments inside `_WRITER_MODULES`.

### Module 3: Lazy `WRITERS` registry and `resolve_writer`
- **Path**: `querysource/outputs/output.py`
- **Responsibility**: Replace the eager writer imports with string specs, and resolve them on first use.
- **Depends on**: M2 for the *performance* goal only. Resolving `"json:jsonWriter"` imports
  `querysource.outputs.writers.json`, which runs `writers/__init__.py`; with M2 that is cheap.
  Functional behaviour does not depend on M2.
- **Interface Skeleton**:
  ```python
  # querysource/outputs/output.py  (modifies :19-62 and :217-224)
  from importlib import import_module
  from typing import Optional, Union

  from .writers.abstract import AbstractWriter  # verified: querysource/outputs/writers/abstract.py:29

  class LazyWriterRegistry(dict):
      """Format → writer registry; stored values are ``"<submodule>:<Class>"`` specs or classes.

      Reads (``[]`` and ``get``) always return a class: a ``str`` spec is imported
      from ``querysource.outputs.writers.<submodule>`` on first access and cached
      back with ``dict.__setitem__``. Writes, ``monkeypatch.setitem``, ``in`` and
      ``len`` behave like a plain ``dict``. ``values()``/``items()`` expose the
      stored form (spec or class).
      """
      def __getitem__(self, ctype: str) -> type[AbstractWriter]:
          """Resolve and return the class for ``ctype``.

          Raises:
              KeyError: ``ctype`` is not registered.
              ImportError: the registered spec cannot be imported or names a
                  missing class (re-raised ``from`` the original error).
          """
      def get(self, ctype: str, default: Optional[type[AbstractWriter]] = None) -> Optional[type[AbstractWriter]]:
          """Like ``__getitem__`` but return ``default`` for a missing key."""

  WRITERS: LazyWriterRegistry = LazyWriterRegistry({
      "json": "json:jsonWriter", "table": "table:TableWriter",
      "txt": "txt:TXTWriter", "plain": "txt:TXTWriter",
      "csv": "csv:CSVWriter", "tsv": "tsv:TSVWriter",
      "excel": "excel:ExcelWriter", "xls": "excel:ExcelWriter",
      "xlsx": "excel:ExcelWriter", "xlsm": "excel:ExcelWriter",
      "ods": "excel:ExcelWriter", "html": "html:HTMLWriter",
      "bokeh": "bokeh:BokehWriter", "plotly": "plotly:PlotlyWriter",
      "pickle": "pickle:PickleWriter", "report": "report:ReportWriter",
      "pdf": "pdf:PDFWriter", "xml": "xml:XMLWriter",
  })  # same 18 keys as output.py:39-62; commented-out entries preserved as comments

  def resolve_writer(ctype: str) -> type[AbstractWriter]:
      """Return the writer class for ``ctype``, importing it on first use.

      - Checks ``ctype in WRITERS`` first. Only a missing key logs
        ``f'Invalid Writer {ctype}, default to JSON.'`` and returns ``WRITERS["json"]``,
        which is the current behaviour at output.py:219-224.
      - A registered key returns ``WRITERS[ctype]``: the lazily imported and
        cached class, or an injected class as is.

      Raises:
          ImportError: when a registered writer's module fails to import. It
              is never swallowed into the json fallback.
      """
  ```
  `DataOutput.response` calls `wt = resolve_writer(self.format)` in place of the
  `try: WRITERS[...] except KeyError` block. The warning text stays
  `f'Invalid Writer {self.format}, default to JSON.'`, emitted through the `QS.Output` logger.

### Module 4: Lazy `outputs` package
- **Path**: `querysource/outputs/__init__.py`
- **Responsibility**: Stop `import querysource.outputs.<sub>` from executing `output.py`.
- **Depends on**: none
- **Interface Skeleton**:
  ```python
  # querysource/outputs/__init__.py  (modifies :3)
  __all__ = ('DataOutput', )

  def __getattr__(name: str):
      """Lazily import ``DataOutput`` from ``.output`` (PEP 562)."""

  def __dir__() -> list[str]:
      """Expose ``DataOutput`` to ``dir()``."""
  ```

### Module 5: Import-weight and registry tests
- **Path**: `tests/unit/test_lazy_writers.py` (new)
- **Responsibility**: Regression-proof G1–G4.
- **Depends on**: M1, M2, M3, M4
- **Interface Skeleton**: see §4. Subprocess isolation is mandatory, because `sys.modules`
  is shared across the pytest session and other tests import `PDFWriter`.

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_render_pdf_imports_weasyprint_lazily` | M1 | `import querysource.outputs.writers.pdf` in a subprocess leaves `weasyprint` out of `sys.modules` |
| `test_pdf_render_runs_off_loop` | M1 | `_render_pdf` is patched with a blocking `time.sleep(0.3)` returning fixed bytes. A concurrent coroutine ticking every 10 ms makes progress while `get_response` awaits, and the render runs on a thread named `qs-pdf*` (codex S8) |
| `test_pdf_render_error_propagates` | M1 | `_render_pdf` raises; the exception propagates out of `get_response` unchanged, and a later render still works, i.e. the executor is not poisoned (codex S8) |
| `test_pdf_executor_bounded` | M1 | `_pdf_executor()` returns the same instance on repeated calls, and its `_max_workers == PDF_RENDER_WORKERS` |
| `test_pdf_render_workers_config_default` | M1 | `querysource.conf.PDF_RENDER_WORKERS` is an `int` and defaults to `4` |
| `test_writers_package_lazy_attr` | M2 | `from querysource.outputs.writers import CSVWriter` works; `PDFWriter` is absent from `sys.modules` until it is accessed (subprocess) |
| `test_writers_package_unknown_attr` | M2 | `querysource.outputs.writers.Nope` raises `AttributeError` |
| `test_writers_package_dir` | M2 | `set(dir(writers)) >= set(writers.__all__)` |
| `test_writers_package_star_import` | M2 | `from querysource.outputs.writers import *` binds all 13 names in `__all__` (subprocess) (codex S1/S7) |
| `test_writers_package_identity` | M2+M3 | `querysource.outputs.writers.PDFWriter is WRITERS["pdf"]` |
| `test_every_registered_writer_resolves` | M3 | For each of the 18 `WRITERS` keys, `WRITERS[k]` and `resolve_writer(k)` return an `AbstractWriter` subclass (catches broken specs early) |
| `test_registry_aliases_share_class` | M3 | `WRITERS["xls"] is WRITERS["xlsx"] is WRITERS["xlsm"] is WRITERS["ods"] is WRITERS["excel"]` and `WRITERS["plain"] is WRITERS["txt"]` (codex S2) |
| `test_registry_starts_as_specs` | M3 | In a subprocess: after `import querysource.outputs.output`, every `dict.__getitem__(WRITERS, k)` is a `str`, and `querysource.outputs.writers.pdf` is not in `sys.modules` (codex S9) |
| `test_resolve_writer_caches_class` | M3 | After `WRITERS[k]`, `dict.__getitem__(WRITERS, k)` is a class, not a `str` |
| `test_registry_get_resolves` | M3 | `WRITERS.get("csv")` returns the class; `WRITERS.get("nope")` returns `None` |
| `test_resolve_writer_class_passthrough` | M3 | `monkeypatch.setitem(WRITERS, "json", Stub)` → `resolve_writer("json") is Stub` |
| `test_resolve_writer_unknown_falls_back_to_json` | M3 | Unknown ctype → json writer and a warning is logged |
| `test_resolve_writer_import_error_propagates` | M3 | `WRITERS["x"] = "does_not_exist:Nope"` raises `ImportError`, not the json fallback. So does `"json:NoSuchClass"` (missing attribute). |
| `test_override_wins_after_cache` | M3 | After `WRITERS["json"]` has been resolved and cached, `monkeypatch.setitem(WRITERS, "json", Stub)` makes `resolve_writer("json")` return `Stub` (codex S9) |
| `test_outputs_package_lazy_dataoutput` | M4 | `import querysource.outputs.dt` in a subprocess leaves `querysource.outputs.output` out of `sys.modules`; `from querysource.outputs import DataOutput` still works |

### Integration Tests
| Test | Description |
|---|---|
| `test_entrypoints_do_not_import_weasyprint` | Parametrized over `querysource.queries.qs`, `querysource.outputs.dt`, `querysource.outputs.output`, `querysource.services`: a subprocess import leaves `weasyprint` out of `sys.modules` |
| `test_http_startup_loads_no_writer_submodules` | Subprocess `import querysource.services`: no `querysource.outputs.writers.<fmt>` module other than `abstract` is in `sys.modules`. `querysource.outputs.output` **may** be loaded, via `handlers/__init__.py` (codex S4) |
| existing `tests/qsurl/test_error_envelope.py` | Still passes unchanged (`WRITERS` monkeypatch contract) |
| existing `tests/unit/test_csv_writer_dataframe.py`, `tests/integration/test_csv_stream_response.py` | Still pass unchanged (direct submodule imports) |

### Test Data / Fixtures
```python
import subprocess, sys

import pytest

import querysource.outputs.output as output_module

@pytest.fixture
def fresh_writers(monkeypatch):
    """Restore the complete WRITERS mapping (specs + cached classes) after the test (codex S9)."""
    snapshot = dict(dict.items(output_module.WRITERS))
    yield output_module.WRITERS
    dict.clear(output_module.WRITERS)
    dict.update(output_module.WRITERS, snapshot)

def _imports_module(target: str, probe: str) -> bool:
    """Import ``target`` in a fresh interpreter and report whether ``probe`` got loaded."""
    code = f"import sys, {target}; print({probe!r} in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return out.stdout.strip().splitlines()[-1] == "True"
```

---

## 5. Acceptance Criteria

- [ ] AC1: `python -c "import sys, querysource.queries.qs; assert 'weasyprint' not in sys.modules"` succeeds.
      The same holds for `querysource.outputs.dt`, `querysource.outputs.output` and `querysource.services`. (G1)
- [ ] AC2: `import querysource.outputs.dt` does not load `querysource.outputs.output` (library mode, M4).
      `import querysource.services` loads no writer submodule other than `abstract`. It may still
      load `querysource.outputs.output`, via `handlers/__init__.py:6-15` (codex S4).
- [ ] AC3: `WRITERS` is still a module-level `dict` (a `LazyWriterRegistry` subclass) in
      `querysource.outputs.output`, with the same 18 keys. `WRITERS[k]` / `WRITERS.get(k)` return a
      class, including for the aliases `xls`/`xlsx`/`xlsm`/`ods`/`plain`, and
      `monkeypatch.setitem(WRITERS, k, cls)` still overrides a writer, even after caching. (C4, codex S2/S9)
- [ ] AC4: Resolved classes are cached. A *missing* format falls back to json with the unchanged
      warning. A *registered* format that fails to import, or names a missing class, raises
      `ImportError`. (M3, codex S3)
- [ ] AC5: All 13 names in `querysource.outputs.writers.__all__` import via
      `from querysource.outputs.writers import <Name>`, and via `import *`. (G3, codex S1)
- [ ] AC6: `PDFWriter.get_response` renders on the bounded `qs-pdf` executor (`PDF_RENDER_WORKERS`,
      default 4). The weasyprint import happens in the worker thread. Another coroutine keeps
      running during the render, and render errors propagate. A PDF response still has the same
      bytes, `content_length` and `Content-Disposition` handling. (G4, codex S5/S6/S8)
- [ ] AC7: `WeasyPrint>=65.0` is still in `[project].dependencies`; `pyproject.toml` and `uv.lock` are unchanged. (Non-goal)
- [ ] AC8: `pytest tests/unit/test_lazy_writers.py tests/qsurl tests/unit/test_csv_writer_dataframe.py tests/integration/test_csv_stream_response.py tests/unit/test_output_error.py -q` passes.
- [ ] AC9: `ruff check querysource/outputs querysource/conf.py tests/unit/test_lazy_writers.py` is clean. Function-local
      imports carry `# noqa: PLC0415` only if that rule is ever enabled; it is not in the current `select`.
- [ ] AC10: Startup improvement is recorded in the task completion note: `python -X importtime -c "import querysource.queries.qs"`
      before and after, with total µs and weasyprint share. Informational, not a hard threshold,
      because it depends on the host.

---

## 6. Codebase Contract

### Verified Imports
```python
from querysource.outputs import DataOutput                    # verified: querysource/outputs/__init__.py:3
from querysource.outputs.output import DataOutput, WRITERS    # verified: querysource/outputs/output.py:39, :64
from querysource.outputs.writers.abstract import AbstractWriter  # verified: querysource/outputs/writers/abstract.py:29
from querysource.outputs.writers.report import ReportWriter   # verified: querysource/outputs/writers/pdf.py:8
from querysource.outputs.dt import OutputFactory              # verified: querysource/outputs/dt/__init__.py:1; used by querysource/queries/base.py:14
from navconfig.logging import logging                         # verified: querysource/outputs/output.py:8
from ...conf import CSV_DEFAULT_DELIMITER                     # relative-conf import precedent inside writers: querysource/outputs/writers/csv.py:5
```

```python
# querysource/conf.py — config int pattern to copy
HTTPCLIENT_MAX_WORKERS = config.getint("HTTPCLIENT_MAX_WORKERS", fallback=1)   # verified: conf.py:281
# querysource/interfaces/http.py — executor precedent
self._executor = ThreadPoolExecutor(max_workers=int(HTTPCLIENT_MAX_WORKERS))    # verified: http.py:176-178
# querysource/handlers/__init__.py — eager handler imports (reason AC2 allows output.py in HTTP mode)
from .describe import QueryDescribe   # verified: handlers/__init__.py:6 (… through :15 QueryService etc.)
```

### Existing Class Signatures
```python
# querysource/outputs/output.py
WRITERS = {...}                                   # lines 39-62, 18 keys (json, table, txt, plain, csv, tsv,
                                                  #   excel, xls, xlsx, xlsm, ods, html, bokeh, plotly,
                                                  #   pickle, report, pdf, xml)
class DataOutput:                                 # line 64
    def __init__(self, request: web.Request, query: Union[AbstractQuery, DataFrame, list],
                 ctype: str = 'json', slug: str = None, **kwargs) -> None:   # line 68
        self.logger = logging.getLogger('QS.Output')                         # line 79
        self.format = ctype                                                   # line 102
    async def response(self):                                                 # line 211
        # 217-224: try: wt = WRITERS[self.format] except KeyError: warning + WRITERS['json']
        # 225-234: writer = wt(request=..., resultset=..., filename=..., response_type=...,
        #          download=..., compression=..., ctype=self.format, **self.writer_options)

# querysource/outputs/writers/abstract.py
class AbstractWriter(ABC):                        # line 29
    def __init__(...)                             # line 40
    async def get_response(self) -> Union[web.StreamResponse, Any]:  # line 151
    async def get_result(self):                   # line 294

# querysource/outputs/writers/report.py
class ReportWriter(AbstractWriter):                # line 185
    async def render_content(self) -> str:        # line 248

# querysource/outputs/writers/pdf.py
from weasyprint import HTML                       # line 7  (to be removed)
class PDFWriter(ReportWriter):                    # line 10
    async def get_response(self) -> web.StreamResponse:   # line 48
        output = BytesIO()                                # 49
        result = await self.render_content()              # 50
        response = await self.response(self.response_type)  # 51
        HTML(string=result).write_pdf(output)             # 53
        output.seek(0); buffer = output.getvalue()        # 54-55
        # 57-66: content_length, download → prepare/write/write_eof, else stream_response
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `resolve_writer` | `WRITERS` | dict lookup + write-back | `output.py:39` |
| `DataOutput.response` | `resolve_writer` | function call replacing the lookup | `output.py:217-224` |
| `outputs.__getattr__` | `.output.DataOutput` | PEP 562 | `outputs/__init__.py:3` |
| `writers.__getattr__` | `.<submodule>.<Class>` | `importlib.import_module(sub, __name__)` | `writers/__init__.py:1-17` |
| `_render_pdf` | `weasyprint.HTML.write_pdf` | function-local import, run with `loop.run_in_executor(_pdf_executor(), ...)` | `pdf.py:7, :53` |
| `_pdf_executor` | `PDF_RENDER_WORKERS` | `from ...conf import PDF_RENDER_WORKERS` | new key next to `conf.py:281` |
| precedent | `__getattr__`/`__dir__` pattern | copy style | `querysource/queries/multi/destinations/__init__.py:76-86` |

### Does NOT Exist (Anti-Hallucination)
- ~~`querysource.outputs.writers.WRITERS`~~: the registry lives only in `querysource.outputs.output`.
- ~~`querysource.outputs.registry`~~ / ~~`get_writer()`~~: no writer registry module or getter exists. `resolve_writer` is new.
- ~~`PDFWriter.render_pdf`~~: `PDFWriter` defines no render method. The new helper is the module-level `_render_pdf`.
- ~~`asyncio` import in `pdf.py`~~: not currently imported (`pdf.py:1-8`); M1 adds it.
- ~~`weasyprint` usage outside `pdf.py`~~: grep finds none in `querysource/` or `tests/`.
- ~~A pytest marker for PDF tests~~: `pytest.ini` defines only `perf`.
- ~~`PDF_RENDER_WORKERS`~~: does not exist yet (grep finds none); M1 adds it to `conf.py`.
- ~~`LazyWriterRegistry`~~, ~~`resolve_writer`~~, ~~`_pdf_executor`~~, ~~`_render_pdf`~~: all new in this feature.

### Edit Sites (Blueprint Anchors)

Verified against: `85cd6f8`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/outputs/writers/pdf.py` | MODIFY | `from weasyprint import HTML` | `pdf.py:7` | 1 |
| `querysource/outputs/writers/pdf.py` | MODIFY | `        HTML(string=result).write_pdf(output)` | `pdf.py:53` | 1 |
| `querysource/outputs/writers/__init__.py` | MODIFY | `from .json import jsonWriter` (whole file 1-17 replaced) | `writers/__init__.py:1` | 1 |
| `querysource/outputs/output.py` | MODIFY | `from .writers import (` (block 19-37 removed) | `output.py:19` | 1 |
| `querysource/outputs/output.py` | MODIFY | `WRITERS = {` (block 39-62 rewritten) | `output.py:39` | 1 |
| `querysource/outputs/output.py` | MODIFY | `                wt = WRITERS[self.format]` (block 217-224) | `output.py:218` | 1 |
| `querysource/outputs/__init__.py` | MODIFY | `from .output import DataOutput` | `outputs/__init__.py:3` | 1 |
| `querysource/conf.py` | MODIFY | `HTTPCLIENT_MAX_WORKERS = config.getint("HTTPCLIENT_MAX_WORKERS", fallback=1)` (insert after) | `conf.py:281` | 1 |
| `tests/unit/test_lazy_writers.py` | CREATE | — | — | — |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- PEP 562 `__getattr__` + `__dir__` exactly as in `querysource/queries/multi/destinations/__init__.py:76-86`,
  including the `AttributeError(f"module {__name__!r} has no attribute {name!r}")` message.
- `importlib.import_module(f".{sub}", package="querysource.outputs.writers")`, like `outputs/dt/factory.py:4`.
- Heavy imports belong at the call site. This is how `bokeh.py:37-41`, `plotly.py:79-80` and `report.py:10-13` already work.
- Logging through the `QS.Output` logger (`output.py:79`); no `print`.
- Bounded thread pool like `querysource/interfaces/http.py:176`; config key like `conf.py:281`.
- Google-style docstrings and type hints on every new function.

### Known Risks / Gotchas
- **Errors surface later.** A broken writer module now fails on its first request instead of at
  startup. *Mitigation*: `test_every_registered_writer_resolves` imports every spec in CI.
- **Test isolation.** `sys.modules` is shared across the pytest session, and other tests import
  `PDFWriter`. Every "was not imported" assertion **must** run in a subprocess.
- **Cache write-back vs. monkeypatch.** Reading `WRITERS[k]` writes the class into `WRITERS`.
  `monkeypatch.setitem` restores whatever value it saw (spec or class) on teardown, and both are
  valid. Tests must not assume a key's stored state across tests. Tests that assert the
  initial string specs run in a subprocess, and tests that mutate the registry use the
  `fresh_writers` fixture (codex S9).
- **Membership before lookup.** `resolve_writer` must test `ctype in WRITERS` before reading.
  Catching `KeyError` around `WRITERS[ctype]` would misroute a `KeyError` raised *inside* a
  writer module's import into the json fallback (codex S3).
- **Thread offload.** `write_pdf` runs on the dedicated `qs-pdf` executor, which is capped at
  `PDF_RENDER_WORKERS`. A WeasyPrint document is built per call and shares no state. Excess
  PDF requests queue in the executor rather than growing memory. The executor is process-wide
  and loop-agnostic, so it is safe across pytest event loops and gunicorn workers (one per
  process). It is never shut down explicitly: its threads are idle and exit with the process.
- **Chart work stays on the loop.** `ReportWriter.render_content` still builds its charts
  synchronously before the PDF step (non-goal; codex S5).
- **`isinstance`/identity checks.** The class obtained through `querysource.outputs.writers.PDFWriter`
  and through `WRITERS["pdf"]` must be the same object. Both come from the same submodule via
  `sys.modules`, so they are.
- **The first PDF request pays the ~0.45 s weasyprint import** inside the worker thread, not on the event loop.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `WeasyPrint` | `>=65.0` (unchanged) | PDF rendering; now imported lazily |

---

## 8. Open Questions

- [x] Scope: PDF-only, lazy registry for all writers, or also decouple the outputs package?
      *Resolved in proposal*: "Lazy registry, all". The lazy `outputs/__init__.py` is included
      because it is what keeps the library path from loading `output.py` (M4).
- [x] Keep WeasyPrint as a hard dependency or move it to an extra? *Resolved in proposal*:
      "Keep hard dependency" (AC7).
- [x] Include the blocking `write_pdf` fix? *Resolved in proposal*: "Include to_thread fix" (M1, AC6).
      It is implemented with the bounded executor from Q1 rather than the default `to_thread` pool.
- [x] Q1: Should concurrent PDF rendering be capped now that it runs off-loop (codex S6)?
      *Resolved by the user during /sdd-spec*: "Bounded executor". A dedicated, lazily created
      `ThreadPoolExecutor(max_workers=PDF_RENDER_WORKERS)` (navconfig, default 4) and
      `loop.run_in_executor`. There is no module-level `asyncio.Semaphore` (M1, AC6).

---

## 9. Design Research Cross-Check

> Independent design opinion from the `codex` seat over the accepted proposal (never over this spec).
> Model: `gpt-5.6-luna` (codex-cli 0.159.0, reasoning high) · Status: completed · Transcript: `sdd/state/FEAT-154/design_research/`

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|
| S1 | Define the lazy package export contract explicitly (architecture) | CONFIRM | Verified: `writers/__init__.py` has no `__all__` today. The spec already had `__all__`/`__dir__`/globals caching; a star-import test was added | §3 M2, §4, AC5 |
| S2 | Preserve direct WRITERS lookup compatibility (api) | CONFIRM | Only `output.py` and one test read `WRITERS` in-repo, but it is module-public. `LazyWriterRegistry(dict)` makes `[]`/`.get()` always return a class; alias test added | §2 Overview, §3 M3, AC3 |
| S3 | Keep known-format import failures distinct from fallback (risk) | CONFIRM | Membership check before lookup; any resolution failure becomes `ImportError` | §2, §3 M3, §7, AC4 |
| S4 | Package-level lazy export doesn't remove output.py from HTTP startup (architecture) | CONFIRM | Verified `handlers/__init__.py:6-15` imports all handlers eagerly; the HTTP invariant was narrowed to "no weasyprint, no writer submodules" | §2 item 3, AC2, §4 |
| S5 | Move the first WeasyPrint import into the worker thread (risk) | CONFIRM | `_render_pdf` does import + build + write in-thread; `render_content`'s synchronous chart work is recorded as a non-goal | §1 Non-Goals, §2 item 4, §3 M1, §7 |
| S6 | Resolve PDF concurrency before enabling parallel offload (risk) | ESCALATE → resolved | Asked the user: "Bounded executor" (`PDF_RENDER_WORKERS`, default 4, no module-level `asyncio.Semaphore`). Precedent verified at `interfaces/http.py:176` | §8 Q1 [x], §3 M1, AC6 |
| S7 | Test every public import form in isolated processes (testing) | CONFIRM | Added star-import, identity, alias and HTTP-startup subprocess tests | §4 |
| S8 | Test the actual off-loop boundary and failure propagation (testing) | CONFIRM | Added a progress-while-rendering test, an error-propagation test and a no-poisoned-executor test. There is no limiter state to leak on cancellation (it is an executor, not a semaphore), so cancellation needs no separate test | §4, AC6 |
| S9 | Isolate registry-cache state between tests (testing) | CONFIRM | `fresh_writers` snapshot fixture; initial-spec assertions run in a subprocess; override-after-cache test | §4, §7 |

Summary: **8** confirmed · **0** rejected · **1** escalated (S6, resolved by the user as §8 Q1).
All 16 cited `affected_paths` passed the containment and `test -e` checks.

---

## Worktree Strategy

- **Isolation**: one feature worktree, `feat-FEAT-154-lazy-import-writers`, based on `origin/dev`;
  each task runs in its own sub-worktree inside it.
- **Module dependency graph**:
  - M1, M2, M3 and M4 have no code edges between them and can run concurrently.
    M1 adds `PDF_RENDER_WORKERS` to `conf.py` and uses it itself.
    M3 imports `AbstractWriter` from `writers/abstract.py` (unchanged), not from M2.
  - M5 → M1, M2, M3, M4, because the tests assert the combined G1 behaviour.
- **Shared files**: none. M1 owns `pdf.py` + `conf.py`; M2, M3 and M4 each own one file; M5 owns the new test file.
- **Exclusive resources**: none. There is no Cython/Rust rebuild, no lockfile change and no migration.
- **Cross-feature dependencies**: none.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-29 | Claude Code for Jesus Lara | Initial draft from proposal FEAT-154 |
| 0.2 | 2026-09-29 | Claude Code for Jesus Lara | Folded codex design research (S1–S9); Q1 resolved: bounded `qs-pdf` executor |
