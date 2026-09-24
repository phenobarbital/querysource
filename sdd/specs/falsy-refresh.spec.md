---
# SDD flow type and base branch (FEAT-145).
# - type: feature  (default)  → base_branch: dev (or any non-main branch)
# - type: hotfix              → base_branch MUST be: main
type: feature
base_branch: dev
# projects: parts of the codebase this doc concerns: `querysource` or a subsystem
#   (providers, parsers, rust-parsers, outputs, multiquery, handlers, datasources,
#   auth, cache, scheduler) or an area (sdd-tooling, dev-loop, docs, ci). Unknown values warn, not fail.
projects: [providers, parsers, querysource]
# tags: free-form kebab-case keywords for organizing specs (e.g. bigquery, cache).
tags: [refresh, paged, cache, boolean-coercion, query-conditions]
---

# Feature Specification: Falsy Refresh — flag-condition coercion for `refresh` and `paged`

**Feature ID**: FEAT-149
**Date**: 2026-09-24
**Author**: Jesus Lara (with Claude)
**Status**: draft
**Target version**: 5.0.1 (current: `querysource/version.py:9` → `5.0.0`)

Source: `sdd/proposals/falsy-refresh.brainstorm.md` (Recommended Option A).

---

## 1. Motivation & Business Requirements

### Problem Statement

`BaseProvider.__init__` reads the `refresh` condition with plain `bool()`
(`querysource/providers/abstract.py:83-85`). Conditions arriving over HTTP are
**strings**, so `refresh=false`, `refresh=0`, `refresh=no` all evaluate to `True`.
`QS.query()` (`querysource/queries/qs.py:385,414`) uses `self._qs.refresh()` to
decide whether to ignore an existing cache entry, so a client sending an explicit
"false" silently forces a fresh database hit — defeating the Redis result cache
and adding load to the datasource.

The parser reads the same key correctly (`AbstractParser._query_refresh_sync`,
`querysource/parsers/abstract.pyx:177-186`, via `strtobool`), so provider and
parser **disagree** on the same input, and only the provider's value drives caching.

A sibling defect exists in the parser's `paged` condition
(`querysource/parsers/abstract.pyx:211-219`): an unrecognized value such as
`paged=maybe` or `paged=` reaches `strtobool()`, which raises `ValueError`; only
`KeyError`/`AttributeError` are caught. Because the build uses **Cython 3.x**
(`pyproject.toml:5` → `Cython==3.0.11`), a `cdef void` method without `noexcept`
propagates the exception, so `AbstractParser.set_options()` raises and the whole
query fails. Verified empirically on 2026-09-24 (`498a56e`):

| Conditions passed to `SQLParser` | `await set_options()` today |
|---|---|
| `{'refresh': 'false'}` | ok, `refresh=False` |
| `{'refresh': ''}` | ok, `refresh=False` |
| `{'paged': 'true', 'page': 3}` | ok |
| `{'paged': 'maybe', 'page': 3}` | **raises `ValueError: invalid truth value for maybe`** |
| `{'paged': '', 'page': 3}` | **raises `ValueError: invalid truth value for `** |

> Correction to the brainstorm: it described the `paged` error as "unraisable /
> silently swallowed". Under Cython 3 it propagates; the table above is authoritative.

**Affected**: API clients that pass `refresh=false` (uncached, slower responses),
any client sending a malformed `paged` (hard query failure), ops (unnecessary DB load).

### Goals
- G1: One shared flag-coercion helper, a Cython `cpdef` in
  `querysource/types/validators.pyx`, exported from `querysource.types`.
- G2: `BaseProvider` and `AbstractParser` produce the **same** boolean for the
  same `refresh` input.
- G3: `refresh=false|0|no|off|f|n|null` no longer bypasses the cache.
- G4: The parser's `paged` condition uses the same helper; malformed values never
  raise out of `set_options()`.
- G5: Unrecognized values are logged as warnings and treated as `False`.

### Non-Goals (explicitly out of scope)
- `externalProvider.refresh()` (`querysource/providers/external.py:65-66`) keeps
  always returning `True`.
- Normalizing conditions in HTTP handlers (brainstorm Option D, rejected: misses
  the Python API / scheduler / MultiQuery paths).
- Making the provider delegate to `parser.refresh` (brainstorm Option C, rejected).
- Reusing `querysource.types.converters.to_boolean` (brainstorm Option B, rejected:
  wrong semantics for `''` and `2`).
