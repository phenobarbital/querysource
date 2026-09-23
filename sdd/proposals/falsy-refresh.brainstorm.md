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
tags: [refresh, cache, boolean-coercion, query-conditions]
---

# Brainstorm: Falsy Refresh — `refresh="false"` must not bypass the cache

**Date**: 2026-09-24
**Author**: Jesus Lara (with Claude)
**Status**: exploration
**Recommended Option**: A

---

## Problem Statement

`BaseProvider.__init__` reads the `refresh` condition with plain `bool()`
(`querysource/providers/abstract.py:83-85`):

```python
if 'refresh' in self._conditions:
    self._refresh = bool(self._conditions['refresh'])
```

Conditions that arrive over HTTP (query string / form values) are **strings**,
so `refresh=false`, `refresh=0`, `refresh=no` all evaluate to `True`. `QS.query()`
(`querysource/queries/qs.py:385,414`) uses `self._qs.refresh()` to decide whether
to ignore an existing cache entry, so any client sending an explicit "false"
silently forces a fresh database hit — defeating the Redis result cache and
adding load to the backing datasource.

The parser already does this correctly (`AbstractParser._query_refresh_sync`,
`querysource/parsers/abstract.pyx:177-186`, uses `strtobool`), so provider and
parser **disagree** on the same input. Only the provider's value drives the
cache decision.

**Affected**: API clients and dashboards that pass `refresh=false` explicitly
(they get uncached, slower responses); ops (unnecessary DB load).

## Constraints & Requirements

- One shared coercion helper; `BaseProvider` and `AbstractParser` must yield the
  same boolean for the same input.
- Helper lives in Cython, next to `strtobool` in `querysource/types/validators.pyx`
  (user decision) → requires `make build-inplace`.
- Truth table (user decisions, Rounds 1–2):
  - `bool` → passes through.
  - `None` → `False`.
  - `int` `1` / `0` → `True` / `False`; any other number → unrecognized.
  - Strings, case-insensitive, whitespace-stripped: the `strtobool` vocabulary
    (`y yes t true on 1` → True; `n no f false off 0 null` → False).
  - **Empty string** (bare `?refresh` or `?refresh=`) → `True` (flag style).
  - Anything else (e.g. `"maybe"`, `2`, `1.5`, lists) → **`False` + a warning log**.
    Never raise to the client; never bypass the cache by accident.
- `externalProvider.refresh()` keeps always returning `True` — out of scope.
- No change to the public `refresh()` method signatures or the `refresh`
  condition name (`describe.py:243` advertises `"refresh_param": "refresh"`).
- Tests: parametrized unit truth table for the helper, a provider test proving
  `refresh="false"` → `refresh() is False`, and a parser parity test.

---

## Options Explored

### Option A: New `cpdef` flag parser in `validators.pyx`, used by provider and parser

Add a single Cython function beside `strtobool` that implements the truth table
above and raises `ValueError` for unrecognized values. It is exported from
`querysource.types`. `BaseProvider` and `AbstractParser._query_refresh_sync`
both call it; each catches `ValueError`, logs a warning through its own logger
(`self._logger` / module logger), and falls back to `False`. The helper stays
pure (no logging inside Cython), the fallback policy is in one tiny, identical
place in each caller.

✅ **Pros:**
- Single source of truth for the "flag" semantics; provider and parser can no
  longer drift.
- Reusable for other flag-style conditions later (`paged`, etc.).
- Matches the user's choice of placing it in Cython next to `strtobool`.

❌ **Cons:**
- Requires a Cython rebuild (`make build-inplace`) for both `validators.pyx` and
  `parsers/abstract.pyx`.
- `validators.pyx` has no `.pxd`, so the parser imports it as a Python-level
  `cpdef` symbol (same as `strtobool` today) — no `cimport` gain.

📊 **Effort:** Low

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `Cython` (existing build dep) | compiles `validators.pyx` / `abstract.pyx` | `make build-inplace` |
| `pytest` + `pytest-asyncio` | truth-table + provider/parser tests | existing |

