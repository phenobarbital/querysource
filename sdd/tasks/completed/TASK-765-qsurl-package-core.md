# TASK-765: qsurl Python package core (`parse` API, `QSUrlError`, capabilities, `ResidualPlan`)

**Feature**: FEAT-152 — qsurl: HTSQL-style URL query dialect (chumsky 0.13 + PyO3)
**Spec**: `sdd/specs/qsurl-parser.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2. Every other Python task imports something from this one: the public
`parse()` / `requires()` API with its Rust→Lark import ladder, the `QSUrlError` both
back-ends raise, the closed capability vocabulary providers declare, and the `ResidualPlan`
dataclass the translator produces and `QS` consumes. Back-end resolution is **lazy** so the
package imports cleanly before the Rust extension (TASK-764/777) and the Lark fallback
(TASK-766) exist. This task also creates the shared `tests/qsurl/` package and fixtures.

---

## Scope

- Create `querysource/qsurl/{__init__,errors,capabilities,plan}.py` per the blueprint.
- Honour `QSURL_FORCE_FALLBACK=1` (env) in the import ladder so tests can hide the extension (spec AC3).
- Create `tests/qsurl/__init__.py`, `tests/qsurl/conftest.py` (fixtures `corpus`, `stores_df`) and `tests/qsurl/test_core.py`.

**NOT in scope**: the Lark grammar/fallback (TASK-766), GBNF (TASK-768), translation
(TASK-771), the residual evaluator (TASK-772), any provider or `QS` change.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/qsurl/__init__.py` | CREATE | Public API + lazy back-end ladder |
| `querysource/qsurl/errors.py` | CREATE | `QSUrlError`, `ERROR_KINDS` |
| `querysource/qsurl/capabilities.py` | CREATE | Capability constants, `ALL`, `BASE`, `UNSUPPORTED_PHASE1`, `validate` |
| `querysource/qsurl/plan.py` | CREATE | `ResidualPlan` frozen dataclass |
| `tests/qsurl/__init__.py` | CREATE | Empty — makes `tests/qsurl` a package like `tests/handlers` |
| `tests/qsurl/conftest.py` | CREATE | `corpus` and `stores_df` fixtures |
| `tests/qsurl/test_core.py` | CREATE | Unit tests for this task |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.exceptions import QueryException   # verified: querysource/exceptions.py:6
```

### Existing Signatures to Use
```python
# querysource/exceptions.py:6-31
class QueryException(Exception):
    default_code: int = 500
    code: int = 500
    def __init__(self, message: str, code: int | None = None, **kwargs):   # line 16
        super().__init__(message)
        self.stacktrace = kwargs.get('stacktrace', None)
        self.message = message
        self.args = kwargs                       # NOTE: overwrites .args with the kwargs dict
        self.code = int(code) if code else self.default_code
    def __str__(self): return f"{self.message!s}"   # line 24

# querysource/qs_parsers/__init__.py:10-25 — the ladder shape to replicate
try:
    from ._qs_parsers import *              # in-wheel .so next to __init__.py
    HAS_RUST = True
except ImportError:
    try:
        from _qs_parsers import *           # maturin develop installs top-level
        HAS_RUST = True
    except ImportError:
        HAS_RUST = False
```

```text
Rust binding contract (TASK-764, reference python.rs):
  _qsurl.parse(src: str) -> str            # JSON IR
  _qsurl.requires(src: str) -> list[str]
  both raise ValueError whose args[0] is the error JSON:
    parse: {"kind":"parse","offset":N,"message":..,"found":str|null,"expected":[..],"pointer":..}
    lower: {"kind":"lower","message":..}           ← offset/found/expected/pointer ABSENT
```

### Does NOT Exist
- ~~`querysource/qsurl/`~~ — created by this task.
- ~~`querysource.qsurl._fallback`~~ — created by TASK-766; import it lazily inside `parse()`, never at module top level.
- ~~`querysource.qsurl.gbnf`~~ — created by TASK-768; `to_gbnf()` imports it lazily.
- ~~`ResidualPlan.apply()`~~ — application lives in `querysource/qsurl/residual.py` (TASK-772).
- ~~`QueryException.args` as a tuple~~ — the base class overwrites `.args` with a dict; do not rely on it.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/qsurl/__init__.py", "action": "CREATE"},
    {"path": "querysource/qsurl/errors.py", "action": "CREATE"},
    {"path": "querysource/qsurl/capabilities.py", "action": "CREATE"},
    {"path": "querysource/qsurl/plan.py", "action": "CREATE"},
    {"path": "tests/qsurl/__init__.py", "action": "CREATE"},
    {"path": "tests/qsurl/conftest.py", "action": "CREATE"},
    {"path": "tests/qsurl/test_core.py", "action": "CREATE"}
  ],
  "contract_symbols": ["sym:querysource/exceptions.py#QueryException"]
}
```