- Auditing other `bool(...)` coercions of conditions (e.g. `distinct` at
  `parsers/abstract.pyx:387`) — not in scope.
- HTTP-level tests.

---

## 2. Architectural Design

### Overview

Add `to_flag(value)` to `querysource/types/validators.pyx`, next to `strtobool`.
It implements one fixed truth table and **raises `ValueError`** on anything it does
not recognize; it never logs (it stays pure). Each caller wraps it in
`try/except ValueError`, logs a warning through its own logger, and falls back to
`False`.

**Truth table (all decided in the brainstorm):**

| Input | Result |
|---|---|
| `bool` | passes through (checked **before** `int`, since `bool` ⊂ `int`) |
| `None` | `False` |
| `int` `1` / `0` | `True` / `False` |
| any other `int`, any `float`, `Decimal`, etc. | `ValueError` |
| `str`, after `.strip()`, empty (`''`, bare `?refresh`) | `True` (flag style) |
| `str` in `y yes t true on 1` (case-insensitive) | `True` |
| `str` in `n no f false off 0 null` (case-insensitive) | `False` |
| any other `str` | `ValueError` |
| `bytes` / `bytearray` | `ValueError` (not decoded) |
| anything else (`list`, `dict`, …) | `ValueError` |

Callers turn `ValueError` into a `False` result plus a warning.

User-facing effect:
- `?refresh=false` (and the other false words) → served from cache when an entry exists.
- `?refresh=true|1|yes|on|t|y`, bare `?refresh`, `?refresh=` → bypass cache.
- `?refresh=maybe` → treated as false, warning logged server-side, no client error.
- `?paged=maybe` / `?paged=` → no longer fail the query (`maybe` → `False` +
  warning; `''` → `True`).

### Component Diagram
```
HTTP / Python API / scheduler conditions {'refresh': ..., 'paged': ...}
        │
        ├──→ BaseProvider.__init__ ──→ to_flag(refresh) ──→ self._refresh ──→ QS.query() cache decision
        │                                  (ValueError → warn + False)
        │
        └──→ AbstractParser.set_options → _extract_options
                 ├─ _query_refresh_sync    ──→ to_flag(refresh) ──→ self.refresh
                 └─ _offset_pagination_sync ─→ to_flag(paged)   ──→ self._paged
                                                (ValueError → warn + False)
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `querysource/types/validators.pyx` | extends | new `cpdef to_flag`; extension rebuild |
| `querysource/types/__init__.py` | extends | re-export `to_flag`, add to `__all__` |
| `BaseProvider.__init__` (`providers/abstract.py:83-85`) | modifies | `bool(...)` → `to_flag(...)` + fallback |
| `AbstractParser._query_refresh_sync` (`parsers/abstract.pyx:177-186`) | modifies | `strtobool` branch → `to_flag` + fallback |
| `AbstractParser._offset_pagination_sync` (`parsers/abstract.pyx:204-223`) | modifies | `paged` block → `to_flag` + fallback |
| `QS.query()` (`queries/qs.py:385,414`) | depends on | unchanged; receives a correct bool |
| `externalProvider.refresh()` | unchanged | still `True` |

### Data Models
None.

### New Public Interfaces
```python
# querysource/types/validators.pyx
cpdef object to_flag(object value):
    """Coerce a flag-style query condition to bool (see §2 truth table).

    Raises:
        ValueError: when value is not a recognized flag value.
    """
