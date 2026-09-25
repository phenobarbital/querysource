# TASK-774: Client-safe `detail` in the error envelope + `QSUrlError` pass-through in `DataOutput`

**Feature**: FEAT-152 — qsurl: HTSQL-style URL query dialect (chumsky 0.13 + PyO3)
**Spec**: `sdd/specs/qsurl-parser.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-765
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 10 (envelope half), AC7. The brainstorm resolved that qsurl 400s use the
existing `AbstractHandler.Error` envelope with the error object in its detail field. Today
`build_error_payload` only emits `detail` when `debug=True` (`querysource/utils/errors.py:147-149`)
and `Error` only forwards `message` publicly in debug (`handlers/abstract.py:173`), so in
production the structured error would vanish. This task adds an explicit, opt-in,
caller-asserted client-safe `detail` channel that is emitted in every mode, and makes
`DataOutput.response` re-raise `QSUrlError` so the handler can answer 400 with it. Every
other error body must stay byte-identical.

---

## Scope

- `build_error_payload(..., public_detail: Optional[dict] = None)`: when given, `payload["detail"] = public_detail` in every mode (replaces the debug `str(exception)` detail; `trace` stays debug-only).
- `AbstractHandler.Error(..., detail: dict | None = None)`: when given, pass `public_message=message` (regardless of `self.debug`) and `public_detail=detail`.
- `DataOutput.response`: `except QSUrlError: raise` as the first `except` after `await writer.get_result()`.
- Tests for the new behaviour; existing envelope tests must stay green unchanged.

**NOT in scope**: `NotFound` / `Except`; the qsurl handler (TASK-775).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/utils/errors.py` | MODIFY | `public_detail` parameter |
| `querysource/handlers/abstract.py` | MODIFY | `Error(detail=...)` |
| `querysource/outputs/output.py` | MODIFY | Re-raise `QSUrlError` |
| `tests/test_error_formatter.py` | MODIFY | Append `public_detail` cases |
| `tests/qsurl/test_error_envelope.py` | CREATE | `Error(detail=...)` and `DataOutput` re-raise tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.utils.errors import GENERIC_MESSAGES, build_error_payload   # verified: querysource/utils/errors.py:22,43
from querysource.handlers.abstract import AbstractHandler                    # verified: querysource/handlers/abstract.py:32
from querysource.outputs.output import DataOutput                            # verified: querysource/outputs/output.py:63
from querysource.qsurl.errors import QSUrlError                              # TASK-765
```

### Existing Signatures to Use
```python
# querysource/utils/errors.py
def build_error_payload(
    *,
    category: str,
    status: int,
    exception: Optional[BaseException] = None,
    debug: bool = False,
    logger: Optional[logging.Logger] = None,
    public_message: Optional[str] = None,          # line 50
) -> dict[str, Any]:                              # line 51
    ...
    payload: dict = {"error": safe_message, "status": status, "error_id": error_id}   # 142-146
    if debug:                                      # line 147
        payload["detail"] = detail                 # line 148
        payload["trace"] = trace                   # line 149
    return payload                                 # line 151

# querysource/handlers/abstract.py
class AbstractHandler(BaseHandler):               # line 32
    debug: bool = DEBUG                           # line 36
    def Error(self, reason: dict = None, message: str = None, exception: BaseException = None,
              stacktrace: str = None,
              code: int = 400                     # line 139
    ) -> HTTPException:                           # line 140
        # category mapping 160-165; payload = build_error_payload(category=..., status=code, exception=exception,
        #   debug=self.debug, logger=self.logger, public_message=message if self.debug else None)   # 167-174
        # args = {"reason": payload["error"], "text": self._json.dumps(payload), "headers": {...}, "content_type": "application/json"}
        # code 400 → web.HTTPBadRequest(**args)   (returned, caller raises it)

# querysource/outputs/output.py
from ..exceptions import (...)                    # line 11 (multi-line import block)
async def response(self):                         # line 210
    ...
    try:
        await writer.get_result()                 # line 236
    except (NoDataFound, DataNotFound) as err:    # line 237 → 204
    ...
    except (DriverError, QueryException) as err:  # line 259 — would swallow QSUrlError (a QueryException)
```

```python
# tests/test_error_formatter.py:17-23 — pattern
def test_production_minimal():
    payload = build_error_payload(category="query_error", status=400)
    assert set(payload) == {"error", "status", "error_id"}