---

## Implementation Notes

### Key Constraints
- `capabilities.py` must import NOTHING from `querysource` — `querysource/providers/abstract.py` will import it (TASK-770) and any back-import risks a cycle.
- `querysource/qsurl/__init__.py` must not import pandas, lark or the providers.
- Log the fallback warning once per process via `logging.getLogger(__name__)`.
- Google-style docstrings, strict type hints, `ruff check` clean.

---

## Implementation Blueprint

### Steps (in order)
1. Write `capabilities.py` and `plan.py` first — *why*: they are leaf modules with no imports from the package.
2. Write `errors.py` — *why*: `__init__` re-exports `QSUrlError`.
3. Write `__init__.py` with the lazy ladder — *why*: the Lark fallback does not exist yet; importing it eagerly would break this task's own tests.
4. Write the tests package and fixtures, then `test_core.py`.

### `querysource/qsurl/capabilities.py` (CREATE)
```python
"""Closed capability vocabulary for qsurl (matches the Rust ``Feature`` enum).

The tuple order of ``ALL`` is the Rust declaration order, which is the order the
IR's ``requires`` list uses. Never sort it alphabetically.
"""
from __future__ import annotations

SELECT = "select"
ALIAS = "alias"
FILTER = "filter"
OR = "or"
NOT = "not"
IN_LIST = "in_list"
NULL_CHECK = "null_check"
TEXT_MATCH = "text_match"
REGEX = "regex"
FUNCTIONS = "functions"
NAVIGATION = "navigation"
SORT = "sort"
LIMIT = "limit"
OFFSET = "offset"
DISTINCT = "distinct"

ALL: tuple[str, ...] = (
    SELECT, ALIAS, FILTER, OR, NOT, IN_LIST, NULL_CHECK, TEXT_MATCH, REGEX,
    FUNCTIONS, NAVIGATION, SORT, LIMIT, OFFSET, DISTINCT,
)
BASE: frozenset[str] = frozenset({SELECT, FILTER, IN_LIST, NULL_CHECK})
UNSUPPORTED_PHASE1: frozenset[str] = frozenset({FUNCTIONS, NAVIGATION})


def validate(caps: frozenset[str]) -> frozenset[str]:
    """Return ``caps`` unchanged after checking every token is in ``ALL``.

    Raises:
        ValueError: naming the unknown token(s).
    """
    # FILL IN: compute unknown = caps - set(ALL); raise ValueError(f"unknown qsurl capabilities: {sorted(unknown)}") when non-empty
    return caps
```
**Why this shape**: the declaration order is load-bearing for byte parity (spec §7 "requires ordering").

### `querysource/qsurl/plan.py` (CREATE)
```python
"""The in-memory work a provider did not do for a qsurl query."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResidualPlan:
    """Work left for the residual stage.

    Applied by ``querysource.qsurl.residual.apply`` in the fixed order
    filter → sort → project → distinct → offset → limit → rename.

    Attributes:
        filter: IR filter node or leaf still to evaluate, or None.
        sort: ``(column, descending)`` pairs.
        project: final column order (original names); empty keeps every column.
        distinct: drop duplicate rows.
        offset: rows to skip.
        limit: rows to keep.
        rename: ``(column, alias)`` pairs, applied last.
    """

    filter: dict | None = None
    sort: tuple[tuple[str, bool], ...] = ()
    project: tuple[str, ...] = ()
    distinct: bool = False
    offset: int | None = None
    limit: int | None = None
    rename: tuple[tuple[str, str], ...] = ()

    def is_empty(self) -> bool:
        """Return True when every field is at its default (nothing to apply)."""
        return self == ResidualPlan()
```
**Why**: exact fields and order fixed by spec §2 Data Models; frozen so `QS` can share it safely.

