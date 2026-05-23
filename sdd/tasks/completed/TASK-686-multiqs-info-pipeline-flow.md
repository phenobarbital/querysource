# TASK-686: Remove Info Early-Return in MultiQS Pipeline

**Feature**: FEAT-098 — MultiQS Info EDA
**Spec**: `sdd/specs/multiqs-info-eda.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-685
**Assigned-to**: unassigned

---

## Context

The current `MultiQS.query()` method (lines 232-246 of `__init__.py`) early-returns when `Info` is used as an operator — it executes Info and immediately returns, skipping Steps 3-5 (Transform/Filter/Output/destinations). This prevents EDA DataFrames from flowing through the rest of the pipeline.

This task modifies the `MultiQS` dispatch to treat Info like Join/Concat — run the operator, pop it from options, and continue to downstream steps.

This implements **Spec Module 2**.

---

## Scope

- Modify the `if 'Info' in self._options:` block (lines 232-246) in `querysource/queries/multi/__init__.py` to:
  1. Pop `Info` from `self._options` (like Join/Concat do).
  2. Run the Info operator.
  3. Assign the result back to `result` (dict of DataFrames or JSON).
  4. **Do NOT return** — let execution flow into Step 3 (Transform/Filter) and beyond.
- Write integration tests verifying Info flows through Transform and Output steps.

**NOT in scope**:
- Rewriting the Info operator itself (TASK-685).
- Changes to other operators (Join, Concat, Melt, Merge).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/__init__.py` | MODIFY | Change Info dispatch block (lines 232-246) to not early-return |
| `tests/test_info_eda_integration.py` | CREATE | Integration tests for Info pipeline flow |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase.
> The implementing agent MUST use these exact imports, class names, and method signatures.

### Verified Imports

```python
# MultiQS class
from querysource.queries.multi import MultiQS  # verified: querysource/queries/multi/__init__.py:53

# Operator dispatch function
def get_operator_module(clsname: str):  # verified: querysource/queries/multi/__init__.py:21

# Exceptions
from querysource.exceptions import DriverError, QueryException, DataNotFound  # verified: querysource/exceptions.py
```

### Existing Signatures to Use

```python
# querysource/queries/multi/__init__.py — CURRENT Info dispatch (lines 232-246)
# THIS IS WHAT MUST BE CHANGED:
        if 'Info' in self._options:                    # line 232
            obj = get_operator_module('Info')           # line 233
            try:
                info = obj(data=result)                 # line 236
                async with info as i:                   # line 237
                    result = await i.run()              # line 238
                return result, self._options             # line 239 ← EARLY RETURN TO REMOVE
            except DataNotFound:                        # line 240
                raise
            except (QueryException, Exception) as ex:   # line 242
                raise self.Error(                       # line 243
                    message=f"Error making Info: {ex!s}",
                    exception=ex
                ) from ex

# REFERENCE PATTERN — how Join dispatches (lines 247-267):
        if 'Join' in self._options:                    # line 247
            obj = get_operator_module('Join')           # line 248
            try:
                _join = self._options.pop('Join', {})   # line 251 ← pops from options
                if isinstance(_join, dict):
                    join = obj(data=result, **_join)
                    async with join as j:
                        result = await j.run()          # result reassigned, no return
                elif isinstance(_join, list):
                    ...
            except ...

# REFERENCE PATTERN — how Concat dispatches (lines 268-280):
        elif 'Concat' in self._options:                # line 268
            obj = get_operator_module('Concat')         # line 269
            _concat = self._options.pop('Concat', {})   # line 270 ← pops from options
            try:
                concat = obj(data=result, **_concat)
                async with concat as c:
                    result = await c.run()              # result reassigned, no return
            except ...
```

### Does NOT Exist

- ~~`MultiQS._run_operator()`~~ — no generic operator dispatch method; each operator has inline dispatch
- ~~`MultiQS.info_result`~~ — no such attribute; results are stored in local `result` variable
- ~~`MultiQS._skip_info`~~ — no flag to skip Info early-return; the logic is inline

