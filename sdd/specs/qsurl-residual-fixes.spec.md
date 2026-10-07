---
# SDD flow type and base branch (FEAT-145).
type: feature
base_branch: dev
projects: [qsurl]
tags: [qsurl, residual, security, redos, regex, sdd-fix]
---

# Feature Specification: qsurl residual regex on a linear-time engine (RE2)

**Feature ID**: FEAT-164
**Date**: 2026-10-07
**Author**: agent:sdd-fix (on behalf of Jesus Lara)
**Status**: approved
**Target version**: 5.2.3
**Origin**: `/sdd-fix issue:2241b8e60919` — ledger group `fixgroup:ed1dd6f8d30a`
(vulnerability, major, discovered from `spec:FEAT-152`). Parent FEAT-152 is
complete (`completed_at` set), so the fix gets its own feature id.

---

## 1. Motivation & Business Requirements

### Problem Statement
`querysource/qsurl/residual.py::_leaf_mask` evaluates the `regex` / `iregex`
residual leaves with
`col.astype("string").str.contains(value, case=..., na=False, regex=True)`
(`residual.py:110-118`), synchronously, on the aiohttp handler's event-loop
thread. The pattern is attacker-controlled (qsurl query string).

Commit `acb6bdf5` added a static screen, `_check_regex_safety`
(`residual.py:35-55`: 200-char cap + a nested-quantifier regex), but the
ledger issue was never closed and the screen is trivially bypassed. Verified
on dev (pandas 3.0.6, pyarrow 25.0.1):

| Pattern | Passes screen? | Why it is still dangerous |
|---|---|---|
| `(a\|a)+$`, `(a\|aa)*b$` | yes | overlapping alternation, no `+`/`*` inside the group |
| `((a+))+$` | yes | nested parentheses defeat `[^()]*` |
| `(a+){2,}$`, `(.*a){25}$` | yes | counted repetition is not matched by `[+*]` |
| `(?=a)(a\|a)+$` | yes | **lookahead forces pandas off RE2 onto Python `re`** |

pandas 3 routes Arrow-backed string `str.contains` through
`pyarrow.compute.match_substring_regex` (RE2, linear time) **except** when
`ArrowStringArray._has_unsupported_regex(pat)` sees lookaround or a
backreference — then it silently falls back to Python's backtracking `re`.
Measured: `(?=a)(a|a)+$` against a single 27-char row (`"a"*26 + "!"`) took
**11.2 s** of blocked event loop; the same pattern without the lookahead on
RE2 takes 0.0002 s on a 5 000-char row. And when pyarrow is not installed
(it is only the optional `parquet` extra in `pyproject.toml`), `"string"`
dtype uses Python storage and every pattern runs on Python `re`.

### Goals
- G1. The `regex` / `iregex` residual leaves are evaluated **only** by RE2
  (`pyarrow.compute.match_substring_regex`); there is no code path that hands
  a user pattern to Python's `re` engine.
- G2. Patterns RE2 cannot compile (lookaround, backreferences, malformed
  syntax, repeat counts > 1000) raise `QSUrlError("lower", "invalid regex
  ...")` — same kind and message prefix as today's malformed-pattern error.
- G3. When pyarrow is unavailable the regex leaves fail closed with
  `QSUrlError("lower", ...)` instead of falling back to Python `re`.
- G4. Semantics for patterns valid in both engines are unchanged:
  search (not full-match) semantics, `iregex` case-insensitive, nulls never
  match, non-string columns are matched on their string form, the result is
  a boolean `pd.Series` aligned to the input index.

### Non-Goals (explicitly out of scope)
- Removing `_check_regex_safety`. It stays as defence in depth and keeps the
  error contract shared with the parser-level regex guard (FEAT-180 S8,
  TASK-840); its length cap also bounds RE2 compile memory.
- Moving the residual stage off the event loop (it is linear now; all
  residual leaves, not only regex, run inline — separate concern).
- Database-side regex pushed down by `qsurl/translate.py` (the database
  enforces its own engine/timeouts).