🔗 **Existing Code to Reuse:**
- `querysource/types/validators.pyx:47` — `strtobool` vocabulary (reuse, don't duplicate the word lists).
- `querysource/types/__init__.py:2,95-98` — re-export surface.
- `querysource/parsers/abstract.pyx:177-186` — call site to switch over.

---

### Option B: Reuse `querysource.types.converters.to_boolean` in the provider only

Replace `bool(...)` at `providers/abstract.py:84` with the existing
`converters.to_boolean` (`querysource/types/converters.pyx:43`), wrapped in a
`try/except ValueError → False`. Parser untouched.

✅ **Pros:**
- Smallest possible diff; no new symbol.

❌ **Cons:**
- Wrong semantics for the agreed truth table: `to_boolean('')` raises (we want
  `True`), `to_boolean(2)` returns `True` via `bool(obj)` (we want warn + `False`).
- Provider and parser still use two different code paths → can drift again.
- Changing `to_boolean` itself to fit would alter every other caller.

📊 **Effort:** Low

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| — | — | no new deps |

🔗 **Existing Code to Reuse:**
- `querysource/types/converters.pyx:43` — `to_boolean`.

---

### Option C: Parser as the single source of truth (provider delegates)

The parser already pops and coerces `refresh` from the original `conditions`
dict. `BaseProvider.refresh()` would return `self._parser.refresh` when a parser
exists, and only fall back to its own coercion otherwise.

✅ **Pros:**
- No duplicated parsing in the happy path; unconventional but eliminates the
  provider-side read entirely.

❌ **Cons:**
- Providers with `__parser__ = None`, or where parser construction fails (the
  `except Exception` at `providers/abstract.py:~108` just logs), still need a
  fallback → two paths anyway.
- Couples cache semantics to parser lifecycle (parser pops `refresh` in
  `_query_refresh_sync`, which runs at build time — ordering-sensitive).
- Parser's current `''` → `False` contradicts the agreed flag semantics, so the
  parser needs changing too.

📊 **Effort:** Medium

🔗 **Existing Code to Reuse:**
- `querysource/parsers/abstract.pyx:86,177-186` — `self.refresh` attribute.

---

### Option D: Normalize at ingress (HTTP handlers) before conditions reach `QS`

Coerce `refresh` to a real bool where query-string/body conditions are built in
`querysource/handlers/`, so providers/parsers always see a `bool`.

✅ **Pros:**
- Fixes the value once, at the trust boundary.

❌ **Cons:**
- The Python API path (`QS(conditions={'refresh': 'false'})`), scheduler jobs and
  MultiQuery sources bypass the handlers → still broken.
- Multiple handlers build conditions; easy to miss one.
- `bool(...)` in the provider stays a latent footgun.

📊 **Effort:** Medium

🔗 **Existing Code to Reuse:**
- `querysource/handlers/` — condition assembly (not audited in detail).

---

## Recommendation

**Option A** is recommended because:

- It is the only option that fixes every entry path (HTTP, Python API,
  scheduler, MultiQuery) *and* removes the provider/parser divergence that made
  this bug possible.
- Option B fails the agreed truth table (`''`, `2`) and leaves two code paths.
- Option C still needs a provider-side fallback and couples cache behavior to
  parser construction order.
- Option D leaves non-HTTP paths broken.
- Tradeoff accepted: a Cython rebuild step, and a small `try/except` duplicated
  in the two call sites (kept there deliberately so the helper stays pure and
  each caller logs with its own logger).

---

## Feature Description

### User-Facing Behavior
- `?refresh=false`, `?refresh=0`, `?refresh=no`, `?refresh=off`, `?refresh=null`
  → served from cache when a cache entry exists (previously: always bypassed).
- `?refresh=true|1|yes|on|t|y` and bare `?refresh` / `?refresh=` → bypass cache.
- `?refresh=maybe` (or any unrecognized value) → treated as `false`; a warning
  is logged server-side; no error returned to the client.
- JSON / Python API: `True`/`False` pass through, `None` → false, `1`/`0` → true/false.

### Internal Behavior
1. `validators.pyx` gains a flag-coercion `cpdef` that applies the truth table
   and raises `ValueError` on unrecognized input; exported via `querysource.types`.
2. `BaseProvider.__init__` replaces `bool(self._conditions['refresh'])` with the
   helper; on `ValueError` logs a warning via `self._logger` and sets `False`.
   The key is still deleted from `self._conditions`.
3. `AbstractParser._query_refresh_sync` uses the same helper with the same
   fallback (keeps catching `KeyError`/`AttributeError`).
4. `QS.query()` is unchanged — it simply receives a correct boolean.

### Edge Cases & Error Handling
- Whitespace/case: `" FALSE "` → `False`.
- `bytes` values: decode as ASCII then treat as string (spec to confirm; see Open Questions).
- `bool` is a subclass of `int` — check `bool` first so `True`/`False` aren't
  routed through the int branch (harmless, but explicit).
- Numeric `1.0`/`0.0`: per agreed rule "any other number → unrecognized" → warn + False.
- Lists/dicts (e.g. repeated `?refresh=a&refresh=b` producing a list) → warn + False.
- Warning log must not echo unbounded user input — truncate/`repr` the value.
- `uap.py:107` deletes `refresh` from its own conditions afterwards — unaffected.

---

## Capabilities

### New Capabilities
- `flag-condition-coercion`: shared Cython helper that converts a flag-style
  condition value to `bool` with a fixed truth table.

### Modified Capabilities
- `provider-refresh-condition`: `BaseProvider` parses `refresh` with the shared helper.
- `parser-refresh-condition`: `AbstractParser` parses `refresh` with the shared
  helper (empty string now `True`, matching the provider).

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `querysource/types/validators.pyx` | extends | new `cpdef` helper; rebuild required |
| `querysource/types/__init__.py` | extends | re-export helper, add to `__all__` |
| `querysource/providers/abstract.py` | modifies | lines 83-85 |
| `querysource/parsers/abstract.pyx` | modifies | `_query_refresh_sync` (177-186); rebuild required |
| `querysource/queries/qs.py` | depends on | consumes `refresh()`; behavior now correct, no code change |
| `querysource/providers/external.py` | unchanged | `refresh()` still hard-coded `True` |
| Behavior change | minor | `refresh=false` now honors cache; parser's `''` flips from False → True |

No new dependencies. No API/schema change.

---

## Code Context

### User-Provided Code
```python
# Source: user-provided (invocation notes)
# refresh = bool(raw) (providers/abstract.py:83-85) — the string "false" is truthy.
```

### Verified Codebase References

#### Classes & Signatures
```python
# From querysource/providers/abstract.py
class BaseProvider(ABC):                                   # line 23
    def __init__(self, slug: str = '', query: Any = None, qstype: str = '',
                 connection: Callable = None,
                 definition: Union[QueryModel, dict] = None,
                 conditions: dict = None, request: web.Request = None,
                 **kwargs):                                # line 38
        self._logger = logging.getLogger(f'QS.{self.__name__}')  # line 50
        self._refresh: bool = False                        # line 71
        if conditions:
            self._conditions = copy.deepcopy(conditions)   # line 82
            if 'refresh' in self._conditions:              # line 83
                self._refresh = bool(self._conditions['refresh'])  # line 84  <-- BUG
                del self._conditions['refresh']            # line 85
    def refresh(self):                                     # line 257
        return self._refresh                               # line 258
```
Note: the parser is built with the ORIGINAL `conditions` (not the provider's copy),
so it sees and pops `refresh` independently.

```python
# From querysource/parsers/abstract.pyx
from ..types import strtobool, is_boolean                  # line 15
    self.refresh = False                                   # line 86 (in __init__)
    self._query_refresh_sync()                             # lines 144, 343
    cdef void _query_refresh_sync(self):                   # line 177
        refresh = self.conditions.pop('refresh', False)    # line 180
        if isinstance(refresh, bool): self.refresh = refresh
        else: self.refresh = strtobool(str(refresh))       # line 184
        # except (KeyError, AttributeError, ValueError): self.refresh = False  # 185-186
```

```python
# From querysource/types/validators.pyx
cpdef object strtobool(str val):                           # line 47
    # True: 'y','yes','t','true','on','1'; False: 'n','no','f','false','off','0','null'
    # else raise ValueError
cpdef bool_t is_boolean(object value):                     # line 340
cdef str to_boolean(object value):                         # line 350 (returns 'TRUE'/'FALSE' strings — not reusable)
```

```python
# From querysource/types/converters.pyx
cpdef object to_boolean(object obj):                       # line 43
    # bool passthrough; bytes decoded; str -> strtobool (raises on ''); else bool(obj)
```

```python
# From querysource/queries/qs.py
refresh = self._qs.refresh()                               # line 385
if refresh is True and exists is True:                     # line 414 — skip cache
```

```python
# From querysource/providers/external.py
def refresh(self) -> bool:                                 # line 65
    return True
```

#### Verified Imports
```python
from querysource.types import strtobool, is_boolean, is_empty   # querysource/types/__init__.py:2
# 'strtobool' listed in __all__ at querysource/types/__init__.py:95-98
```

#### Key Attributes & Constants
- `BaseProvider._refresh` → `bool` (querysource/providers/abstract.py:71)
- `BaseProvider._logger` → `logging.Logger` (querysource/providers/abstract.py:50)
- `AbstractParser.refresh` → `bool` (querysource/parsers/abstract.pyx:86)
- Extension build entry: `querysource.types.validators` (setup.py:140-141)
- `describe.py:243` — `"refresh_param": "refresh"` (public contract; keep name)

### Does NOT Exist (Anti-Hallucination)
- ~~`querysource/types/validators.pxd`~~ — no `.pxd`; `cimport` of validators symbols is NOT possible without adding one.
- ~~A shared flag/boolean-condition helper~~ — nothing today implements the agreed truth table (`''` → True, non-0/1 ints → reject).
- ~~`validators.to_boolean` as a Python-callable~~ — it is `cdef` (not `cpdef`) and returns SQL strings.
- ~~Tests for `BaseProvider` refresh parsing~~ — no existing test file covers it (tests/ has parser tests like `test_sql_parser_combinations.py` but none for refresh).

---

## Parallelism Assessment

- **Internal parallelism**: None worth splitting — helper → two call sites → tests
  is a strict chain, and both call sites share one rebuild.
- **Cross-feature independence**: Touches `providers/abstract.py` and
  `parsers/abstract.pyx` — both hot files; check for in-flight specs editing
  `BaseProvider.__init__` or `_query_refresh_sync` before starting.
- **Recommended isolation**: per-spec
- **Rationale**: ~3 small sequential tasks (or a single task); a worktree per task adds overhead with no benefit.

---

## Open Questions

- [x] Flow type — *Owner: Jesus Lara*: feature → dev
- [x] Unrecognized value behavior — *Owner: Jesus Lara*: treat as False + log warning
- [x] Empty value (`?refresh`, `?refresh=`) — *Owner: Jesus Lara*: True (flag style)
- [x] Scope — *Owner: Jesus Lara*: shared helper used by provider + parser, with tests
- [x] Non-string inputs — *Owner: Jesus Lara*: None→False, 1/0→True/False, other numbers → warn + False
- [x] Helper location — *Owner: Jesus Lara*: Cython `cpdef` in `querysource/types/validators.pyx`
- [x] `externalProvider.refresh()` — *Owner: Jesus Lara*: keep as-is (always True)
- [x] Test depth — *Owner: Jesus Lara*: unit tests for helper + provider (+ parser parity); no HTTP-level test
- [ ] Should `bytes` values be decoded and accepted, or treated as unrecognized? — *Owner: Jesus Lara*
- [ ] Sibling bug: `AbstractParser` `paged` handling (`parsers/abstract.pyx:~211-214`) assigns the raw string when `is_boolean(paged)` is True (e.g. `paged="true"` → `self._paged = "true"`). Fold into this feature using the new helper, or file separately? — *Owner: Jesus Lara*
