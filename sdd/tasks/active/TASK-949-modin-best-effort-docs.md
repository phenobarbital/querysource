# TASK-949: Document modin extra as best-effort and verify runtime fallback

**Feature**: FEAT-183 — Modin Extra Best-Effort Documentation and Pip Compatibility
**Spec**: `sdd/specs/fixgroup-f56d33e1fe34.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

**Discovered-from**: `issue:73a622d58c0b`

---

## Context

The `[modin]` optional extra declares `modin>=0.37.1` which pins `pandas<2.4`,
but QuerySource requires `pandas>=3.0`. A `[tool.uv]` override makes `uv lock`
succeed, but `pip install querysource[modin]` hits a resolver conflict. The
runtime fallback (TASK-835) already catches broad `Exception`, so only the
packaging documentation is missing.

---

## Scope

- Update the `pyproject.toml` comment on the `[modin]` extra to clearly state
  best-effort status and that only `uv` resolves correctly.
- Update the `[tool.uv]` override comment to mention pip incompatibility.
- Add a module-level docstring note to `querysource/outputs/dt/modin.py`
  about best-effort status on pandas 3.
- Verify all existing modin fallback tests pass.

**NOT in scope**: removing the extra, downgrading pandas, patching modin.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `pyproject.toml` | MODIFY | Update comments on modin extra and uv override |
| `querysource/outputs/dt/modin.py` | MODIFY | Add best-effort note to module/class docstring |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.exceptions import QueryException  # verified: querysource/outputs/dt/modin.py:1
from .abstract import OutputFormat  # verified: querysource/outputs/dt/modin.py:3
```

### Existing Signatures to Use
```python
# querysource/outputs/dt/modin.py:6
class modinFormat(OutputFormat):
    """Returns a Pandas Dataframe from a Resultset"""
    def __init__(self):  # line 10
    async def serialize(self, result, error, *args, **kwargs):  # line 20

# querysource/queries/multi/operators/abstract.py:24
class AbstractOperator(AbstractMulti):
    def __init__(self, data: dict, **kwargs) -> None:  # line 31
        # catches Exception (not just ImportError) at line 38
```

### Does NOT Exist
- ~~`querysource.outputs.dt.modin.MODIN_SUPPORTED`~~ — no such constant
- ~~`modinFormat.check_compatibility()`~~ — no such method
- ~~`querysource.compat.modin`~~ — no compatibility module exists

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "pyproject.toml",
      "action": "MODIFY"
    },
    {
      "path": "querysource/outputs/dt/modin.py",
      "action": "MODIFY"
    }
  ],
  "contract_symbols": [
    "sym:querysource/outputs/dt/modin.py#modinFormat",
    "sym:querysource/outputs/dt/modin.py#modinFormat.__init__"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Comment-only changes in `pyproject.toml` — do not change dependency versions.
- Docstring-only change in `modin.py` — do not change runtime behavior.
- The existing `except Exception` catches are correct and sufficient.

### References in Codebase
- `tests/unit/test_pandas3_modin_fallback.py` — existing tests to verify

---

## Implementation Blueprint

### Steps (in order)
1. Update the `pyproject.toml` `[modin]` extra comment — *why*: pip users need to know this extra is uv-only.
2. Update the `[tool.uv]` override comment — *why*: explain the pip incompatibility explicitly.
3. Add best-effort note to `modinFormat` class docstring — *why*: runtime users need to know modin is unsupported on pandas 3.
4. Run existing tests — *why*: verify no regression from docstring changes.

### `pyproject.toml` (MODIFY)
```toml
# occurrences: 1 (verified: grep -c '# modin is optional' pyproject.toml)
# REPLACE — replace the comment block at line 122 (verified: pyproject.toml:122)
# modin is optional and BEST-EFFORT on pandas >= 3 (FEAT-161, FEAT-183):
# modin <= 0.37.1 pins pandas < 2.4, so `pip install querysource[modin]` will
# fail with a resolver conflict.  Only `uv` honours the [tool.uv] override
# that forces pandas >= 3.  The runtime catches broad Exception on import
# (TASK-835) and falls back to pandas when modin is broken or absent.
modin = ["modin>=0.37.1", "distributed"]
```
**Why**: The current comment mentions the version pin but not the pip failure mode or best-effort status. Users who read pyproject.toml need to understand that this extra only works under uv.

### `querysource/outputs/dt/modin.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'Returns a Pandas Dataframe from a Resultset' querysource/outputs/dt/modin.py)
# REPLACE — replace the class docstring at line 8 (verified: querysource/outputs/dt/modin.py:8)
    """Returns a Pandas Dataframe from a Resultset.

    .. warning:: Best-effort on pandas >= 3

       modin <= 0.37.1 declares ``pandas < 2.4``.  This output format is
       available only when modin imports successfully; on pandas 3 it may
       raise at init time, which is surfaced as a ``QueryException``.
       Install with ``uv pip install querysource[modin]`` (pip cannot
       resolve the extra against pandas >= 3).
    """
```
**Why**: The class docstring is the first thing a developer sees. It should warn about the best-effort status so they don't waste time debugging resolver conflicts.

### FILL IN checklist
- [ ] `pyproject.toml` comment block — update the `[tool.uv]` override comment at line 327 similarly; bounded by AC-1
- [ ] Verify `pytest tests/unit/test_pandas3_modin_fallback.py` passes; bounded by AC-3

---

## Acceptance Criteria

- [x] AC-1: `pyproject.toml` `[modin]` extra has an inline comment stating best-effort status and the uv-override requirement.
- [x] AC-2: `querysource/outputs/dt/modin.py` module docstring mentions best-effort and the pandas 3 incompatibility.
- [ ] AC-3: All existing modin fallback tests pass (`pytest tests/unit/test_pandas3_modin_fallback.py`).
- [ ] AC-4: No functional regression — `ruff check` passes on the affected files.

---

## Validation Commands

- `pytest tests/unit/test_pandas3_modin_fallback.py -q`

---

## Test Specification

No new tests needed — existing `tests/unit/test_pandas3_modin_fallback.py`
covers all four import failure paths (operator missing, operator runtime,
modinFormat missing, modinFormat runtime).

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `dev`
2. **Read the spec** at the path listed above for full context
3. **Verify the Codebase Contract** — confirm anchors still exist
4. **Implement** — start from the Implementation Blueprint blocks
5. **Verify** all acceptance criteria are met — run the Validation Commands
6. **Commit the code** — stage only the files this task lists

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