### `querysource/qsurl/errors.py` (CREATE)
```python
"""Structured qsurl errors shared by the Rust and Lark back-ends."""
from __future__ import annotations

import json

from ..exceptions import QueryException  # verified: querysource/exceptions.py:6

ERROR_KINDS: tuple[str, ...] = ("parse", "lower", "unsupported", "cost")


class QSUrlError(QueryException):
    """A qsurl grammar, lowering, capability or cost error (HTTP 400).

    ``str(err)`` is the error JSON so it survives any logger; ``to_dict()`` is the
    object placed in the 400 envelope's ``detail``.
    """

    default_code: int = 400

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        offset: int = 0,
        found: str | None = None,
        expected: list[str] | None = None,
        pointer: str = "",
        code: int = 400,
    ) -> None:
        # FILL IN: raise ValueError if kind not in ERROR_KINDS — bounded by spec §2 error object
        super().__init__(message, code=code)
        self.kind = kind
        self.offset = offset
        self.found = found
        self.expected = list(expected or [])
        self.pointer = pointer

    @classmethod
    def from_json(cls, payload: str) -> "QSUrlError":
        """Build from the JSON string the Rust binding puts in ``ValueError.args[0]``.

        Missing keys (a Rust ``lower`` error carries only kind and message) take the
        constructor defaults.
        """
        data = json.loads(payload)
        # FILL IN: return cls(data["kind"], data["message"], offset=data.get("offset", 0), ...)

    def to_dict(self) -> dict:
        """Return ``{"kind","offset","message","found","expected","pointer"}``."""
        # FILL IN: build the dict in exactly this key order

    def __str__(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
```
**Why**: `code=400` makes every handler that reads `.code` answer 400 (spec AC7). `__str__` override is deliberate: `QueryException.__str__` returns the message only.

### `querysource/qsurl/__init__.py` (CREATE)
```python
"""qsurl — HTSQL-style URL query dialect for QuerySource.

Public API: ``parse()``, ``requires()``, ``to_gbnf()``, ``HAS_RUST``, ``QSUrlError``,
``ResidualPlan``. The Rust extension (``_qsurl``) is used when importable; otherwise
the pure-Python Lark fallback is imported lazily on first use.
"""
from __future__ import annotations

import json
import logging
import os

from .errors import QSUrlError
from .plan import ResidualPlan

_logger = logging.getLogger(__name__)
_FORCE_FALLBACK = os.environ.get("QSURL_FORCE_FALLBACK", "") == "1"

_rs = None
if not _FORCE_FALLBACK:
    try:
        from . import _qsurl as _rs  # in-wheel: querysource/qsurl/_qsurl*.so
    except ImportError:
        try:
            import _qsurl as _rs  # maturin develop installs it top-level
        except ImportError:
            _rs = None
HAS_RUST: bool = _rs is not None
_warned = False


def _fallback():
    """Import the Lark back-end on first use, warning once per process."""
    global _warned  # pylint: disable=global-statement
    from . import _fallback as fb  # lazy: created by TASK-766
    if not _warned:
        _logger.warning("qsurl: Rust extension unavailable, using the Lark fallback")
        _warned = True
    return fb


def parse(src: str) -> dict:
    """Parse a percent-decoded qsurl string into the IR dict.

    Raises:
        QSUrlError: kind "parse" or "lower", from either back-end.
    """
    if _rs is not None:
        try:
            return json.loads(_rs.parse(src))
        except ValueError as err:
            # FILL IN: raise QSUrlError.from_json(str(err.args[0])) from err
            #   — json.JSONDecodeError is a ValueError subclass: only map errors raised by _rs.parse
            raise
    return _fallback().parse(src)


def requires(src: str) -> list[str]:
    """Return the IR's ``requires`` list (Rust declaration order); raises QSUrlError."""
    return list(parse(src)["requires"])


def to_gbnf() -> str:
    """Return the GBNF rendering of ``grammar.lark`` (lazy import of ``.gbnf``)."""
    from .gbnf import to_gbnf as _to_gbnf  # created by TASK-768
    return _to_gbnf()


__all__ = ("HAS_RUST", "QSUrlError", "ResidualPlan", "parse", "requires", "to_gbnf")
```
**Why**: ladder copied from `querysource/qs_parsers/__init__.py:10-25`; the env switch gives tests a way to exercise the fallback with the extension installed (AC3).

### `tests/qsurl/conftest.py` (CREATE)
```python
"""Shared fixtures for the qsurl test suite."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

CORPUS_PATH = Path(__file__).with_name("corpus.json")


@pytest.fixture(scope="session")
def corpus() -> list[dict]:
    """The parity corpus (created by TASK-767); skips when it does not exist yet."""
    if not CORPUS_PATH.exists():
        pytest.skip("tests/qsurl/corpus.json not present yet")
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def stores_df() -> pd.DataFrame:
    """Eight stores covering every residual leaf branch (mixed case, None, '', tz datetimes)."""
    # FILL IN: 8 rows, columns store_id(int), name(str, mixed case), city(str incl. one None and one ''),
    #   state_code(str), price(float), opened(ISO str, some with 'Z' / '+02:00'), closed_at(nullable ISO str)
```
**Why**: owned here (not by a later task) because several tasks' tests load it; a later edit would force those tasks to be exclusive.