```

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: `to_flag` helper | yes | signature and truth table fixed in §2; raises `ValueError`; exported from `querysource.types` | — |
| M2: provider `refresh` | yes | 3-line swap at `providers/abstract.py:83-85`; warning via `self._logger`; `del` kept | — |
| M3: parser `refresh` + `paged` | yes | two blocks in `parsers/abstract.pyx`; warning via `self.logger`; `page` pop untouched | — |
| M4: tests | yes | file `tests/test_flag_conditions.py`; cases listed in §4 | — |

### Module 1: `to_flag` helper
- **Path**: `querysource/types/validators.pyx`, `querysource/types/__init__.py`
- **Responsibility**: the single truth table (§2). Pure: no logging, no I/O.
- **Depends on**: existing `strtobool` (`validators.pyx:47`) — reuse its word
  lists by calling it; do not duplicate them. `strtobool` is typed `str val`, so
  only call it with a `str`.
- **Interface Skeleton**:
  ```python
  # querysource/types/validators.pyx  (modifies; insert after strtobool, verified: validators.pyx:47-62)
  cpdef object to_flag(object value):
      """Coerce a flag-style condition value (e.g. ``refresh``, ``paged``) to bool.

      Args:
          value: raw condition value (str from HTTP, bool/int/None from JSON or Python).

      Returns:
          bool: per the FEAT-149 truth table; an empty/whitespace string is True.

      Raises:
          ValueError: for unrecognized strings, bytes/bytearray, numbers other
              than int 0/1, and any other type.
      """

  # querysource/types/__init__.py  (modifies line 2 and __all__ at 95-98)
  from .validators import is_boolean, is_empty, strtobool, to_flag  # verified: types/__init__.py:2
  # __all__ gains 'to_flag'
  ```

### Module 2: Provider `refresh` coercion
- **Path**: `querysource/providers/abstract.py`
- **Responsibility**: `BaseProvider.__init__` sets `self._refresh` with `to_flag`;
  on `ValueError` logs `self._logger.warning(...)` (value shown as a truncated
  `repr`, ≤ 64 chars) and sets `False`. `del self._conditions['refresh']` is kept.
- **Depends on**: Module 1 (imports `to_flag` from `..types`).
- **Interface Skeleton**: no new signatures. Changes inside
  `BaseProvider.__init__` (verified: `providers/abstract.py:38`), block at `:83-85`.
  New import at module top: `from ..types import to_flag`.

### Module 3: Parser `refresh` + `paged` coercion
- **Path**: `querysource/parsers/abstract.pyx`
- **Responsibility**:
  - `_query_refresh_sync` (`:177-186`): `self.refresh = to_flag(refresh)`; keep
    catching `KeyError`/`AttributeError` → `False`; `ValueError` → `self.logger.warning(...)` + `False`.
  - `_offset_pagination_sync` (`:204-223`): replace the `is_boolean`/`strtobool`
    branch at `:211-217` with `self._paged = to_flag(paged)`; `ValueError` →
    warning + `False`. The following `page` pop (`:220-223`) must still run in every case.
  - Import: extend `from ..types import strtobool, is_boolean` (`:15`) with `to_flag`.
    Leave `strtobool`/`is_boolean` imported if still used elsewhere in the file
    (they are used at other sites — 4 references total today).
  - Rebuild: `make build-inplace` (also required for Module 1).
- **Depends on**: Module 1.
- **Interface Skeleton**: no signature changes; `pxd` declarations unchanged
  (`abstract.pxd:23` `cdef public bint refresh`, `:42` `cdef bint _paged`,
  `:67`/`:70` the two `cdef void` methods).

### Module 4: Tests
- **Path**: `tests/test_flag_conditions.py` (new)
- **Responsibility**: §4 cases. Provider tests use a minimal concrete
  `BaseProvider` subclass (only abstract method: `async def query(self)`,
  verified `providers/abstract.py:240`) with `__parser__ = None`, constructed
  inside a running loop (pytest-asyncio `auto` mode). Parser tests construct
  `SQLParser(query=..., definition=None, conditions=...)` (the `definition` kwarg is
  **required**, verified `parsers/abstract.pyx:31-37`) and `await p.set_options()`.
- **Depends on**: Modules 1–3.

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_to_flag_truthy` | M1 | parametrized: `True, 1, '', '  ', 'true', 'TRUE', ' yes ', 'on', 't', 'y', '1'` → `True` |
| `test_to_flag_falsy` | M1 | parametrized: `False, None, 0, 'false', 'False', 'no', 'off', 'f', 'n', '0', 'null'` → `False` |
| `test_to_flag_unrecognized_raises` | M1 | parametrized: `'maybe', '2', 2, -1, 1.0, 0.0, b'true', bytearray(b'1'), [], {}` → `ValueError` |
| `test_to_flag_exported` | M1 | `from querysource.types import to_flag` works and is in `__all__` |
| `test_provider_refresh_false_string` | M2 | `conditions={'refresh': 'false'}` → `refresh() is False`, key removed from `_conditions` |
| `test_provider_refresh_values` | M2 | parametrized over truthy/falsy strings, bare `''` → `True` |
| `test_provider_refresh_unrecognized_warns` | M2 | `'maybe'` → `False` and a WARNING record (`caplog`) |
| `test_provider_no_refresh_default` | M2 | no key → `refresh() is False` |
| `test_parser_refresh_parity` | M3 | for each value in the provider table, `SQLParser.refresh` equals `BaseProvider.refresh()` |
| `test_parser_paged_unrecognized_no_raise` | M3 | `{'paged': 'maybe', 'page': 3}` → `set_options()` does not raise; `'page'` consumed from `conditions` |
| `test_parser_paged_empty_is_true` | M3 | `{'paged': '', 'page': 3}` → does not raise |