```

### Does NOT Exist
- ~~`public_detail`~~ / ~~`Error(detail=...)`~~ — created here.
- ~~a `detail` key in production payloads today~~ — only in debug.
- ~~`AbstractHandler.error()`~~ (lowercase) — not the method to change; `Error` (line 133) is.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/utils/errors.py", "action": "MODIFY"},
    {"path": "querysource/handlers/abstract.py", "action": "MODIFY"},
    {"path": "querysource/outputs/output.py", "action": "MODIFY"},
    {"path": "tests/test_error_formatter.py", "action": "MODIFY"},
    {"path": "tests/qsurl/test_error_envelope.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/utils/errors.py#build_error_payload",
    "sym:querysource/handlers/abstract.py#AbstractHandler.Error",
    "sym:querysource/outputs/output.py#DataOutput.response"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Extend `build_error_payload` — *why*: it is the single place bodies are built.
2. Extend `Error` — *why*: handlers never call the builder directly.
3. Add the re-raise in `DataOutput.response` — *why*: otherwise a residual/cost `QSUrlError` raised while the writer pulls data becomes a redacted generic error.
4. Tests.

### `querysource/utils/errors.py` (MODIFY — signature)
```python
# occurrences: 1 (verified: grep -cF '    public_message: Optional[str] = None,' querysource/utils/errors.py)
# AFTER — insert below `    public_message: Optional[str] = None,` (verified: querysource/utils/errors.py:50)
    public_detail: Optional[dict] = None,
# and document it in the Args section of the docstring:
#   public_detail: Caller-asserted client-safe structured object. When given it is
#       emitted as ``payload["detail"]`` in every mode (``trace`` stays debug-only).
```

### `querysource/utils/errors.py` (MODIFY — payload)
```python
# occurrences: 1 (verified: grep -cF '        payload["detail"] = detail' querysource/utils/errors.py)
# REPLACE the block at querysource/utils/errors.py:147-149:
#     if debug:
#         payload["detail"] = detail
#         payload["trace"] = trace
# with:
    if debug:
        payload["detail"] = detail
        payload["trace"] = trace
    if public_detail is not None:
        payload["detail"] = public_detail
```

### `querysource/handlers/abstract.py` (MODIFY — signature)
```python
# occurrences: 1 (verified: grep -cF '        code: int = 400' querysource/handlers/abstract.py)
# AFTER — insert below `        code: int = 400` (verified: querysource/handlers/abstract.py:139, inside `def Error(` at 133)
        detail: dict | None = None,
# add to the docstring Args:
#   detail (dict, optional): client-safe structured error; when given, ``message`` is
#       public in every mode and ``detail`` is emitted as the payload's ``detail``.
```

### `querysource/handlers/abstract.py` (MODIFY — call)
```python
# occurrences: 3 for `            public_message=message if self.debug else None,` (verified: abstract.py:120,173,239)
# FILL IN: disambiguate — change ONLY the occurrence inside `Error`, i.e. the one preceded by
#     `            debug=self.debug,`
#     `            logger=self.logger,`
# at line 173 and followed by `        )` / `        args = {` whose headers use `"X-STATUS": str(code)`.
# REPLACE it with:
            public_message=message if (self.debug or detail is not None) else None,
            public_detail=detail,
```
**Why**: qsurl messages are grammar text plus the caller's own query — never SQL, DSNs or paths — so exposing them is safe by construction; all other callers pass no `detail` and keep today's redaction.

### `querysource/outputs/output.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -cF '            except (NoDataFound, DataNotFound) as err:' querysource/outputs/output.py)
# BEFORE — insert above `            except (NoDataFound, DataNotFound) as err:` (verified: querysource/outputs/output.py:237)
            except QSUrlError:
                raise  # FEAT-152: the qsurl handler answers 400 with the structured detail
# + module import next to the other package imports (after `from ..utils.errors import build_error_payload`, line 17):
from ..qsurl.errors import QSUrlError
```

### `tests/test_error_formatter.py` (MODIFY — append at end of file)
```python
def test_public_detail_survives_production():
    """FEAT-152: a caller-asserted client-safe detail is emitted with debug=False; trace is not."""
    payload = build_error_payload(category="query_error", status=400,
                                  public_detail={"kind": "parse", "offset": 3})
    assert payload["detail"] == {"kind": "parse", "offset": 3}
    assert "trace" not in payload


def test_public_detail_absent_keeps_minimal_payload():
    payload = build_error_payload(category="query_error", status=400, public_detail=None)
    assert set(payload) == {"error", "status", "error_id"}