---

## Implementation Notes

### Pattern to Follow

Follow the Join/Concat dispatch pattern — pop from options, run operator, continue:

```python
# NEW Info dispatch — replaces lines 232-246
if 'Info' in self._options:
    obj = get_operator_module('Info')
    _info = self._options.pop('Info', {})
    try:
        info = obj(data=result, **_info)
        async with info as i:
            result = await i.run()
    except DataNotFound:
        raise
    except (QueryException, Exception) as ex:
        raise self.Error(
            message=f"Error making Info: {ex!s}",
            exception=ex
        ) from ex
# NO return here — execution continues to Join/Concat/Transform/Output
```

### Key Constraints

- The `if 'Info'` block must remain BEFORE the `if 'Join'` block (same position in the dispatch order).
- Pop `Info` from `self._options` so it doesn't get re-processed in Step 3 iteration.
- Pass `**_info` to the operator constructor so `output_format` and any other attributes reach the Info operator.
- When `output_format="json"`, Info returns a JSON dict (not DataFrame dict). Downstream Transform steps may fail on this — that's acceptable and documented in the spec's Known Risks. The default `"dataframe"` mode returns `dict[str, DataFrame]` which is compatible.
- After Info, the `if 'Join'` / `elif 'Concat'` blocks should still work. If Info is used WITH Join/Concat (unusual but possible), both operators run sequentially on the same result.

---

## Acceptance Criteria

- [ ] `MultiQS.query()` no longer early-returns when Info is in the pipeline
- [ ] Info options are popped from `self._options` before dispatch
- [ ] Info attributes (e.g., `output_format`) are passed to the operator constructor
- [ ] EDA DataFrames flow through Transform/Filter/Output steps when present
- [ ] Existing pipelines WITHOUT Info are unaffected
- [ ] Integration tests pass: `pytest tests/test_info_eda_integration.py -v`
- [ ] No linting errors: `ruff check querysource/queries/multi/__init__.py`

---

## Test Specification

```python
# tests/test_info_eda_integration.py
import pytest
import pandas as pd


class TestInfoPipelineFlow:
    @pytest.mark.asyncio
    async def test_info_no_early_return(self):
        """Info result flows to downstream steps instead of early-returning."""
        from querysource.queries.multi.operators.Info import Info

        data = {
            "src": pd.DataFrame({
                "x": [1, 2, 3],
                "y": ["a", "b", "c"],
            })
        }
        info = Info(data=data)
        async with info as i:
            result = await i.run()
        # Result should be dict of DataFrames (EDA format), not JSON
        assert isinstance(result, dict)
        assert "src" in result
        assert isinstance(result["src"], pd.DataFrame)

    @pytest.mark.asyncio
    async def test_info_with_json_output(self):
        """Info with output_format='json' returns dict, not DataFrames."""
        from querysource.queries.multi.operators.Info import Info

        data = {
            "src": pd.DataFrame({"x": [1, 2, 3]})
        }
        info = Info(data=data, output_format="json")
        async with info as i:
            result = await i.run()
        assert isinstance(result, (dict, str))

    @pytest.mark.asyncio
    async def test_info_options_popped(self):
        """Verify Info key is popped from options dict."""
        options = {"Info": {"output_format": "dataframe"}, "other": "value"}
        _info = options.pop("Info", {})
        assert "Info" not in options
        assert _info == {"output_format": "dataframe"}
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiqs-info-eda.spec.md` for full context
2. **Check dependencies** — TASK-685 must be completed first (Info operator rewrite)
3. **Verify the Codebase Contract** — confirm the line numbers in `__init__.py` still match
4. **Read `querysource/queries/multi/__init__.py`** lines 225-315 to understand the dispatch flow
5. **Implement** the change: replace the early-return block with the pop-and-continue pattern
6. **Run tests**: `source .venv/bin/activate && pytest tests/test_info_eda_integration.py -v`
7. **Run lint**: `source .venv/bin/activate && ruff check querysource/queries/multi/__init__.py`
8. **Verify** all acceptance criteria are met

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