Note: `_paged` is `cdef bint` without `public` (`abstract.pxd:42`), so it is not
readable from Python; `paged` tests assert on "does not raise" + `conditions`
contents + warning record, not on `_paged` directly.

### Integration Tests
None (decided: no HTTP-level test).

### Test Data / Fixtures
```python
@pytest.fixture
def make_provider():
    """Factory for a minimal concrete BaseProvider with no parser."""
    ...
```

---

## 5. Acceptance Criteria

- [ ] `to_flag` exists in `querysource/types/validators.pyx`, is importable as
  `from querysource.types import to_flag`, and is listed in `querysource.types.__all__`.
- [ ] `to_flag` implements exactly the §2 truth table (bool first; `None`→False;
  int 0/1; empty string→True; `strtobool` words; bytes and everything else → `ValueError`).
- [ ] `BaseProvider(conditions={'refresh': 'false'}).refresh() is False`.
- [ ] `BaseProvider` with `refresh=''` → `True`; with `refresh='maybe'` → `False`
  and a WARNING is logged; `'refresh'` is removed from `_conditions` in all cases.
- [ ] For every value in the §4 tables, `AbstractParser.refresh` equals `BaseProvider.refresh()`.
- [ ] `SQLParser(..., conditions={'paged': 'maybe', 'page': 3}).set_options()`
  and `{'paged': ''}` no longer raise; `page` is still popped from conditions.
- [ ] Unrecognized values log a warning containing a truncated `repr` (≤ 64 chars) of the value.
- [ ] `externalProvider.refresh()` still returns `True` (no change to `external.py`).
- [ ] `make build-inplace` succeeds; `pytest tests/test_flag_conditions.py -q` passes.
- [ ] Existing parser tests still pass: `pytest tests/test_sql_parser_combinations.py tests/test_grouping_sync.py -q`.
- [ ] `ruff check querysource/providers/abstract.py tests/test_flag_conditions.py` is clean.
- [ ] No change to the `refresh` condition name or any public method signature.

---

## 6. Codebase Contract

> Verified against `498a56e` (dev), 2026-09-24.

### Verified Imports
```python
from querysource.types import strtobool, is_boolean, is_empty   # verified: querysource/types/__init__.py:2
from querysource.parsers.sql import SQLParser                   # verified: querysource/parsers/sql.pyx:85 (cdef class SQLParser(AbstractParser)); imported and run 2026-09-24
from querysource.providers.abstract import BaseProvider         # verified: querysource/providers/abstract.py:23
# inside parsers/abstract.pyx:
from ..types import strtobool, is_boolean                       # verified: parsers/abstract.pyx:15
```

### Existing Class Signatures
```python
# querysource/providers/abstract.py
class BaseProvider(ABC):                                        # line 23
    __parser__: AbstractParser = None                           # line 25
    _parser_options: dict = {}                                  # line 26
    def __init__(self, slug: str = '', query: Any = None, qstype: str = '',
                 connection: Callable = None,
                 definition: Union[QueryModel, dict] = None,
                 conditions: dict = None, request: web.Request = None,
                 **kwargs):                                     # line 38
        self._logger = logging.getLogger(f'QS.{self.__name__}') # line 50
        self._refresh: bool = False                             # line 71
        if conditions:
            self._conditions = copy.deepcopy(conditions)        # line 82
            if 'refresh' in self._conditions:                   # line 83
                self._refresh = bool(self._conditions['refresh'])   # line 84  <-- BUG
                del self._conditions['refresh']                 # line 85
        # event loop: kwargs['loop'] or asyncio.get_running_loop() → RuntimeError if none (lines 89-98)
        # parser built with the ORIGINAL `conditions`, not the copy (lines 101-108)
    async def prepare_connection(self):                         # line 195 → await self._parser.set_options() (200)
    @abstractmethod
    async def query(self): ...                                  # line 240 (only abstract method)
    def refresh(self):                                          # line 257
        return self._refresh                                    # line 258

# querysource/providers/external.py
def refresh(self) -> bool:                                      # line 65
    return True                                                 # line 66

# querysource/queries/qs.py
refresh = self._qs.refresh()                                    # line 385
if refresh is True and exists is True:                          # line 414 — skip cache
```