```

### `tests/qsurl/test_error_envelope.py` (CREATE)
```python
"""AbstractHandler.Error(detail=...) and DataOutput's QSUrlError pass-through (spec AC7)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from querysource.handlers.abstract import AbstractHandler
from querysource.qsurl import QSUrlError


def _handler(debug: bool) -> AbstractHandler:
    h = AbstractHandler.__new__(AbstractHandler)
    h.logger = MagicMock()
    h.debug = debug
    h._json = MagicMock()
    h._json.dumps = json.dumps
    return h


@pytest.mark.parametrize("debug", [False, True])
def test_error_with_detail_is_400_with_detail(debug):
    err = QSUrlError("parse", "boom", offset=2, pointer="ab\n  ^")
    resp = _handler(debug).Error(message=err.message, exception=err, code=400, detail=err.to_dict())
    body = json.loads(resp.text)
    assert resp.status == 400 and body["detail"] == err.to_dict() and body["error"] == "boom"

def test_error_without_detail_unchanged_in_production(): ...   # FILL IN: no "detail" key, generic message
async def test_dataoutput_reraises_qsurlerror(): ...            # FILL IN: writer.get_result raises QSUrlError → propagates
```

### FILL IN checklist
- [ ] Disambiguate the `public_message` occurrence inside `Error`.
- [ ] Remaining test bodies (`DataOutput` test: patch `WRITERS["json"]` with a stub writer whose `get_result` raises).

---

## Acceptance Criteria

- [ ] `detail` present with `debug=False` only when the caller passes it (spec AC7).
- [ ] `pytest tests/test_error_formatter.py tests/test_feat102_error_redaction.py -q` green — every existing body unchanged.
- [ ] `QSUrlError` raised inside `writer.get_result()` propagates out of `DataOutput.response`.
- [ ] `ruff check querysource/utils/errors.py querysource/handlers/abstract.py querysource/outputs/output.py` clean.

---

## Validation Commands

- `pytest tests/test_error_formatter.py -q`
- `pytest tests/test_feat102_error_redaction.py -q`
- `pytest tests/qsurl/test_error_envelope.py -q`

---

## Test Specification

See the test blocks above.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at the path listed above for full context.
2. **Check dependencies** — verify every `Depends-on` task is `done` in `sdd/tasks/index/qsurl-parser.json`.
3. **Verify the Codebase Contract** before writing any code: re-run the `grep -c` for every MODIFY anchor; if a count changed, re-locate the anchor; if it is `0`, stop and report the drift.
4. **Update status** in `sdd/tasks/index/qsurl-parser.json` → `"in-progress"`.
5. **Implement** from the Implementation Blueprint; complete every `# FILL IN:`; never change a signature or path the blueprint fixes.
6. **Verify** every Acceptance Criterion and run the Validation Commands (`source .venv/bin/activate` first).
7. **Move this file** to `sdd/tasks/completed/TASK-774-error-envelope-detail.md` and set the index entry to `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

**Completed by**: sdd-worker (Claude Sonnet 5, sequential fallback loop)
**Date**: 2026-09-24
**Notes**: Implemented exactly per the Implementation Blueprint: `build_error_payload`
gained `public_detail: Optional[dict] = None`, documented, and emits `payload["detail"]
= public_detail` in every mode when given (after the existing debug-only block, so it
can override it). `AbstractHandler.Error` gained `detail: dict | None = None`,
documented; disambiguated the correct one of the three `public_message=message if
self.debug else None,` occurrences (confirmed by context: the one at the-then line 173,
now 176, preceded by `debug=self.debug,` / `logger=self.logger,` inside `Error`, distinct
from the ones in `NotFound` and `Except`) and changed it to `public_message=message if
(self.debug or detail is not None) else None,` plus `public_detail=detail,`.
`DataOutput.response` gained `except QSUrlError: raise` as the first except clause after
`await writer.get_result()` (before the existing `except (DriverError, QueryException)`
which would otherwise swallow it, since `QSUrlError` is a `QueryException` subclass) plus
the `from ..qsurl.errors import QSUrlError` import. Appended
`test_public_detail_survives_production` / `test_public_detail_absent_keeps_minimal_payload`
to `tests/test_error_formatter.py`; created `tests/qsurl/test_error_envelope.py` with the
given `test_error_with_detail_is_400_with_detail` plus the two FILL IN tests
(`test_error_without_detail_unchanged_in_production`, `test_dataoutput_reraises_qsurlerror`
— the latter via `monkeypatch.setitem(output_module.WRITERS, "json", _StubWriter)` with a
minimal writer stub whose `get_result()` raises `QSUrlError`, per the blueprint's guidance).
`pytest tests/test_error_formatter.py tests/test_feat102_error_redaction.py -q` → 38
passed (no regression; note: `-p no:logging` disables the `caplog` fixture these suites
use — do not pass that flag to this file). `pytest tests/qsurl/test_error_envelope.py -q`
→ 4 passed. `ruff check --select E9,F63,F7,F82` clean on every touched file; full
`ruff check` reports 2 pre-existing findings (a `B012` in `abstract.py` on an unrelated
line, an `I001` import-sort finding in `test_error_formatter.py`) — confirmed via `git
stash` to be identical before and after this task's changes.

**Deviations from spec**: none.
