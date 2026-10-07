# TASK-858: Evaluate qsurl residual regex leaves on RE2 only

**Feature**: FEAT-164 — qsurl residual regex on a linear-time engine (RE2)
**Spec**: `sdd/specs/qsurl-residual-fixes.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned
**discovered_from**: issue:2241b8e60919

---

## Context

Ledger issue `issue:2241b8e60919` (vulnerability, major): the qsurl residual
`regex`/`iregex` leaf runs an attacker-controlled pattern through
`pd.Series.str.contains(regex=True)` on the event-loop thread. The static
screen `_check_regex_safety` (commit `acb6bdf5`) is bypassable, and pandas 3
silently falls back from RE2 to Python's backtracking `re` whenever the
pattern contains lookaround/backreferences: `(?=a)(a|a)+$` blocked for 11.2 s
on a single 27-char row. Implements spec §3 Module 1 / §2.

---

## Scope

- Add a module-level guarded `pyarrow` / `pyarrow.compute` import to `residual.py`.
- Add `_re2_contains(col, pattern, ignore_case)` that matches with
  `pc.match_substring_regex` only, maps `pa.ArrowInvalid` to
  `QSUrlError("lower", "invalid regex ...")`, and fails closed (`QSUrlError`
  mentioning pyarrow) when pyarrow is missing.
- Route the `regex`/`iregex` branch of `_leaf_mask` through
  `_check_regex_safety(value)` then `_re2_contains(...)`.
- Rewrite the issue comment block above `_MAX_REGEX_PATTERN_LENGTH` to state
  RE2 is the primary mitigation and the screen is defence in depth.
- Append the spec §4 unit tests to `tests/qsurl/test_residual.py`.

**NOT in scope**: removing `_check_regex_safety`; moving residual evaluation
off the event loop; DB-side regex pushdown in `translate.py`; making pyarrow a
core dependency; the `contains`/`icontains` leaves.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/qsurl/residual.py` | MODIFY | guarded pyarrow import, `_re2_contains`, regex branch, comment |
| `tests/qsurl/test_residual.py` | MODIFY | bypass / lookaround / no-`re` / fail-closed / semantics tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import pandas as pd                                   # verified: querysource/qsurl/residual.py:8
from .errors import QSUrlError                        # verified: querysource/qsurl/residual.py:10
import pyarrow as pa                                  # verified: pyarrow 25.0.1 in .venv; pyproject.toml:171 (`parquet` extra)
import pyarrow.compute as pc                          # verified: pc.match_substring_regex(arr, pattern=..., ignore_case=...)
import querysource.qsurl.residual as residual         # verified: tests/qsurl/test_residual.py:8
from querysource.qsurl import QSUrlError, ResidualPlan  # verified: tests/qsurl/test_residual.py:9
```

### Existing Signatures to Use
```python
# querysource/qsurl/residual.py
_MAX_REGEX_PATTERN_LENGTH = 200                           # line 35
_NESTED_QUANTIFIER_RE = re.compile(...)                   # line 36
def _check_regex_safety(pattern: str) -> None             # line 39 (unchanged)
def _column(df: pd.DataFrame, name: str) -> pd.Series     # line 58
def _leaf_mask(df: pd.DataFrame, leaf: dict) -> pd.Series # line 71; regex branch lines 110-118
def evaluate(df: pd.DataFrame, node: dict) -> pd.Series   # line 123
# querysource/qsurl/errors.py:20
class QSUrlError(Exception):
    def __init__(self, kind: str, message: str, *, offset=0, found=None, expected=None, pointer="", code=400)
    # attributes used by tests: .kind, .message