- Making pyarrow a core dependency.
- The `contains`/`icontains` leaves (they `re.escape` the value, so the
  pattern is a literal and cannot backtrack).

---

## 2. Architectural Design

### Overview
Add a private helper `_re2_contains(col, pattern, ignore_case)` in
`residual.py` that converts the column to an Arrow string array and calls
`pyarrow.compute.match_substring_regex` directly, mapping `ArrowInvalid` to
`QSUrlError("lower", "invalid regex ...")`. The `regex`/`iregex` branch keeps
calling `_check_regex_safety(value)` first, then delegates to the helper.
pyarrow is imported at module import inside `try/except ImportError`; when
absent the helper raises `QSUrlError("lower", ...)`.

This supersedes the literal `str.contains(..., regex=True)` call mandated by
`sdd/specs/qsurl-parser.spec.md` §3 Module 7 for the regex leaves only (the
ledger issue body notes that the original design itself was the gap).

### Component Diagram
```
_leaf_mask(df, leaf)  expr in ("regex", "iregex")
  ├─ _check_regex_safety(value)            (unchanged: length cap + nested-quantifier screen)
  └─ _re2_contains(col, value, ignore_case=(expr == "iregex"))     ← NEW
        ├─ pc is None ──► QSUrlError("lower", "regex filters require pyarrow ...")
        ├─ pa.array(col.astype(pd.StringDtype("pyarrow")))
        ├─ pc.match_substring_regex(arr, pattern=value, ignore_case=...)
        │      └─ ArrowInvalid ──► QSUrlError("lower", "invalid regex `<value>`: <err>")
        └─ .fill_null(False) ──► pd.Series(bool, index=col.index)
```

### Integration Points
| Existing Component | Integration Type | Notes |
|---|---|---|
| `_leaf_mask` regex branch (`residual.py:110-118`) | modifies | delegate to `_re2_contains` |
| `_check_regex_safety` (`residual.py:39`) | uses | unchanged |
| `QSUrlError` (`querysource/qsurl/errors.py`) | uses | kind `"lower"` |
| `tests/qsurl/test_residual.py` | extends | bypass/RE2/fail-closed tests |

### Data Models
None.

### New Public Interfaces
None (private helper only).

---

## 3. Module Breakdown

#### Delegation-eligible modules
| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: RE2-only regex leaf + tests | yes | helper signature, error messages, import guard and test cases are fixed below | — |

### Module 1: RE2-only regex residual leaf
- **Path**: `querysource/qsurl/residual.py`, `tests/qsurl/test_residual.py`
- **Responsibility**: evaluate `regex`/`iregex` exclusively with RE2; fail
  closed without pyarrow; prove the bypass patterns are neutralised.
- **Depends on**: nothing new (pyarrow is already installed in dev/CI via
  the `parquet` extra; pandas 3 uses it for `"string"` dtype).
- **Interface Skeleton**:
  ```python
  # querysource/qsurl/residual.py — after `import pandas as pd`
  try:
      import pyarrow as pa
      import pyarrow.compute as pc
  except ImportError:  # pragma: no cover - pyarrow ships with the `parquet` extra
      pa = None
      pc = None


  def _re2_contains(col: pd.Series, pattern: str, ignore_case: bool) -> pd.Series:
      """Search ``pattern`` in ``col`` with RE2 only (linear time, never Python ``re``).

      Args:
          col: column to match; non-string values are matched on their string form.
          pattern: user-supplied regex (RE2 syntax).
          ignore_case: True for ``iregex``.

      Returns:
          Boolean mask aligned to ``col.index``; nulls never match.

      Raises:
          QSUrlError: kind "lower" when pyarrow is unavailable or RE2 rejects the
              pattern (lookaround, backreferences, malformed syntax).
      """
      if pc is None:
          raise QSUrlError("lower", "regex filters require pyarrow (RE2 engine), which is not installed")
      arrow = pa.array(col.astype(pd.StringDtype("pyarrow")))
      try:
          matched = pc.match_substring_regex(arrow, pattern=pattern, ignore_case=ignore_case)
      except pa.ArrowInvalid as err:
          raise QSUrlError("lower", f"invalid regex `{pattern}`: {err}") from err
      return pd.Series(matched.fill_null(False).to_numpy(zero_copy_only=False), index=col.index, dtype=bool)

  # _leaf_mask regex branch (replaces residual.py:110-118)
      if expr in ("regex", "iregex"):
          _check_regex_safety(value)
          return _re2_contains(col, value, ignore_case=(expr == "iregex"))
  ```
  Also update the issue comment block above `_MAX_REGEX_PATTERN_LENGTH`
  (`residual.py:23-34`) so it describes RE2 as the primary mitigation and the
  screen as defence in depth (the current text says the spec mandates
  `str.contains` and that no timeout/engine change is made).