### `tests/qsurl/__init__.py` (CREATE)
```python
```
**Why**: empty file; `tests/handlers/__init__.py` and `tests/e2e/__init__.py` follow the same convention.

### FILL IN checklist
- [ ] `capabilities.validate` — raise on unknown tokens.
- [ ] `QSUrlError.__init__` — kind validation; `from_json` defaults; `to_dict` key order.
- [ ] `__init__.parse` — map only the binding's `ValueError` to `QSUrlError`.
- [ ] `conftest.stores_df` — 8 rows per the docstring.
- [ ] `test_core.py` bodies (Test Specification).

---

## Acceptance Criteria

- [ ] `python -c "import querysource.qsurl as q; print(q.HAS_RUST)"` works with no extension and no `_fallback.py` present.
- [ ] `QSUrlError.from_json('{"kind":"lower","message":"m"}').to_dict() == {"kind":"lower","offset":0,"message":"m","found":None,"expected":[],"pointer":""}`.
- [ ] `QSUrlError(...).code == 400` and `isinstance(err, QueryException)`.
- [ ] `capabilities.ALL` equals the Rust `Feature` declaration order.
- [ ] `QSURL_FORCE_FALLBACK=1` makes `HAS_RUST` False.
- [ ] `ruff check querysource/qsurl tests/qsurl` clean.

---

## Validation Commands

- `pytest tests/qsurl/test_core.py -q`

---

## Test Specification

```python
# tests/qsurl/test_core.py
import importlib
import pytest
from querysource.exceptions import QueryException
from querysource.qsurl import QSUrlError, ResidualPlan
from querysource.qsurl import capabilities as caps


def test_all_is_rust_declaration_order():
    assert caps.ALL[:3] == ("select", "alias", "filter") and caps.ALL[-1] == "distinct"

def test_validate_rejects_unknown():
    with pytest.raises(ValueError):
        caps.validate(frozenset({"select", "foo"}))

def test_qsurlerror_roundtrip_and_code():
    err = QSUrlError("parse", "boom", offset=3, expected=["x"], pointer="ab\n   ^")
    assert QSUrlError.from_json(str(err)).to_dict() == err.to_dict()
    assert err.code == 400 and isinstance(err, QueryException)

def test_from_json_lower_defaults(): ...          # FILL IN
def test_residualplan_is_empty(): ...             # FILL IN: default empty; ResidualPlan(limit=1) not empty
def test_force_fallback_env(monkeypatch): ...     # FILL IN: setenv QSURL_FORCE_FALLBACK=1, importlib.reload, assert not HAS_RUST
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at the path listed above for full context.
2. **Check dependencies** — verify every `Depends-on` task is `done` in `sdd/tasks/index/qsurl-parser.json`.
3. **Verify the Codebase Contract** before writing any code: re-run the `grep -c` for every MODIFY anchor; if a count changed, re-locate the anchor; if it is `0`, stop and report the drift.
4. **Update status** in `sdd/tasks/index/qsurl-parser.json` → `"in-progress"`.
5. **Implement** from the Implementation Blueprint; complete every `# FILL IN:`; never change a signature or path the blueprint fixes.
6. **Verify** every Acceptance Criterion and run the Validation Commands (`source .venv/bin/activate` first).
7. **Move this file** to `sdd/tasks/completed/TASK-765-qsurl-package-core.md` and set the index entry to `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

**Completed by**: sdd-worker (Claude Sonnet 5, sequential fallback loop)
**Date**: 2026-09-24
**Notes**: Implemented `querysource/qsurl/{__init__,errors,capabilities,plan}.py` and
`tests/qsurl/{__init__,conftest,test_core}.py` exactly per the Implementation Blueprint.
All FILL IN sections completed: `capabilities.validate` raises `ValueError` naming
unknown tokens; `QSUrlError.__init__` validates `kind` against `ERROR_KINDS`;
`QSUrlError.from_json`/`to_dict` implemented with the exact key order; `__init__.parse`
maps only the Rust binding's `ValueError` to `QSUrlError.from_json`; `stores_df` fixture
has 8 rows covering mixed-case names, a None city, an empty-string city, and both `Z`
and `+02:00` datetime offsets; `test_core.py` bodies filled in per the Test Specification.
`pytest tests/qsurl/test_core.py -q` → 6 passed. `ruff check querysource/qsurl tests/qsurl`
clean (after removing one unnecessary quoted type annotation, `UP037`, auto-fixable).
Verified `python -c "import querysource.qsurl as q; print(q.HAS_RUST)"` → `False` (no
extension, no `_fallback.py` yet) and `QSURL_FORCE_FALLBACK=1` also forces `HAS_RUST=False`.

**Deviations from spec**: none