```python
# querysource/parsers/abstract.pxd
cdef public object logger                                       # line 11
cdef public bint refresh                                        # line 23
cdef bint _paged                                                # line 42 (not public)
cdef int32_t _page_                                             # line 43
cdef void _extract_options(self)                                # line 63
cdef void _query_refresh_sync(self)                             # line 67 (no noexcept → propagates under Cython 3)
cdef void _offset_pagination_sync(self)                         # line 70

# querysource/parsers/abstract.pyx
cdef class AbstractParser:                                      # line 28
    def __cinit__(self, *args, definition: object, conditions: object,
                  query: str = None, **kwargs):                 # lines 31-37 (definition is keyword-only, required)
        self.logger = logging.getLogger(f'QS.Parser.{self._name_}')  # line 40
    # __init__: self.refresh = False (86); self._paged = False (90)
    cdef void _extract_options(self):                           # line 139; calls _query_refresh_sync (144), _offset_pagination_sync (147)
    cdef void _query_refresh_sync(self):                        # line 177
        refresh = self.conditions.pop('refresh', False)         # 180
        if isinstance(refresh, bool): self.refresh = refresh    # 181-182
        else: self.refresh = strtobool(str(refresh))            # 183-184
        # except (KeyError, AttributeError, ValueError): self.refresh = False   # 185-186
    cdef void _offset_pagination_sync(self):                    # line 204
        # _offset pop                                           # 206-209
        paged = self.conditions.pop('paged', False)             # 211
        if is_boolean(paged): self._paged = paged               # 212-213
        elif isinstance(paged, str): self._paged = strtobool(paged)  # 214-215 (ValueError uncaught)
        else: self._paged = False                               # 216-217
        # except (KeyError, AttributeError): self._paged = False     # 218-219
        # page pop → self._page_                                # 220-223
    async def set_options(self):                                # line 379; calls self._extract_options() at 390

# querysource/types/validators.pyx
cpdef object strtobool(str val):                                # line 47 (True words: y yes t true on 1; False: n no f false off 0 null; else ValueError; lowercases)
cpdef bool_t is_boolean(object value):                          # line 340
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `to_flag` | `strtobool` | function call (str only) | `types/validators.pyx:47` |
| `BaseProvider.__init__` | `to_flag` | import from `..types` | `providers/abstract.py:83-85` |
| `AbstractParser._query_refresh_sync` | `to_flag` | import at `parsers/abstract.pyx:15` | `parsers/abstract.pyx:177-186` |
| `AbstractParser._offset_pagination_sync` | `to_flag` | same import | `parsers/abstract.pyx:211-219` |

### Does NOT Exist (Anti-Hallucination)
- ~~`querysource/types/validators.pxd`~~ — no `.pxd`; `to_flag` cannot be `cimport`ed; import it like `strtobool`.
- ~~`querysource.types.to_flag`~~ / ~~`parse_flag`~~ / ~~`coerce_flag`~~ — none exist yet (grep: 0 hits); `to_flag` is created by M1.
- ~~`validators.to_boolean` as a Python-callable~~ — it is `cdef` (line 350) and returns `'TRUE'`/`'FALSE'` strings.
- ~~`AbstractParser.paged` / Python access to `_paged`~~ — `_paged` is a non-public `cdef bint`.
- ~~Any reader of `_paged` outside `parsers/abstract.pyx`~~ — none (`providers/sql.py:68` only lists `"paged"` as a reserved key).
- ~~`AbstractParser(query=..., conditions=...)` without `definition`~~ — raises `TypeError: __cinit__() needs keyword-only argument definition`.
- ~~Existing tests for provider `refresh` parsing~~ — none.
- ~~`legacy_implicit_noexcept` compiler directive~~ — not set in `setup.py`; `cythonize(extensions)` at line 160 uses defaults.

### Edit Sites (Blueprint Anchors)

Verified against: `498a56e`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/types/validators.pyx` | MODIFY | `cpdef object strtobool(str val):` | `validators.pyx:47` | 1 |
| `querysource/types/__init__.py` | MODIFY | `from .validators import is_boolean, is_empty, strtobool` | `__init__.py:2` | 1 |
| `querysource/types/__init__.py` | MODIFY | `    'strtobool',` (inside `__all__`) | `__init__.py:98` | 1 |
| `querysource/providers/abstract.py` | MODIFY | `self._refresh = bool(self._conditions['refresh'])` | `abstract.py:84` | 1 |
| `querysource/providers/abstract.py` | MODIFY | `from ..parsers.abstract import AbstractParser` (add import after) | `abstract.py:20` | 1 |
| `querysource/parsers/abstract.pyx` | MODIFY | `from ..types import strtobool, is_boolean` | `abstract.pyx:15` | 1 |
| `querysource/parsers/abstract.pyx` | MODIFY | `self.refresh = strtobool(str(refresh))` | `abstract.pyx:184` | 1 |
| `querysource/parsers/abstract.pyx` | MODIFY | `if is_boolean(paged):` | `abstract.pyx:212` | 1 |
| `tests/test_flag_conditions.py` | CREATE | — | — | — |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Keep `to_flag` pure; logging happens in callers with their own logger
  (`self._logger` in providers, `self.logger` in parsers).