---

## 4. Test Specification

### Unit Tests (append to `tests/qsurl/test_residual.py`)
| Test | Description |
|---|---|
| `test_screen_bypass_patterns_run_in_linear_time` | parametrized over `(a\|a)+$`, `(a\|aa)*b$`, `((a+))+$`, `(a+){2,}$`, `(.*a){25}$`; column of 50 rows `"a"*5000 + "!"`; `evaluate` returns an all-False mask in < 1 s (wall clock) |
| `test_lookaround_and_backreference_are_rejected` | parametrized over `(?=a)(a\|a)+$`, `(?!x)a`, `(?<=a)b`, `(a)\1`; `QSUrlError`, kind `"lower"`, message starts with `"invalid regex"`; with a 27-char `"a"*26+"!"` row it returns in < 1 s (never reaches Python `re`) |
| `test_regex_never_uses_python_re_engine` | monkeypatch `pd.core.strings.accessor.StringMethods.contains` to raise `AssertionError`; `regex` and `iregex` leaves still evaluate correctly |
| `test_regex_fails_closed_without_pyarrow` | monkeypatch `residual.pc` to `None`; `regex` leaf raises `QSUrlError` kind `"lower"`, message contains `"pyarrow"` |
| `test_regex_mask_semantics` | index `[10, 11, 12, 13]`, values `["San Jose", "SAN DIEGO", None, "x"]`: `regex ^SAN ` → `[F, T, F, F]`; `iregex ^SAN ` → `[T, T, F, F]`; mask index equals input index; numeric column `[1, 22, None]` with `regex 2` → `[F, T, F]` |
| existing `test_bad_regex_is_lower_error`, `test_nested_quantifier_regex_is_rejected`, `test_overlong_regex_is_rejected`, `test_safe_regex_still_matches`, the `iregex ^SAN ` parametrized case | unchanged, still green |

### Integration Tests
| Test | Description |
|---|---|
| `pytest tests/qsurl -q` | whole qsurl suite green |
| `pytest tests/e2e/test_qsurl_dry_run.py -q` | handler dry-run path unchanged |

---

## 5. Acceptance Criteria

- [ ] AC1. `querysource/qsurl/residual.py` contains no `str.contains(` call with `regex=True` on a user pattern in the `regex`/`iregex` branch; that branch calls `_check_regex_safety(value)` then `_re2_contains(...)`.
- [ ] AC2. `(?=a)(a|a)+$` against `"a"*26 + "!"` raises `QSUrlError` (kind `"lower"`) in well under 1 s (was 11 s).
- [ ] AC3. `(a|a)+$`, `((a+))+$`, `(.*a){25}$` against 50 rows of 5 001 chars evaluate in < 1 s.
- [ ] AC4. With `residual.pc = None` the regex leaves raise `QSUrlError("lower", ...)` mentioning pyarrow.
- [ ] AC5. `pytest tests/qsurl tests/e2e/test_qsurl_dry_run.py -q` passes.
- [ ] AC6. `ruff check querysource/qsurl/residual.py tests/qsurl/test_residual.py` reports nothing new.

---

## 6. Codebase Contract

