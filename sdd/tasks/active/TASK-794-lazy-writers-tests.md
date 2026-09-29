# TASK-794: Import-weight, registry and off-loop PDF tests

**Feature**: FEAT-154 — Lazy-import output writers
**Spec**: `sdd/specs/lazy-import-writers.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-790, TASK-791, TASK-792, TASK-793
**Assigned-to**: unassigned

---

## Context

This task implements spec §3 **Module 5** and the whole §4 Test Specification. The tests keep
goals G1–G4 from regressing: weasyprint is not loaded at startup, the writers load lazily, the
`WRITERS` contract holds, and PDF rendering runs off the event loop. It also records the AC10 import-time measurement.

It runs last because each test asserts behaviour that one of TASK-790…793 creates.

---

## Scope

- Create `tests/unit/test_lazy_writers.py` with every test listed in spec §4 Unit Tests and Integration Tests.
  That is 20 unit tests plus 2 integration tests, one of them parametrized.
- Use the `fresh_writers` fixture and the `_imports_module` subprocess helper from spec §4 "Test Data / Fixtures".
- Record the AC10 measurement in the Completion Note: `python -X importtime -c "import querysource.queries.qs"`
  before (on `origin/dev`) and after (in the worktree), with the total µs and weasyprint's share.

**NOT in scope**: changing any `querysource/` file. If a test fails because of a bug in
TASK-790…793, report it in the Completion Note and fix it only if the fix fits in that task's file.
Also out of scope: edits to `conftest.py` and `pytest.ini`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/unit/test_lazy_writers.py` | CREATE | All FEAT-154 tests (spec §4) |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import querysource.outputs.output as output_module          # module exists: querysource/outputs/output.py
from querysource.outputs.output import WRITERS, resolve_writer, LazyWriterRegistry   # created by TASK-792
from querysource.outputs.writers.abstract import AbstractWriter   # verified: querysource/outputs/writers/abstract.py:29
from querysource.outputs.writers import pdf as pdf_module         # querysource/outputs/writers/pdf.py
from querysource.outputs.writers.pdf import PDFWriter, _render_pdf, _pdf_executor   # _render_pdf/_pdf_executor created by TASK-790
from querysource.conf import PDF_RENDER_WORKERS                   # created by TASK-790
import querysource.outputs.writers as writers_pkg                 # __all__/__getattr__/__dir__ created by TASK-791
```

### Existing Signatures to Use
```python
# querysource/outputs/writers/report.py:192-235 — ReportWriter.__init__ needs request.app['templating']
#   (raises RuntimeError otherwise), so a fake request must expose `.app = {"templating": object()}`
#   and `.headers = {}`.
# querysource/outputs/writers/abstract.py:90  async def response(self, response_type: str = 'web', data: str = None)
# querysource/outputs/writers/abstract.py:157 async def stream_response(self, response, data)
# querysource/outputs/writers/report.py:248   async def render_content(self) -> str
# Stubbing precedent: tests/unit/test_csv_writer_dataframe.py:25-40 — construct the writer, then
#   replace `writer.response` / `writer.stream_response` with async fakes on the instance.
# WRITERS monkeypatch contract: tests/qsurl/test_error_envelope.py:59
```

The logger name used for the fallback warning is `QS.Output` (`output.py:79`). Assert it with `caplog`.

### Does NOT Exist
- ~~A pytest marker for PDF tests~~: `pytest.ini` defines only `perf`. Do not add a marker.
- ~~`querysource.outputs.writers.WRITERS`~~: the registry is `querysource.outputs.output.WRITERS`.
- ~~`PDFWriter.render_pdf`~~: patch the module-level `querysource.outputs.writers.pdf._render_pdf`.
- ~~A shared subprocess helper in `tests/conftest.py`~~: define `_imports_module` in this file.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "tests/unit/test_lazy_writers.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/outputs/writers/abstract.py#AbstractWriter",
    "sym:querysource/outputs/writers/pdf.py#PDFWriter",
    "sym:querysource/outputs/output.py#DataOutput"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **Every "X is not in sys.modules" assertion runs in a subprocess**, through `_imports_module` or a
  `subprocess.run([sys.executable, "-c", ...])`. `sys.modules` is shared across the pytest session
  and other tests import `PDFWriter` (spec §7 "Test isolation").
- **Every test that mutates `WRITERS` uses `fresh_writers`**, or `monkeypatch.setitem`, which
  restores automatically. Never assume that a key is still a `str` spec in-process (codex S9).
- **Off-loop test**: monkeypatch `pdf_module._render_pdf` with a function that calls `time.sleep(0.3)`,
  records `threading.current_thread().name`, and returns `b"%PDF-fake"`. Run `get_response()`
  together with a ticker coroutine (`asyncio.sleep(0.01)` in a loop that counts ticks). Assert:
  - there are at least 10 ticks;
  - the recorded thread name starts with `"qs-pdf"`;
  - the fake `stream_response` received `b"%PDF-fake"`.

  `get_response` looks `_render_pdf` up in its module globals at call time, so patching the module attribute works.
- **Error test**: patch `_render_pdf` to raise `ValueError("boom")`, and assert that `get_response` raises `ValueError`.
  Then restore it, patch in a working fake, and assert that a second render succeeds.
- The `time.sleep` inside a sync function run by the executor is fine: ruff `ASYNC` flags only
  calls inside `async def`, and `tests/**` already ignores `ASYNC230`/`ASYNC240`.
- `asyncio_mode = auto` (`pytest.ini`): `async def test_*` needs no decorator.

---

## Implementation Blueprint

### Steps (in order)
1. Check that TASK-790…793 are `done` in `sdd/tasks/index/lazy-import-writers.json`, then confirm the new symbols exist:
   `grep -n "_render_pdf\|_pdf_executor" querysource/outputs/writers/pdf.py`,
   `grep -n "class LazyWriterRegistry\|def resolve_writer" querysource/outputs/output.py`.
   *Why*: these tests import those symbols.
2. Before writing anything, measure AC10 on `origin/dev` for the Completion Note. Run it in the
   main repo or with `git stash`, never on the worktree branch:
   `python -X importtime -c "import querysource.queries.qs" 2>&1 | tail -1` and `... | grep -i weasyprint | sort -t'|' -k2 -n | tail -1`.
3. Write block A and block B below into `tests/unit/test_lazy_writers.py`, then complete every `FILL IN`.
4. Run the Validation Commands and `ruff check tests/unit/test_lazy_writers.py`.
5. Measure AC10 again in the worktree and write both numbers into the Completion Note.

### `tests/unit/test_lazy_writers.py` (CREATE) — block A: header, fixtures, M1 tests
```python
"""FEAT-154 — lazy-import output writers: import weight, registry contract, off-loop PDF."""
import asyncio
import subprocess
import sys
import threading
import time

import pytest

import querysource.outputs.output as output_module
import querysource.outputs.writers as writers_pkg
from querysource.outputs.output import WRITERS, LazyWriterRegistry, resolve_writer
from querysource.outputs.writers import pdf as pdf_module
from querysource.outputs.writers.abstract import AbstractWriter

ENTRYPOINTS = [
    "querysource.queries.qs",
    "querysource.outputs.dt",
    "querysource.outputs.output",
    "querysource.services",
]


@pytest.fixture
def fresh_writers():
    """Restore the complete WRITERS mapping (specs + cached classes) after the test (codex S9)."""
    snapshot = dict(dict.items(output_module.WRITERS))
    yield output_module.WRITERS
    dict.clear(output_module.WRITERS)
    dict.update(output_module.WRITERS, snapshot)


def _run(code: str) -> str:
    """Run ``code`` in a fresh interpreter and return its last stdout line."""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return out.stdout.strip().splitlines()[-1]


def _imports_module(target: str, probe: str) -> bool:
    """Import ``target`` in a fresh interpreter and report whether ``probe`` got loaded."""
    return _run(f"import sys, {target}; print({probe!r} in sys.modules)") == "True"


class _Req:
    headers: dict = {}
    app: dict = {"templating": object()}


def _pdf_writer(sink: list) -> "pdf_module.PDFWriter":
    """Build a PDFWriter with render/response/stream stubbed; ``sink`` receives the streamed bytes."""
    writer = pdf_module.PDFWriter(request=_Req(), resultset=[], filename="r", ctype="pdf")
    # FILL IN: replace writer.render_content (async → "<p>x</p>"), writer.response
    #          (async → object with `headers` dict and `content_length`), writer.stream_response
    #          (async: append data to `sink`, return data) — pattern: tests/unit/test_csv_writer_dataframe.py:25-40
    return writer


def test_render_pdf_imports_weasyprint_lazily():
    assert not _imports_module("querysource.outputs.writers.pdf", "weasyprint")


async def test_pdf_render_runs_off_loop(monkeypatch):
    # FILL IN: fake _render_pdf (time.sleep(0.3), record thread name, return b"%PDF-fake");
    #          run get_response() alongside a 10 ms ticker; assert ticks >= 10,
    #          thread name startswith "qs-pdf", sink == [b"%PDF-fake"] — bounded by AC6
    ...


async def test_pdf_render_error_propagates(monkeypatch):
    # FILL IN: _render_pdf raises ValueError → get_response raises ValueError;
    #          then a working fake renders fine (executor not poisoned) — bounded by AC6
    ...


def test_pdf_executor_bounded():
    from querysource.conf import PDF_RENDER_WORKERS

    ex = pdf_module._pdf_executor()
    assert ex is pdf_module._pdf_executor()
    assert ex._max_workers == PDF_RENDER_WORKERS


def test_pdf_render_workers_config_default():
    from querysource.conf import PDF_RENDER_WORKERS

    assert isinstance(PDF_RENDER_WORKERS, int)
    # FILL IN: assert the default is 4 when the env var is unset — run in a subprocess with
    #          PDF_RENDER_WORKERS removed from env (navconfig reads env) — bounded by AC6
```

### `tests/unit/test_lazy_writers.py` (CREATE, continued) — block B: M2, M3, M4 and integration
```python
def test_writers_package_lazy_attr():
    assert _run(
        "import sys; from querysource.outputs.writers import CSVWriter; "
        "print('querysource.outputs.writers.pdf' in sys.modules)"
    ) == "False"


def test_writers_package_unknown_attr():
    with pytest.raises(AttributeError):
        writers_pkg.Nope  # noqa: B018


def test_writers_package_dir():
    assert set(dir(writers_pkg)) >= set(writers_pkg.__all__)


def test_writers_package_star_import():
    # FILL IN: subprocess `from querysource.outputs.writers import *` then print that all 13
    #          __all__ names are bound in globals() — bounded by AC5
    ...


def test_writers_package_identity():
    assert writers_pkg.PDFWriter is WRITERS["pdf"]


def test_every_registered_writer_resolves():
    assert len(WRITERS) == 18
    for key in list(WRITERS):
        assert issubclass(WRITERS[key], AbstractWriter), key
        assert resolve_writer(key) is WRITERS[key]


def test_registry_aliases_share_class():
    assert WRITERS["xls"] is WRITERS["xlsx"] is WRITERS["xlsm"] is WRITERS["ods"] is WRITERS["excel"]
    assert WRITERS["plain"] is WRITERS["txt"]


def test_registry_starts_as_specs():
    # FILL IN: subprocess: import querysource.outputs.output; print that every
    #          dict.__getitem__(WRITERS, k) is str AND 'querysource.outputs.writers.pdf' not in sys.modules — codex S9
    ...


def test_resolve_writer_caches_class(fresh_writers):
    dict.__setitem__(fresh_writers, "csv", "csv:CSVWriter")
    cls = fresh_writers["csv"]
    assert dict.__getitem__(fresh_writers, "csv") is cls


def test_registry_get_resolves():
    assert issubclass(WRITERS.get("csv"), AbstractWriter)
    assert WRITERS.get("nope") is None
    assert isinstance(WRITERS, LazyWriterRegistry) and isinstance(WRITERS, dict)


# FILL IN (each bounded by AC3/AC4, spec §4 table):
#   test_resolve_writer_class_passthrough(monkeypatch)  — setitem json → Stub; resolve_writer("json") is Stub
#   test_resolve_writer_unknown_falls_back_to_json(caplog) — returns WRITERS["json"]; warning
#       "Invalid Writer nope, default to JSON." on logger "QS.Output"
#   test_resolve_writer_import_error_propagates(fresh_writers) — "does_not_exist:Nope" and
#       "json:NoSuchClass" both raise ImportError from resolve_writer
#   test_override_wins_after_cache(monkeypatch) — WRITERS["json"] first, then setitem Stub → Stub
#   test_outputs_package_lazy_dataoutput — subprocess: querysource.outputs.dt leaves
#       querysource.outputs.output unloaded; `from querysource.outputs import DataOutput` works
#   @pytest.mark.parametrize("target", ENTRYPOINTS) test_entrypoints_do_not_import_weasyprint
#   test_http_startup_loads_no_writer_submodules — subprocess `import querysource.services`; no
#       sys.modules key startswith "querysource.outputs.writers." other than "...writers.abstract"
```
**Why this shape**: the helpers and the tests that need no judgement are complete. Stub wiring
and subprocess scripts are left as `FILL IN`, bounded by the AC each test proves. The file is split
into two blocks to respect the ~80-line cap. Write them one after the other into the same file.

### FILL IN checklist
- [ ] `_pdf_writer`: stub `render_content`, `response` and `stream_response`. Bounded by the precedent in `test_csv_writer_dataframe.py`.
- [ ] `test_pdf_render_runs_off_loop`: prove progress on the loop, the `qs-pdf` thread name and the streamed bytes. Bounded by AC6.
- [ ] `test_pdf_render_error_propagates`: the error propagates and the executor still works afterwards. Bounded by AC6.
- [ ] `test_pdf_render_workers_config_default`: default of 4, checked in a subprocess with the env var unset. Bounded by AC6.
- [ ] `test_writers_package_star_import`. Bounded by AC5.
- [ ] `test_registry_starts_as_specs`. Bounded by AC3 and codex S9.
- [ ] The seven remaining tests in the block B comment, one of them parametrized. Bounded by AC1–AC4.

---

## Acceptance Criteria

- [ ] All 20 unit tests and 2 integration tests from spec §4 exist and pass. The weasyprint entrypoint test is parametrized over 4 entrypoints.
- [ ] Spec AC8 passes: `pytest tests/unit/test_lazy_writers.py tests/qsurl tests/unit/test_csv_writer_dataframe.py tests/integration/test_csv_stream_response.py tests/unit/test_output_error.py -q`
- [ ] `ruff check tests/unit/test_lazy_writers.py` is clean (AC9)
- [ ] The Completion Note records AC10 before and after: total µs and weasyprint's share

---

## Validation Commands

- `pytest tests/unit/test_lazy_writers.py -q`
- `pytest tests/qsurl/test_error_envelope.py -q`
- `pytest tests/unit/test_csv_writer_dataframe.py -q`
- `pytest tests/integration/test_csv_stream_response.py -q`
- `pytest tests/unit/test_output_error.py -q`

---

## Test Specification

This task *is* the test specification. See spec §4.

---

## Agent Instructions

1. Work in the feature worktree:
   `python -m scripts.sdd.ensure_worktree --slug lazy-import-writers --feature-id FEAT-154`
2. Read spec §4 and §7 ("Test isolation", "Cache write-back vs. monkeypatch").
3. Check that TASK-790, TASK-791, TASK-792 and TASK-793 are all `"done"`.
4. Verify the Codebase Contract, especially that the new symbols exist.
5. Set the status to `"in-progress"` in `sdd/tasks/index/lazy-import-writers.json`.
6. Implement from the blueprint.
7. Run the Validation Commands and ruff, and take the AC10 measurement.
8. Commit only `tests/unit/test_lazy_writers.py`.
9. Close with `scripts/sdd/close_task.sh TASK-794 lazy-import-writers verified`.
10. Fill in the Completion Note, including the AC10 numbers.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**AC10 import time** (`python -X importtime -c "import querysource.queries.qs"`):
- before (origin/dev): total ___ µs, weasyprint ___ µs (___ %)
- after (worktree):   total ___ µs, weasyprint ___ µs

**Deviations from spec**: none