- Warning text: name the condition and show `repr(value)[:64]` — never echo
  unbounded user input.
- Follow `.claude/rules/cython-development.md`: typed locals (`cdef object`/`cdef str`)
  inside `to_flag`; no `.pxd` needed for this change.
- Google-style docstring on `to_flag`.

### Known Risks / Gotchas
- **Cython 3 exception propagation**: any new exception escaping a `cdef void`
  in the parser fails `set_options()`. Catch `ValueError` explicitly in both parser blocks.
- **`bool` ⊂ `int`**: check `isinstance(value, bool)` before the int branch.
- **`strtobool` takes `str`**: passing a non-`str` raises `TypeError`, not
  `ValueError` — only call it with a `str`.
- **Rebuild required**: `make build-inplace` after M1 and M3, or tests run
  against stale `.so` files.
- **Behavior change**: the parser's `refresh=''` flips from `False` to `True`;
  `paged=''` goes from raising to `True`. Intended (brainstorm decision).
- **Whitespace**: `' FALSE '` → `False` (strip before lookup).
- **Repeated query keys** (`?refresh=a&refresh=b` → list) → `ValueError` → warn + `False`.
- `uap.py:107` deletes `refresh` from its own conditions afterwards — unaffected.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| — | — | none new (`Cython==3.0.11` already a build dep) |

---

## Worktree Strategy

- **Isolation**: one feature worktree for FEAT-149; the `sdd-coder` engine may give
  each task its own sub-worktree inside it.
- **Module dependency graph**:
  - M2 → M1 (imports `to_flag` from `querysource.types`)
  - M3 → M1 (imports `to_flag` in `parsers/abstract.pyx`)
  - M4 → M1, M2, M3 (tests exercise all three)
  - M2 and M3 have no edge between them → may run concurrently.
- **Shared files**: none between modules (M1: `types/*`; M2: `providers/abstract.py`;
  M3: `parsers/abstract.pyx`; M4: new test file).
- **Exclusive resources**: `make build-inplace` (Cython rebuild) for M1 and M3 →
  those tasks are `parallel: false`.
- **Cross-feature dependencies**: none known. `providers/abstract.py` and
  `parsers/abstract.pyx` are hot files — check for in-flight specs touching them before starting.

---

## 8. Open Questions

- [x] Flow type — *Resolved in brainstorm*: feature → dev
- [x] Unrecognized value behavior — *Resolved in brainstorm*: treat as False + log warning
- [x] Empty value (`?refresh`, `?refresh=`) — *Resolved in brainstorm*: True (flag style)
- [x] Scope — *Resolved in brainstorm*: shared helper used by provider + parser, with tests
- [x] Non-string inputs — *Resolved in brainstorm*: None→False, 1/0→True/False, other numbers → warn + False
- [x] Helper location — *Resolved in brainstorm*: Cython `cpdef` in `querysource/types/validators.pyx`
- [x] `externalProvider.refresh()` — *Resolved in brainstorm*: keep as-is (always True)
- [x] Test depth — *Resolved in brainstorm*: unit tests for helper + provider (+ parser parity); no HTTP-level test
- [x] `bytes` values — *Resolved in brainstorm*: unrecognized → warn + False
- [x] Sibling `paged` handling — *Resolved in brainstorm*: fold in; parse `paged` with the same helper and fallback

---

## 9. Design Research Cross-Check

> Model: `gpt-5.6-luna` · Status: skipped (exploration doc status is `exploration`, not `accepted`)

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-24 | Jesus Lara (with Claude) | Initial draft from `falsy-refresh.brainstorm.md`; corrected `paged` failure mode (propagates under Cython 3) |