### Verified Imports
```python
import pandas as pd                                   # verified: residual.py:8
from .errors import QSUrlError                        # verified: residual.py:10
import pyarrow as pa                                  # verified: pyarrow 25.0.1 in .venv; pyproject.toml:171 (`parquet` extra)
import pyarrow.compute as pc                          # verified: pc.match_substring_regex(arr, pattern=, ignore_case=)
import querysource.qsurl.residual as residual         # verified: tests/qsurl/test_residual.py:8
from querysource.qsurl import QSUrlError, ResidualPlan  # verified: tests/qsurl/test_residual.py:9
```

### Existing Signatures
```python
# querysource/qsurl/residual.py
_MAX_REGEX_PATTERN_LENGTH = 200                           # line 35
_NESTED_QUANTIFIER_RE = re.compile(...)                   # line 36
def _check_regex_safety(pattern: str) -> None             # line 39
def _column(df: pd.DataFrame, name: str) -> pd.Series     # line 58
def _leaf_mask(df: pd.DataFrame, leaf: dict) -> pd.Series # line 71; regex branch at 110-118
def evaluate(df: pd.DataFrame, node: dict) -> pd.Series   # line 123
# querysource/qsurl/errors.py
class QSUrlError(Exception)  # QSUrlError(kind, message, *, offset=0, found=None, expected=None, pointer="", code=400); .kind, .message  (errors.py:20)
# tests/qsurl — `stores_df` fixture (conftest), used by every residual test
```
Verified behaviour (pandas 3.0.6 / pyarrow 25.0.1): `pc.match_substring_regex`
raises `pyarrow.ArrowInvalid` (a `ValueError` subclass) for `(?=`, `\1`,
`(`; returns nulls for null input (hence `fill_null(False)`).

### Does NOT Exist (Anti-Hallucination)
- ~~`pd.Series.str.contains(..., engine="re2")`~~ — pandas has no engine switch; the fallback decision is internal (`ArrowStringArray._has_unsupported_regex`).
- ~~`re2` / `google-re2` Python package~~ — not a dependency; use pyarrow's bundled RE2.

### Edit Sites (Blueprint Anchors)
Verified against: `6b897b2c` (dev, 2026-10-07)

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/qsurl/residual.py` | MODIFY (insert guarded import after) | `import pandas as pd` | `residual.py:8` | 1 |
| `querysource/qsurl/residual.py` | MODIFY (rewrite comment block) | `# Ledger issue:2241b8e60919 (code review, FEAT-152): the \`regex\` leaf runs an` | `residual.py:23` | 1 |
| `querysource/qsurl/residual.py` | MODIFY (insert `_re2_contains` before) | `def _column(df: pd.DataFrame, name: str) -> pd.Series:` | `residual.py:58` | 1 |
| `querysource/qsurl/residual.py` | MODIFY (replace branch body 111-118) | `    if expr in ("regex", "iregex"):` | `residual.py:110` | 1 |
| `tests/qsurl/test_residual.py` | MODIFY (append at EOF) | — | — | — |

---

## 7. Implementation Notes & Constraints

### Decisions
- **D1 — call RE2 directly instead of trusting pandas' dispatch.** pandas'
  fallback to Python `re` is silent and pattern-driven (attacker-chosen), so
  the only guarantee is to never call `str.contains(regex=True)` with a user
  pattern.
- **D2 — fail closed without pyarrow.** Falling back to `re` would reopen the
  issue on any deployment without the `parquet` extra.
- **D3 — keep `_check_regex_safety`.** Defence in depth, unchanged error
  messages (existing tests), parity with the parser-level guard.
- **D4 — behaviour change accepted:** lookaround, backreferences and
  Python-only escapes (e.g. `\Z`) in qsurl `regex`/`iregex` residual leaves
  are now rejected as `invalid regex` (RE2 syntax is the contract).

### Known Risks / Gotchas
- `pa.array(series)` may return a `ChunkedArray`; `fill_null` and
  `to_numpy(zero_copy_only=False)` work on both.
- Timing assertions use a generous 1 s budget so CI noise cannot flake them;
  the pre-fix runtime is ≥ 10 s.

### External Dependencies
None new (pyarrow already present via the `parquet` extra).

---

## 8. Open Questions
None.