# tests/qsurl/conftest.py:22
def stores_df() -> pd.DataFrame  # fixture; has columns "store_id", "city"
```
Verified behaviour (pandas 3.0.6 / pyarrow 25.0.1): `pc.match_substring_regex`
raises `pyarrow.ArrowInvalid` (subclass of `ValueError`) for `(?=`, `\1`, `(`;
returns null for null input; `pa.array(series_of_StringDtype("pyarrow"))` works.

### Does NOT Exist
- ~~`pd.Series.str.contains(..., engine="re2")`~~ — no engine switch in pandas.
- ~~`re2` / `google-re2` package~~ — not a dependency; use pyarrow's RE2.
- ~~`residual._re2_contains`~~ before this task — you create it.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/qsurl/residual.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/qsurl/test_residual.py",
      "action": "MODIFY"
    }
  ],
  "contract_symbols": [
    "sym:querysource/qsurl/residual.py#_leaf_mask",
    "sym:querysource/qsurl/residual.py#_check_regex_safety",
    "sym:querysource/qsurl/residual.py#evaluate"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Never call `str.contains(..., regex=True)` with a user pattern — pandas
  decides the engine from the pattern, so an attacker can force Python `re`.
- Keep `_check_regex_safety` and its messages unchanged (existing tests assert them).
- Keep the error message prefix `invalid regex` (existing `test_bad_regex_is_lower_error`).
- `test_module_never_uses_eval` bans `.query(` / `.eval(` substrings in `residual.py` — do not introduce them.

---

## Implementation Blueprint

### Steps (in order)
1. Insert the guarded pyarrow import below `import pandas as pd` — *why*: pyarrow is an optional extra; the module must still import without it (G3).
2. Rewrite the comment block at lines 23-34 — *why*: it currently says no engine change is made, which becomes false.
3. Insert `_re2_contains` above `_column` — *why*: single place that owns the RE2 call and error mapping.
4. Replace the regex branch body — *why*: removes the only path to Python `re` (G1).
5. Append the tests, run the Validation Commands, `ruff check` both files.

### `querysource/qsurl/residual.py` (MODIFY) — import
```python
# occurrences: 1 (verified: grep -c '^import pandas as pd$' querysource/qsurl/residual.py)
# AFTER — insert below `import pandas as pd` (verified: querysource/qsurl/residual.py:8)

try:
    import pyarrow as pa
    import pyarrow.compute as pc
except ImportError:  # pragma: no cover - pyarrow ships with the `parquet` extra
    pa = None
    pc = None
```

### `querysource/qsurl/residual.py` (MODIFY) — comment block
```python
# occurrences: 1 (verified: grep -c 'Ledger issue:2241b8e60919 (code review' querysource/qsurl/residual.py)
# REPLACE lines 23-34 (from `# Ledger issue:2241b8e60919 (code review, FEAT-152): ...` through
# `# whole-column pandas call (which cannot be interrupted per-row anyway).`)
# FILL IN: comment stating (a) regex/iregex leaves run only on RE2 via
# pyarrow.compute.match_substring_regex (linear time) because pandas' str.contains falls back
# to Python `re` for lookaround/backreferences; (b) without pyarrow they fail closed;
# (c) the length cap + nested-quantifier screen below stay as defence in depth — bounded by spec §7 D1-D3.
```

### `querysource/qsurl/residual.py` (MODIFY) — helper
```python
# occurrences: 1 (verified: grep -c '^def _column(df: pd.DataFrame, name: str) -> pd.Series:$' querysource/qsurl/residual.py)
# BEFORE — insert above `def _column(df: pd.DataFrame, name: str) -> pd.Series:` (verified: residual.py:58)
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
```
**Why**: fixed by spec §3 skeleton; the index must be preserved because `apply` masks `df[mask]`.

### `querysource/qsurl/residual.py` (MODIFY) — regex branch
```python
# occurrences: 1 (verified: grep -c '    if expr in ("regex", "iregex"):' querysource/qsurl/residual.py)
# REPLACE the branch body (residual.py:111-118, the try/except around str.contains) with:
    if expr in ("regex", "iregex"):
        _check_regex_safety(value)
        return _re2_contains(col, value, ignore_case=(expr == "iregex"))
```

### `tests/qsurl/test_residual.py` (MODIFY) — append at EOF (124 lines today)
```python
import time  # add to the top-level imports

import pandas as pd  # add to the top-level imports


BYPASS_PATTERNS = ["(a|a)+$", "(a|aa)*b$", "((a+))+$", "(a+){2,}$", "(.*a){25}$"]


@pytest.mark.parametrize("pattern", BYPASS_PATTERNS)
def test_screen_bypass_patterns_run_in_linear_time(pattern):
    """Ledger issue:2241b8e60919: patterns the static screen misses stay linear on RE2."""
    df = pd.DataFrame({"city": ["a" * 5000 + "!"] * 50})
    # FILL IN: time residual.evaluate on a `regex` leaf; assert < 1.0 s and mask all False — AC3


@pytest.mark.parametrize("pattern", ["(?=a)(a|a)+$", "(?!x)a", "(?<=a)b", r"(a)\1"])
def test_lookaround_and_backreference_are_rejected(pattern):
    """Lookaround/backrefs used to force pandas onto Python `re`; RE2 rejects them."""
    df = pd.DataFrame({"city": ["a" * 26 + "!"]})
    # FILL IN: QSUrlError kind "lower", message startswith "invalid regex", elapsed < 1.0 s — AC2


def test_regex_never_uses_python_re_engine(stores_df, monkeypatch):
    # FILL IN: monkeypatch pd.core.strings.accessor.StringMethods.contains to raise AssertionError;
    # evaluate a `regex` and an `iregex` leaf on stores_df and assert non-empty correct masks — G1


def test_regex_fails_closed_without_pyarrow(stores_df, monkeypatch):
    # FILL IN: monkeypatch.setattr(residual, "pc", None); regex leaf -> QSUrlError kind "lower",
    # "pyarrow" in message — AC4


def test_regex_mask_semantics():
    # FILL IN: index [10, 11, 12, 13], values ["San Jose", "SAN DIEGO", None, "x"]:
    # regex "^SAN " -> [F, T, F, F]; iregex -> [T, T, F, F]; mask.index equals input index;
    # numeric column [1, 22, None] with regex "2" -> [F, T, F] — G4
```

### FILL IN checklist
- [ ] comment block rewritten (spec §7 D1-D3)
- [ ] five tests implemented, imports placed at module top (ruff I001/E402)

---

## Acceptance Criteria

- [ ] No `str.contains(..., regex=True)` on a user pattern in the regex branch (spec AC1)
- [ ] `(?=a)(a|a)+$` rejected in < 1 s (AC2); bypass patterns linear (AC3); fail closed without pyarrow (AC4)
- [ ] `pytest tests/qsurl/test_residual.py tests/e2e/test_qsurl_dry_run.py -q` passes (AC5)
- [ ] `ruff check querysource/qsurl/residual.py tests/qsurl/test_residual.py` clean (AC6)

---

## Validation Commands

- `pytest tests/qsurl/test_residual.py -q`
- `pytest tests/e2e/test_qsurl_dry_run.py -q`

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug qsurl-residual-fixes --feature-id FEAT-164`).
2. Verify the Codebase Contract, implement from the blueprint, complete every FILL IN.
3. Run the Validation Commands and `ruff check` on both files.
4. Commit only the two listed files; close with `scripts/sdd/close_task.sh TASK-858 qsurl-residual-fixes verified`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none
